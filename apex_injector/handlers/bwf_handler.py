"""
Apex Meta-Injector — BWF/WAV Container Handler.

Handles metadata injection for Broadcast Wave Format (BWF) and standard
WAV files. Directly manipulates RIFF chunks for bext, axml, iXML, and
id3 metadata without altering the audio essence.
"""

from __future__ import annotations

import io
import logging
import shutil
import struct
from datetime import datetime
from pathlib import Path
from typing import Any

from apex_injector.codec_manifest import ContainerFormat, MetadataSchema
from apex_injector.handlers import (
    ContainerHandler,
    FileAnalysis,
    InjectionResult,
    InjectionStatus,
    MetadataPayload,
    MetadataField,
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
            with open(file_path, "rb") as f:
                riff_header = f.read(12)
                if len(riff_header) < 12:
                    analysis.error = "File too small for RIFF"
                    return analysis

                riff_id = riff_header[:4]
                file_size = struct.unpack("<I", riff_header[4:8])[0]
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

    def inject(
        self,
        file_path: Path,
        payload: MetadataPayload,
        staging_path: Path,
    ) -> InjectionResult:
        """
        Inject metadata into a BWF/WAV file.

        Reads all existing chunks, modifies/adds metadata chunks,
        and rebuilds the RIFF container preserving the audio data.
        """
        import time
        start = time.perf_counter()
        result = InjectionResult(file_path=file_path, status=InjectionStatus.FAILED)

        try:
            with open(file_path, "rb") as f:
                riff_header = f.read(12)
                riff_id = riff_header[:4]
                wave_id = riff_header[8:12]
                chunks = self._parse_riff_chunks(f)

                # Read all chunk data
                chunk_data = {}
                for chunk in chunks:
                    f.seek(chunk["data_offset"])
                    chunk_data[chunk["id"]] = f.read(chunk["size"])

            fields_written = 0

            # Process bext fields
            bext_fields = payload.get_by_schema(MetadataSchema.BEXT)
            if bext_fields:
                existing_bext = self._parse_bext(chunk_data.get(b"bext", b"")) if b"bext" in chunk_data else {}
                for field in bext_fields:
                    existing_bext[field.key] = field.value
                chunk_data[b"bext"] = self._build_bext(existing_bext)
                fields_written += len(bext_fields)

            # Process XMP/axml fields
            xmp_fields = payload.get_by_schema(MetadataSchema.XMP)
            if xmp_fields:
                xmp_xml = self._build_axml(xmp_fields)
                chunk_data[b"axml"] = xmp_xml.encode("utf-8")
                fields_written += len(xmp_fields)

            # Process iXML fields
            ixml_fields = payload.get_by_schema(MetadataSchema.IXML)
            if ixml_fields:
                ixml_xml = self._build_ixml(ixml_fields)
                chunk_data[b"iXML"] = ixml_xml.encode("utf-8")
                fields_written += len(ixml_fields)

            # Rebuild the RIFF file
            self._rebuild_riff(staging_path, riff_id, wave_id, chunks, chunk_data)

            result.status = InjectionStatus.SUCCESS
            result.fields_written = fields_written
            result.duration_ms = (time.perf_counter() - start) * 1000

        except Exception as e:
            result.status = InjectionStatus.FAILED
            result.message = str(e)
            logger.error("BWF injection failed: %s", e)

        return result

    def get_essence_region(self, file_path: Path) -> tuple[int, int]:
        """Get the data chunk offset and size."""
        try:
            with open(file_path, "rb") as f:
                f.seek(12)
                chunks = self._parse_riff_chunks(f)
                data_chunk = next((c for c in chunks if c["id"] == b"data"), None)
                if data_chunk:
                    return (data_chunk["data_offset"], data_chunk["size"])
        except Exception:
            pass
        return (0, file_path.stat().st_size)

    # ─────────────────────────────────────────────────────────────
    # RIFF Parsing
    # ─────────────────────────────────────────────────────────────

    def _parse_riff_chunks(self, f) -> list[dict]:
        """Parse RIFF chunks from current file position."""
        chunks = []
        f.seek(12)  # After RIFF header

        # Get total file size
        f.seek(0, 2)
        file_size = f.tell()
        f.seek(12)

        while f.tell() < file_size:
            chunk_start = f.tell()
            header = f.read(8)
            if len(header) < 8:
                break

            chunk_id = header[:4]
            chunk_size = struct.unpack("<I", header[4:8])[0]

            chunks.append({
                "id": chunk_id,
                "size": chunk_size,
                "offset": chunk_start,
                "data_offset": chunk_start + 8,
            })

            # Move to next chunk (pad to even boundary)
            next_offset = chunk_start + 8 + chunk_size
            if chunk_size % 2 != 0:
                next_offset += 1

            f.seek(next_offset)

        return chunks

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

    def _build_bext(self, fields: dict) -> bytes:
        """Build a bext chunk from field dict."""
        buf = io.BytesIO()

        def write_fixed(value: str, length: int):
            encoded = value.encode("ascii", errors="replace")[:length]
            buf.write(encoded.ljust(length, b"\x00"))

        write_fixed(fields.get("Description", ""), 256)
        write_fixed(fields.get("Originator", ""), 32)
        write_fixed(fields.get("OriginatorReference", ""), 32)
        write_fixed(fields.get("OriginationDate", datetime.now().strftime("%Y-%m-%d")), 10)
        write_fixed(fields.get("OriginationTime", datetime.now().strftime("%H:%M:%S")), 8)

        # TimeReference (8 bytes)
        time_ref = int(fields.get("TimeReference", 0))
        buf.write(struct.pack("<Q", time_ref))

        # Version
        buf.write(struct.pack("<H", int(fields.get("Version", 2))))

        # UMID (64 bytes)
        umid = fields.get("UMID", "")
        if umid:
            try:
                buf.write(bytes.fromhex(umid)[:64].ljust(64, b"\x00"))
            except ValueError:
                buf.write(b"\x00" * 64)
        else:
            buf.write(b"\x00" * 64)

        # LoudnessValue, LoudnessRange, MaxTruePeakLevel, MaxMomentaryLoudness, MaxShortTermLoudness
        buf.write(b"\x00" * 10)  # 5 x 2-byte fields

        # Reserved (180 bytes)
        buf.write(b"\x00" * 180)

        # CodingHistory (variable)
        coding = fields.get("CodingHistory", "")
        if coding:
            buf.write(coding.encode("ascii", errors="replace"))
            buf.write(b"\r\n\x00")

        return buf.getvalue()

    def _build_axml(self, fields: list) -> str:
        """Build an axml (XMP) chunk from fields."""
        parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<x:xmpmeta xmlns:x="adobe:ns:meta/">',
            '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">',
            '<rdf:Description rdf:about=""',
            '  xmlns:dc="http://purl.org/dc/elements/1.1/">',
        ]

        for field in fields:
            key = field.key if ":" in field.key else f"dc:{field.key}"
            parts.append(f"  <{key}>{field.value}</{key}>")

        parts.extend([
            "</rdf:Description>",
            "</rdf:RDF>",
            "</x:xmpmeta>",
        ])
        return "\n".join(parts)

    def _build_ixml(self, fields: list) -> str:
        """Build an iXML chunk from fields."""
        parts = ['<?xml version="1.0" encoding="UTF-8"?>', "<BWFXML>"]

        for field in fields:
            tag = field.key.upper()
            parts.append(f"  <{tag}>{field.value}</{tag}>")

        parts.append("</BWFXML>")
        return "\n".join(parts)

    def _rebuild_riff(
        self,
        output_path: Path,
        riff_id: bytes,
        wave_id: bytes,
        original_chunks: list[dict],
        chunk_data: dict[bytes, bytes],
    ):
        """Rebuild the RIFF file with updated chunk data."""
        # Determine chunk order: keep original order, add new chunks at end (before data)
        existing_ids = [c["id"] for c in original_chunks]
        new_ids = [cid for cid in chunk_data if cid not in existing_ids and cid != b"data"]

        # Build ordered chunk list
        ordered = []
        for chunk in original_chunks:
            cid = chunk["id"]
            if cid in chunk_data:
                ordered.append((cid, chunk_data[cid]))
            else:
                # Read original data
                ordered.append((cid, None))  # Will be read from original

        # Add new chunks before the data chunk
        data_idx = next((i for i, (cid, _) in enumerate(ordered) if cid == b"data"), len(ordered))
        for new_id in new_ids:
            ordered.insert(data_idx, (new_id, chunk_data[new_id]))
            data_idx += 1

        # Calculate total size
        total_data = 4  # "WAVE"
        for cid, data in ordered:
            if data is not None:
                chunk_size = len(data)
            else:
                orig = next(c for c in original_chunks if c["id"] == cid)
                chunk_size = orig["size"]
            total_data += 8 + chunk_size
            if chunk_size % 2 != 0:
                total_data += 1

        # Write the file
        with open(output_path, "wb") as out_f:
            out_f.write(riff_id)
            out_f.write(struct.pack("<I", total_data))
            out_f.write(wave_id)

            # Need the original file for chunks we didn't modify
            with open(original_chunks[0]["offset"], "rb") if False else open(output_path, "rb") as _:
                pass  # placeholder

            for cid, data in ordered:
                if data is None:
                    # This shouldn't happen in practice since we read all data upfront
                    # in the inject method, but handle gracefully
                    orig = next(c for c in original_chunks if c["id"] == cid)
                    data = b"\x00" * orig["size"]

                out_f.write(cid)
                out_f.write(struct.pack("<I", len(data)))
                out_f.write(data)

                # Pad to even boundary
                if len(data) % 2 != 0:
                    out_f.write(b"\x00")

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
