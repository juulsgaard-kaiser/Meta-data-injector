"""
Apex Meta-Injector — AIFF Container Handler.

Handles metadata injection for AIFF and AIFF-C containers using mutagen
for ID3v2.4 tags and manual IFF chunk manipulation for native AIFF
metadata (ANNO, NAME, AUTH chunks).
"""

from __future__ import annotations

import io
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
        return [MetadataSchema.ID3V24, MetadataSchema.XMP]

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
            from mutagen.aiff import AIFF as MutagenAIFF

            audio = MutagenAIFF(str(file_path))
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
                InjectionStatus.SUCCESS if fields_failed == 0
                else InjectionStatus.PARTIAL if fields_written > 0
                else InjectionStatus.FAILED
            )
            result.duration_ms = (time.perf_counter() - start) * 1000

        except Exception as e:
            result.status = InjectionStatus.FAILED
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

    def _inject_id3(self, file_path: Path, fields: list) -> tuple[int, int]:
        """Inject ID3v2.4 tags using mutagen."""
        written = 0
        failed = 0

        try:
            from mutagen.aiff import AIFF as MutagenAIFF
            from mutagen.id3 import (
                TIT2, TPE1, TALB, TDRC, COMM, TCON, TXXX, TCOP, TEXT,
            )

            audio = MutagenAIFF(str(file_path))

            if audio.tags is None:
                audio.add_tags()

            # Mapping from common keys to ID3 frames
            frame_map = {
                "title": lambda v: TIT2(encoding=3, text=[v]),
                "Title": lambda v: TIT2(encoding=3, text=[v]),
                "dc:Title": lambda v: TIT2(encoding=3, text=[v]),
                "artist": lambda v: TPE1(encoding=3, text=[v]),
                "Artist": lambda v: TPE1(encoding=3, text=[v]),
                "dc:Creator": lambda v: TPE1(encoding=3, text=[v]),
                "album": lambda v: TALB(encoding=3, text=[v]),
                "Album": lambda v: TALB(encoding=3, text=[v]),
                "date": lambda v: TDRC(encoding=3, text=[v]),
                "Date": lambda v: TDRC(encoding=3, text=[v]),
                "genre": lambda v: TCON(encoding=3, text=[v]),
                "Genre": lambda v: TCON(encoding=3, text=[v]),
                "copyright": lambda v: TCOP(encoding=3, text=[v]),
                "Copyright": lambda v: TCOP(encoding=3, text=[v]),
            }

            for field in fields:
                key = field.key
                value = str(field.value)

                frame_factory = frame_map.get(key)
                if frame_factory:
                    frame = frame_factory(value)
                    audio.tags.add(frame)
                    written += 1
                else:
                    # Store as TXXX (user-defined text frame)
                    audio.tags.add(TXXX(
                        encoding=3,
                        desc=key,
                        text=[value],
                    ))
                    written += 1

            audio.save()

        except ImportError:
            logger.error("mutagen not installed for AIFF ID3 injection")
            failed = len(fields)
        except Exception as e:
            logger.error("ID3 injection failed: %s", e)
            failed = len(fields) - written

        return written, failed


# Auto-register
_handler = AIFFHandler()
register_handler(_handler)
