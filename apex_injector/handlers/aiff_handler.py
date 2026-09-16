"""
Apex Meta-Injector — AIFF Container Handler.

Handles metadata injection for AIFF and AIFF-C containers using mutagen
for ID3v2.4 tags and manual IFF chunk manipulation for native AIFF
metadata (ANNO, NAME, AUTH chunks).
"""

from __future__ import annotations

import logging
import shutil
import struct
from pathlib import Path
from typing import Any

from apex_injector.codec_manifest import ContainerFormat, MetadataSchema
from apex_injector.handlers import (
    ContainerHandler,
    FileAnalysis,
    InjectionResult,
    InjectionStatus,
    MetadataPayload,
    register_handler,
)

logger = logging.getLogger(__name__)

# AIFF native chunk tag mapping
AIFF_CHUNK_MAP = {
    "title": b"NAME",
    "Title": b"NAME",
    "name": b"NAME",
    "author": b"AUTH",
    "Author": b"AUTH",
    "artist": b"AUTH",
    "Artist": b"AUTH",
    "annotation": b"ANNO",
    "Annotation": b"ANNO",
    "comment": b"ANNO",
    "Comment": b"ANNO",
    "copyright": b"(c) ",
    "Copyright": b"(c) ",
}


class AIFFHandler(ContainerHandler):
    """
    Handler for AIFF and AIFF-C (compressed AIFF) containers.

    Supports:
    - ID3v2.4 tags via mutagen
    - Native AIFF chunks: NAME, AUTH, ANNO, (c)
    - XMP metadata via ID3v2 TXXX frames
    """

    @property
    def supported_containers(self) -> list[ContainerFormat]:
        return [ContainerFormat.AIFF]

    @property
    def supported_schemas(self) -> list[MetadataSchema]:
        return [MetadataSchema.ID3V24]

    def analyze(self, file_path: Path) -> FileAnalysis:
        """Analyze an AIFF file."""
        analysis = FileAnalysis(
            file_path=file_path,
            file_size=file_path.stat().st_size,
            container=ContainerFormat.AIFF,
        )

        try:
            with open(file_path, "rb") as f:
                header = f.read(12)
                if len(header) < 12:
                    analysis.error = "File too small"
                    return analysis

                form_id = header[:4]
                form_type = header[8:12]

                if form_id != b"FORM" or form_type not in (b"AIFF", b"AIFC"):
                    analysis.error = "Not a valid AIFF file"
                    return analysis

                analysis.codec = "PCM (AIFF-C)" if form_type == b"AIFC" else "PCM (AIFF)"
                analysis.injectable = True
                analysis.complex_wrap_risk = "none"

            analysis.current_metadata = self.read_metadata(file_path)

        except Exception as e:
            analysis.error = str(e)

        return analysis

    def read_metadata(self, file_path: Path) -> dict[str, dict[str, Any]]:
        """Read metadata from an AIFF file."""
        metadata: dict[str, dict[str, Any]] = {}

        # Read native AIFF chunks
        try:
            native = self._read_native_chunks(file_path)
            if native:
                metadata["id3v2.4"] = native
        except Exception as e:
            logger.debug("Failed to read native AIFF chunks: %s", e)

        # Read ID3v2 tags via mutagen
        try:
            from mutagen.aiff import AIFF

            audio = AIFF(str(file_path))
            if audio.tags:
                id3_data = {}
                for key, value in audio.tags.items():
                    if hasattr(value, "text"):
                        id3_data[key] = value.text[0] if len(value.text) == 1 else list(value.text)
                    elif hasattr(value, "url"):
                        id3_data[key] = value.url
                if id3_data:
                    metadata.setdefault("id3v2.4", {}).update(id3_data)
        except ImportError:
            logger.debug("mutagen not available for AIFF ID3 reading")
        except Exception as e:
            logger.debug("Failed to read AIFF ID3 tags: %s", e)

        return metadata

    def inject(
        self,
        file_path: Path,
        payload: MetadataPayload,
        staging_path: Path,
    ) -> InjectionResult:
        """
        Inject metadata into an AIFF file.

        Uses mutagen for ID3v2.4 tag injection and manual chunk
        manipulation for native AIFF metadata.
        """
        import time

        start = time.perf_counter()
        result = InjectionResult(file_path=file_path, status=InjectionStatus.FAILED)

        if not payload.fields or any(f.schema not in self.supported_schemas for f in payload.fields):
            result.message = "AIFF supports ID3 metadata only"
            result.fields_failed = len(payload.fields)
            return result

        # Copy to staging first
        shutil.copy2(str(file_path), str(staging_path))

        fields_written = 0
        fields_failed = 0

        try:
            # Inject ID3v2.4 tags via mutagen
            id3_fields = payload.get_by_schema(MetadataSchema.ID3V24)
            xmp_fields = payload.get_by_schema(MetadataSchema.XMP)
            all_id3 = id3_fields + xmp_fields

            if all_id3:
                written, failed = self._inject_id3(staging_path, all_id3)
                fields_written += written
                fields_failed += failed

            result.fields_written = fields_written
            result.fields_failed = fields_failed
            result.status = (
                InjectionStatus.SUCCESS
                if fields_failed == 0
                else InjectionStatus.PARTIAL
                if fields_written > 0
                else InjectionStatus.FAILED
            )
            result.duration_ms = (time.perf_counter() - start) * 1000

        except Exception as e:
            result.status = InjectionStatus.FAILED
            result.fields_written = 0
            result.fields_failed = len(payload.fields)
            result.message = str(e)
            logger.error("AIFF injection failed: %s", e)

        return result

    def get_essence_region(self, file_path: Path) -> tuple[int, int]:
        """Get the SSND chunk region for bitstream verification."""
        try:
            with open(file_path, "rb") as f:
                f.seek(12)
                while True:
                    header = f.read(8)
                    if len(header) < 8:
                        break
                    chunk_id = header[:4]
                    chunk_size = struct.unpack(">I", header[4:8])[0]

                    if chunk_id == b"SSND":
                        return (f.tell(), chunk_size)

                    # Skip to next chunk
                    f.seek(chunk_size, 1)
                    if chunk_size % 2 != 0:
                        f.seek(1, 1)
        except Exception:
            pass
        return (0, file_path.stat().st_size)

    def _read_native_chunks(self, file_path: Path) -> dict[str, str]:
        """Read native AIFF metadata chunks (NAME, AUTH, ANNO)."""
        metadata = {}
        chunk_to_key = {b"NAME": "title", b"AUTH": "artist", b"ANNO": "annotation", b"(c) ": "copyright"}

        try:
            with open(file_path, "rb") as f:
                f.seek(12)  # Skip FORM header
                file_size = file_path.stat().st_size

                while f.tell() < file_size:
                    header = f.read(8)
                    if len(header) < 8:
                        break

                    chunk_id = header[:4]
                    chunk_size = struct.unpack(">I", header[4:8])[0]

                    key = chunk_to_key.get(chunk_id)
                    if key:
                        data = f.read(chunk_size)
                        metadata[key] = data.split(b"\x00")[0].decode("utf-8", errors="replace")
                    else:
                        f.seek(chunk_size, 1)

                    if chunk_size % 2 != 0:
                        f.seek(1, 1)

        except Exception as e:
            logger.debug("Native AIFF chunk read failed: %s", e)

        return metadata

    def _inject_id3(self, file_path, fields):
        from mutagen.aiff import AIFF

        from apex_injector.handlers.id3_metadata import update_tags, verify_tags

        audio = AIFF(file_path)
        if audio.tags is None:
            audio.add_tags()
        update_tags(audio.tags, fields)
        audio.save()
        verify_tags(AIFF(file_path).tags, fields)
        return len(fields), 0

    def validate(self, path):
        from mutagen.aiff import AIFF

        AIFF(path)
        self.get_essence_regions(path)

    def get_essence_regions(self, path):
        regions = []
        size = path.stat().st_size
        with open(path, "rb") as f:
            header = f.read(12)
            if len(header) != 12 or header[:4] != b"FORM" or header[8:] not in (b"AIFF", b"AIFC"):
                raise ValueError("Invalid AIFF header")
            if int.from_bytes(header[4:8], "big") + 8 != size:
                raise ValueError("AIFF size mismatch")
            offset = 12
            while offset < size:
                f.seek(offset)
                chunk = f.read(8)
                if len(chunk) != 8:
                    raise ValueError("Truncated AIFF chunk")
                length = int.from_bytes(chunk[4:], "big")
                end = offset + 8 + length + length % 2
                if end > size:
                    raise ValueError("AIFF chunk exceeds file length")
                if chunk[:4] in (b"SSND", b"COMM"):
                    regions.append((offset + 8, length))
                offset = end
        if len(regions) < 2:
            raise ValueError("AIFF requires sound and format chunks")
        return regions

    def essence_fingerprint(self, path):
        from apex_injector.win32_io import hash_regions

        return hash_regions(path, self.get_essence_regions(path))


# Auto-register
_handler = AIFFHandler()
register_handler(_handler)
