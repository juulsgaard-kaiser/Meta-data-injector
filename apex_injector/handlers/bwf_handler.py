"""
Apex Meta-Injector — BWF/WAV Container Handler.

Handles metadata injection for Broadcast Wave Format (BWF) and standard
WAV files. Directly manipulates RIFF chunks for bext, axml, iXML, and
id3 metadata without altering the audio essence.
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path
from typing import Any

from apex_injector.codec_manifest import ContainerFormat, MetadataSchema
from apex_injector.handlers import (
    ContainerHandler,
    FileAnalysis,
    InjectionResult,
    InjectionStatus,
    register_handler,
)

logger = logging.getLogger(__name__)


class BWFHandler(ContainerHandler):
    """
    Handler for BWF (Broadcast Wave Format) and standard WAV containers.

    Directly reads and writes RIFF chunks:
    - bext: Broadcast Extension (EBU Tech 3285)
    - axml: XML metadata (EBU Tech 3285 supplement)
    - iXML: iXML production metadata
    - id3:  ID3v2 tags
    - LIST/INFO: Standard RIFF INFO tags
    """

    @property
    def supported_containers(self) -> list[ContainerFormat]:
        return [ContainerFormat.WAV, ContainerFormat.BWF]

    @property
    def supported_schemas(self) -> list[MetadataSchema]:
        return [MetadataSchema.BEXT, MetadataSchema.IXML, MetadataSchema.XMP, MetadataSchema.ID3V24]

    def analyze(self, file_path: Path) -> FileAnalysis:
        """Analyze a WAV/BWF file."""
        analysis = FileAnalysis(
            file_path=file_path,
            file_size=file_path.stat().st_size,
        )

        try:
            self.validate(file_path)
            with open(file_path, "rb") as f:
                riff_header = f.read(12)
                if len(riff_header) < 12:
                    analysis.error = "File too small for RIFF"
                    return analysis

                riff_id = riff_header[:4]
                wave_id = riff_header[8:12]

                if riff_id == b"RF64":
                    analysis.container = ContainerFormat.BWF
                    analysis.codec = "PCM (RF64)"
                elif riff_id == b"RIFF" and wave_id == b"WAVE":
                    # Check for bext chunk to classify as BWF
                    chunks = self._parse_riff_chunks(f)
                    has_bext = any(c["id"] == b"bext" for c in chunks)
                    analysis.container = ContainerFormat.BWF if has_bext else ContainerFormat.WAV
                    analysis.codec = "PCM"
                else:
                    analysis.error = "Not a valid WAV/BWF file"
                    return analysis

                analysis.injectable = True
                analysis.complex_wrap_risk = "none"
                analysis.current_metadata = self.read_metadata(file_path)

        except Exception as e:
            analysis.error = str(e)

        return analysis

    def read_metadata(self, file_path: Path) -> dict[str, dict[str, Any]]:
        """Read all metadata chunks from a BWF/WAV file."""
        metadata: dict[str, dict[str, Any]] = {}

        try:
            with open(file_path, "rb") as f:
                f.seek(12)  # Skip RIFF header
                chunks = self._parse_riff_chunks(f)

                for chunk in chunks:
                    if chunk["id"] == b"bext":
                        f.seek(chunk["data_offset"])
                        bext_data = f.read(chunk["size"])
                        metadata["bext"] = self._parse_bext(bext_data)

                    elif chunk["id"] == b"axml":
                        f.seek(chunk["data_offset"])
                        xml_data = f.read(chunk["size"])
                        metadata["xmp"] = {"raw_xml": xml_data.decode("utf-8", errors="replace")}

                    elif chunk["id"] == b"iXML":
                        f.seek(chunk["data_offset"])
                        ixml_data = f.read(chunk["size"])
                        metadata["ixml"] = {"raw_ixml": ixml_data.decode("utf-8", errors="replace")}

                    elif chunk["id"] == b"LIST":
                        f.seek(chunk["data_offset"])
                        list_type = f.read(4)
                        if list_type == b"INFO":
                            info = self._parse_list_info(f, chunk["data_offset"] + 4, chunk["size"] - 4)
                            metadata.setdefault("bext", {}).update(info)

        except Exception as e:
            logger.error("Failed to read BWF metadata: %s", e)

        return metadata

    def _chunks(self, path):
        with open(path, "rb") as f:
            header = f.read(12)
            if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
                raise ValueError("Only RIFF/WAVE is writable; RF64 requires a dedicated implementation")
            if struct.unpack("<I", header[4:8])[0] + 8 != path.stat().st_size:
                raise ValueError("RIFF size does not match file length")
            return self._parse_riff_chunks(f)

    def validate(self, path):
        chunks = self._chunks(path)
        if sum(c["id"] == b"fmt " for c in chunks) != 1 or not any(c["id"] == b"data" for c in chunks):
            raise ValueError("WAV requires one fmt chunk and audio data")
        if any(c["size"] < 16 for c in chunks if c["id"] == b"fmt "):
            raise ValueError("Truncated WAV format")

    def get_essence_regions(self, path):
        return [(c["data_offset"], c["size"]) for c in self._chunks(path) if c["id"] in (b"data", b"fmt ")]

    def get_essence_region(self, path):
        return next((c["data_offset"], c["size"]) for c in self._chunks(path) if c["id"] == b"data")

    def essence_fingerprint(self, path):
        from apex_injector.win32_io import hash_regions

        return hash_regions(path, self.get_essence_regions(path))

    def _parse_riff_chunks(self, f):
        chunks = []
        f.seek(0, 2)
        end = f.tell()
        offset = 12
        while offset < end:
            f.seek(offset)
            header = f.read(8)
            if len(header) != 8:
                raise ValueError("Truncated RIFF chunk")
            size = struct.unpack("<I", header[4:])[0]
            following = offset + 8 + size + size % 2
            if following > end:
                raise ValueError("RIFF chunk exceeds file length")
            chunks.append(dict(id=header[:4], offset=offset, data_offset=offset + 8, size=size))
            if len(chunks) > 100000:
                raise ValueError("RIFF chunk count limit exceeded")
            offset = following
        return chunks

    def inject(self, file_path, payload, staging_path):
        from xml.etree import ElementTree as ET

        from mutagen.wave import WAVE

        from apex_injector.handlers.id3_metadata import update_tags, verify_tags
        from apex_injector.handlers.xml_metadata import merge_xmp, parse_xml

        result = InjectionResult(file_path=file_path, status=InjectionStatus.FAILED)
        try:
            if not payload.fields or any(f.schema not in self.supported_schemas for f in payload.fields):
                raise ValueError("Unsupported WAV schema")
            self.validate(file_path)
            chunks = self._chunks(file_path)
            replacements = {}

            def existing(cid):
                matches = [c for c in chunks if c["id"] == cid]
                if len(matches) > 1:
                    raise ValueError(f"Ambiguous duplicate metadata chunks: {cid!r}")
                if not matches:
                    return None
                c = matches[0]
                if c["size"] > 16 * 1024 * 1024:
                    raise ValueError("Metadata chunk exceeds 16 MiB limit")
                with open(file_path, "rb") as f:
                    f.seek(c["data_offset"])
                    return f.read(c["size"])

            bext = payload.get_by_schema(MetadataSchema.BEXT)
            if bext:
                replacements[b"bext"] = self._update_bext(existing(b"bext"), bext)
            xmp = payload.get_by_schema(MetadataSchema.XMP)
            if xmp:
                replacements[b"axml"] = merge_xmp(existing(b"axml"), xmp)
            ixml = payload.get_by_schema(MetadataSchema.IXML)
            if ixml:
                import re

                previous = existing(b"iXML")
                root = parse_xml(previous) if previous else ET.Element("BWFXML")
                for field in ixml:
                    key = field.key.upper()
                    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key):
                        raise ValueError(f"Invalid iXML field: {key}")
                    for node in list(root):
                        if node.tag == key:
                            root.remove(node)
                    ET.SubElement(root, key).text = str(field.value)
                replacements[b"iXML"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            with open(file_path, "rb") as src, open(staging_path, "w+b") as out:
                out.write(b"RIFF\0\0\0\0WAVE")
                for chunk in chunks:
                    cid = chunk["id"]
                    if cid in replacements:
                        data = replacements.pop(cid)
                        self._write_chunk(out, cid, data)
                    else:
                        src.seek(chunk["offset"])
                        remaining = 8 + chunk["size"] + chunk["size"] % 2
                        while remaining:
                            data = src.read(min(8 * 1024 * 1024, remaining))
                            if not data:
                                raise ValueError("Truncated source chunk")
                            out.write(data)
                            remaining -= len(data)
                # Appending avoids relocating audio for new metadata.
                for cid, data in replacements.items():
                    self._write_chunk(out, cid, data)
                size = out.tell() - 8
                if size > 0xFFFFFFFF:
                    raise ValueError("RIFF exceeds 4 GiB; RF64 conversion is not supported")
                out.seek(4)
                out.write(struct.pack("<I", size))
            id3 = payload.get_by_schema(MetadataSchema.ID3V24)
            if id3:
                audio = WAVE(staging_path)
                if audio.tags is None:
                    audio.add_tags()
                update_tags(audio.tags, id3)
                audio.save()
                verify_tags(WAVE(staging_path).tags, id3)
            self.validate(staging_path)
            result.status = InjectionStatus.SUCCESS
            result.fields_written = len(payload.fields)
        except Exception as e:
            result.message = str(e)
            result.fields_failed = len(payload.fields)
        return result

    @staticmethod
    def _write_chunk(out, cid, data):
        out.write(cid + struct.pack("<I", len(data)) + data)
        if len(data) % 2:
            out.write(b"\0")

    def _update_bext(self, original, fields):
        if original is not None and len(original) < 602:
            raise ValueError("Truncated bext chunk")
        data = bytearray(original if original is not None else bytes(602))
        text = {
            "Description": (0, 256),
            "Originator": (256, 32),
            "OriginatorReference": (288, 32),
            "OriginationDate": (320, 10),
            "OriginationTime": (330, 8),
        }
        numeric = {
            "TimeReference": (338, "<Q"),
            "Version": (346, "<H"),
            "LoudnessValue": (412, "<h"),
            "LoudnessRange": (414, "<h"),
            "MaxTruePeakLevel": (416, "<h"),
            "MaxMomentaryLoudness": (418, "<h"),
            "MaxShortTermLoudness": (420, "<h"),
        }
        keys = {field.key for field in fields}
        previous_version = struct.unpack_from("<H", data, 346)[0]
        minimum_version = (
            2 if keys.intersection(set(numeric) - {"Version", "TimeReference"}) else 1 if "UMID" in keys else 0
        )
        requested_version = next((int(f.value) for f in fields if f.key == "Version"), None)
        version = max(previous_version, minimum_version) if requested_version is None else requested_version
        if version not in (0, 1, 2) or version < minimum_version:
            raise ValueError("BEXT version must be 0, 1 or 2; UMID needs version 1 and loudness needs version 2")
        if previous_version < 2 <= version:
            # EBU Tech 3285: unmeasured loudness fields use 0x7fff, not zero.
            data[412:422] = struct.pack("<5h", *([32767] * 5))
        struct.pack_into("<H", data, 346, version)
        for field in fields:
            if field.key in text:
                offset, size = text[field.key]
                encoded = str(field.value).encode("ascii")
                if len(encoded) > size:
                    raise ValueError(f"{field.key} exceeds {size} ASCII bytes")
                data[offset : offset + size] = encoded.ljust(size, b"\0")
            elif field.key in numeric:
                offset, fmt = numeric[field.key]
                number = int(field.value)
                if isinstance(field.value, float) and field.value != number:
                    raise ValueError(f"{field.key} requires an integer; loudness uses hundredths of a unit")
                packed = struct.pack(fmt, number)
                data[offset : offset + len(packed)] = packed
            elif field.key == "UMID":
                umid = bytes.fromhex(str(field.value))
                if len(umid) != 64:
                    raise ValueError("UMID must be 64 bytes")
                data[348:412] = umid
            elif field.key == "CodingHistory":
                data[602:] = str(field.value).encode("ascii")
            else:
                raise ValueError(f"Unsupported bext field: {field.key}")
        return bytes(data)

    def _parse_bext(self, data: bytes) -> dict[str, Any]:
        """Parse a bext (Broadcast Extension) chunk."""
        if len(data) < 602:
            return {}

        bext = {
            "Description": data[0:256].split(b"\x00")[0].decode("ascii", errors="replace").strip(),
            "Originator": data[256:288].split(b"\x00")[0].decode("ascii", errors="replace").strip(),
            "OriginatorReference": data[288:320].split(b"\x00")[0].decode("ascii", errors="replace").strip(),
            "OriginationDate": data[320:330].split(b"\x00")[0].decode("ascii", errors="replace").strip(),
            "OriginationTime": data[330:338].split(b"\x00")[0].decode("ascii", errors="replace").strip(),
            "TimeReference": struct.unpack("<Q", data[338:346])[0],
            "Version": struct.unpack("<H", data[346:348])[0],
        }

        # UMID (64 bytes at offset 348)
        if len(data) >= 412:
            bext["UMID"] = data[348:412].hex()

        # Coding history (variable length after fixed fields)
        if len(data) > 602:
            bext["CodingHistory"] = data[602:].split(b"\x00")[0].decode("ascii", errors="replace").strip()

        return {k: v for k, v in bext.items() if v}

    def _parse_list_info(self, f, offset: int, size: int) -> dict[str, str]:
        """Parse LIST INFO sub-chunks."""
        info = {}
        pos = offset
        end = offset + size

        while pos < end:
            f.seek(pos)
            header = f.read(8)
            if len(header) < 8:
                break

            chunk_id = header[:4].decode("ascii", errors="replace")
            chunk_size = struct.unpack("<I", header[4:8])[0]

            data = f.read(chunk_size)
            info[chunk_id] = data.split(b"\x00")[0].decode("utf-8", errors="replace")

            pos += 8 + chunk_size
            if chunk_size % 2 != 0:
                pos += 1

        return info


# Auto-register
_handler = BWFHandler()
register_handler(_handler)
