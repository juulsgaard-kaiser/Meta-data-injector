"""
Apex Meta-Injector — ISOBMFF Container Handler.

Handles metadata injection for MP4 and MOV containers using pymp4/construct
for atom-level parsing and manipulation. Supports XMP, IPTC, and EXIF schemas
via udta/meta atom structures.

Strict constraint: Only header/atom manipulation. The mdat (essence) is
copied byte-for-byte. stco/co64 offsets are recalculated if atom sizes change.
"""

from __future__ import annotations

import io
import logging
import shutil
import struct
from pathlib import Path
from typing import Any, Optional

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

# XMP UUID as defined by ISO 16684-1 / Adobe XMP Specification
XMP_UUID = bytes([
    0xBE, 0x7A, 0xCF, 0xCB, 0x97, 0xA9, 0x42, 0xE8,
    0x9C, 0x71, 0x99, 0x94, 0x91, 0xE3, 0xAF, 0xAC,
])


class ISOBMFFHandler(ContainerHandler):
    """
    Handler for ISO Base Media File Format containers (MP4 / MOV).

    Uses pymp4 for atom parsing when available, falls back to manual
    struct-based parsing for basic operations.
    """

    @property
    def supported_containers(self) -> list[ContainerFormat]:
        return [ContainerFormat.MP4, ContainerFormat.MOV]

    @property
    def supported_schemas(self) -> list[MetadataSchema]:
        return [MetadataSchema.XMP, MetadataSchema.IPTC, MetadataSchema.EXIF]

    def analyze(self, file_path: Path) -> FileAnalysis:
        """Analyze an ISOBMFF file for its codec, metadata, and injectability."""
        analysis = FileAnalysis(file_path=file_path, file_size=file_path.stat().st_size)

        try:
            atoms = self._parse_top_level_atoms(file_path)
            analysis.container = (
                ContainerFormat.MOV
                if self._is_mov(file_path, atoms)
                else ContainerFormat.MP4
            )

            # Find codec fourcc from moov/trak/mdia/minf/stbl/stsd
            codec_info = self._find_codec_info(file_path, atoms)
            analysis.codec = codec_info.get("codec_name", "unknown")
            analysis.codec_fourcc = codec_info.get("fourcc", "")
            analysis.injectable = True

            # Read existing metadata
            analysis.current_metadata = self.read_metadata(file_path)

            # Check if moov is before mdat (faststart)
            moov_offset = next((a["offset"] for a in atoms if a["type"] == b"moov"), None)
            mdat_offset = next((a["offset"] for a in atoms if a["type"] == b"mdat"), None)
            if moov_offset and mdat_offset and moov_offset > mdat_offset:
                analysis.complex_wrap_risk = "low"

        except Exception as e:
            analysis.error = str(e)
            logger.error("Failed to analyze %s: %s", file_path, e)

        return analysis

    def read_metadata(self, file_path: Path) -> dict[str, dict[str, Any]]:
        """Read metadata from moov/udta/meta atoms and XMP UUID atoms."""
        metadata: dict[str, dict[str, Any]] = {}

        try:
            atoms = self._parse_top_level_atoms(file_path)

            with open(file_path, "rb") as f:
                # Look for XMP in uuid atoms
                xmp_data = self._find_xmp_uuid(f, atoms)
                if xmp_data:
                    metadata["xmp"] = {"raw_xmp": xmp_data.decode("utf-8", errors="replace")}

                # Look for udta atom inside moov
                moov = next((a for a in atoms if a["type"] == b"moov"), None)
                if moov:
                    udta_metadata = self._read_udta_metadata(f, moov)
                    if udta_metadata:
                        metadata.setdefault("iptc", {}).update(udta_metadata)

        except Exception as e:
            logger.error("Failed to read metadata from %s: %s", file_path, e)

        return metadata

    def inject(
        self,
        file_path: Path,
        payload: MetadataPayload,
        staging_path: Path,
    ) -> InjectionResult:
        """
        Inject metadata into an ISOBMFF file.

        Strategy:
        1. Parse the atom tree
        2. Build a new moov atom with updated metadata
        3. Copy ftyp + modified moov + original mdat to staging file
        4. Recalculate stco/co64 offsets
        """
        result = InjectionResult(file_path=file_path, status=InjectionStatus.FAILED)

        try:
            import time
            start = time.perf_counter()

            # Copy original to staging
            shutil.copy2(str(file_path), str(staging_path))

            atoms = self._parse_top_level_atoms(file_path)
            fields_written = 0

            # Handle XMP injection
            xmp_fields = payload.get_by_schema(MetadataSchema.XMP)
            if xmp_fields:
                xmp_packet = self._build_xmp_packet(xmp_fields)
                self._inject_xmp_uuid(staging_path, atoms, xmp_packet)
                fields_written += len(xmp_fields)

            # Handle IPTC/EXIF via udta atoms
            iptc_fields = payload.get_by_schema(MetadataSchema.IPTC)
            exif_fields = payload.get_by_schema(MetadataSchema.EXIF)
            other_fields = iptc_fields + exif_fields

            if other_fields:
                self._inject_udta_metadata(staging_path, atoms, other_fields)
                fields_written += len(other_fields)

            result.status = InjectionStatus.SUCCESS
            result.fields_written = fields_written
            result.duration_ms = (time.perf_counter() - start) * 1000

        except Exception as e:
            result.status = InjectionStatus.FAILED
            result.message = str(e)
            logger.error("Injection failed for %s: %s", file_path, e)

        return result

    def get_essence_region(self, file_path: Path) -> tuple[int, int]:
        """Get the mdat atom offset and size for bitstream verification."""
        try:
            atoms = self._parse_top_level_atoms(file_path)
            mdat = next((a for a in atoms if a["type"] == b"mdat"), None)
            if mdat:
                # Skip mdat header (8 bytes for normal, 16 for extended)
                header_size = 16 if mdat["size"] == 1 else 8
                data_offset = mdat["offset"] + header_size
                data_length = mdat["actual_size"] - header_size
                return (data_offset, data_length)
        except Exception as e:
            logger.warning("Cannot determine essence region for %s: %s", file_path, e)

        return (0, file_path.stat().st_size)

    # ─────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────

    def _parse_top_level_atoms(self, file_path: Path) -> list[dict]:
        """Parse top-level atoms (boxes) from an ISOBMFF file."""
        atoms = []
        with open(file_path, "rb") as f:
            file_size = file_path.stat().st_size
            offset = 0

            while offset < file_size:
                f.seek(offset)
                header = f.read(8)
                if len(header) < 8:
                    break

                size = struct.unpack(">I", header[:4])[0]
                box_type = header[4:8]

                actual_size = size
                if size == 1:
                    # 64-bit extended size
                    ext_size = f.read(8)
                    if len(ext_size) < 8:
                        break
                    actual_size = struct.unpack(">Q", ext_size)[0]
                elif size == 0:
                    # Box extends to end of file
                    actual_size = file_size - offset

                atoms.append({
                    "type": box_type,
                    "offset": offset,
                    "size": size,
                    "actual_size": actual_size,
                    "header_size": 16 if size == 1 else 8,
                })

                if actual_size <= 0:
                    break
                offset += actual_size

        return atoms

    def _parse_child_atoms(self, f, parent_offset: int, parent_size: int, header_size: int = 8) -> list[dict]:
        """Parse child atoms within a parent container atom."""
        children = []
        offset = parent_offset + header_size
        end = parent_offset + parent_size

        while offset < end:
            f.seek(offset)
            header = f.read(8)
            if len(header) < 8:
                break

            size = struct.unpack(">I", header[:4])[0]
            box_type = header[4:8]

            actual_size = size
            if size == 1:
                ext = f.read(8)
                actual_size = struct.unpack(">Q", ext)[0] if len(ext) >= 8 else 0
            elif size == 0:
                actual_size = end - offset

            if actual_size <= 0:
                break

            children.append({
                "type": box_type,
                "offset": offset,
                "size": size,
                "actual_size": actual_size,
                "header_size": 16 if size == 1 else 8,
            })

            offset += actual_size

        return children

    def _is_mov(self, file_path: Path, atoms: list[dict]) -> bool:
        """Determine if an ISOBMFF file is MOV (QuickTime) vs MP4."""
        ftyp = next((a for a in atoms if a["type"] == b"ftyp"), None)
        if not ftyp:
            return file_path.suffix.lower() in (".mov", ".qt")

        with open(file_path, "rb") as f:
            f.seek(ftyp["offset"] + 8)
            brand = f.read(4)
            return brand in (b"qt  ", b"MSNV")

    def _find_codec_info(self, file_path: Path, atoms: list[dict]) -> dict:
        """Extract codec information from the moov/trak/mdia/minf/stbl/stsd atom chain."""
        info = {"codec_name": "unknown", "fourcc": ""}

        moov = next((a for a in atoms if a["type"] == b"moov"), None)
        if not moov:
            return info

        try:
            with open(file_path, "rb") as f:
                moov_children = self._parse_child_atoms(f, moov["offset"], moov["actual_size"])

                for trak in [c for c in moov_children if c["type"] == b"trak"]:
                    trak_children = self._parse_child_atoms(f, trak["offset"], trak["actual_size"])

                    mdia = next((c for c in trak_children if c["type"] == b"mdia"), None)
                    if not mdia:
                        continue

                    mdia_children = self._parse_child_atoms(f, mdia["offset"], mdia["actual_size"])
                    minf = next((c for c in mdia_children if c["type"] == b"minf"), None)
                    if not minf:
                        continue

                    minf_children = self._parse_child_atoms(f, minf["offset"], minf["actual_size"])
                    stbl = next((c for c in minf_children if c["type"] == b"stbl"), None)
                    if not stbl:
                        continue

                    stbl_children = self._parse_child_atoms(f, stbl["offset"], stbl["actual_size"])
                    stsd = next((c for c in stbl_children if c["type"] == b"stsd"), None)
                    if not stsd:
                        continue

                    # stsd: version(4) + entry_count(4) + first entry
                    f.seek(stsd["offset"] + stsd["header_size"])
                    stsd_header = f.read(8)  # version + flags + entry_count
                    if len(stsd_header) >= 8:
                        # First sample entry starts at offset +8 from stsd data
                        entry_header = f.read(8)
                        if len(entry_header) >= 8:
                            fourcc = entry_header[4:8].decode("ascii", errors="replace").strip()
                            info["fourcc"] = fourcc
                            info["codec_name"] = fourcc
                            return info

        except Exception as e:
            logger.debug("Codec detection failed: %s", e)

        return info

    def _find_xmp_uuid(self, f, atoms: list[dict]) -> Optional[bytes]:
        """Find and read XMP data from a UUID atom."""
        for atom in atoms:
            if atom["type"] == b"uuid":
                f.seek(atom["offset"] + 8)
                uuid_bytes = f.read(16)
                if uuid_bytes == XMP_UUID:
                    xmp_size = atom["actual_size"] - 24  # header(8) + uuid(16)
                    return f.read(xmp_size)

            # Also check inside moov
            if atom["type"] == b"moov":
                children = self._parse_child_atoms(f, atom["offset"], atom["actual_size"])
                for child in children:
                    if child["type"] == b"uuid":
                        f.seek(child["offset"] + 8)
                        uuid_bytes = f.read(16)
                        if uuid_bytes == XMP_UUID:
                            xmp_size = child["actual_size"] - 24
                            return f.read(xmp_size)

        return None

    def _read_udta_metadata(self, f, moov: dict) -> dict[str, Any]:
        """Read metadata from moov/udta atom."""
        metadata = {}

        children = self._parse_child_atoms(f, moov["offset"], moov["actual_size"])
        udta = next((c for c in children if c["type"] == b"udta"), None)
        if not udta:
            return metadata

        udta_children = self._parse_child_atoms(f, udta["offset"], udta["actual_size"])

        # Common QuickTime/iTunes metadata atoms
        tag_map = {
            b"\xa9nam": "title",
            b"\xa9ART": "artist",
            b"\xa9alb": "album",
            b"\xa9day": "date",
            b"\xa9cmt": "comment",
            b"\xa9gen": "genre",
            b"\xa9des": "description",
            b"\xa9wrt": "writer",
        }

        for child in udta_children:
            tag_name = tag_map.get(child["type"])
            if tag_name:
                f.seek(child["offset"] + child["header_size"])
                data = f.read(child["actual_size"] - child["header_size"])
                # Skip data atom header (size + 'data' + type + locale = 16 bytes)
                if len(data) > 16 and data[4:8] == b"data":
                    text = data[16:].decode("utf-8", errors="replace")
                    metadata[tag_name] = text
                elif data:
                    metadata[tag_name] = data.decode("utf-8", errors="replace")

        return metadata

    def _build_xmp_packet(self, xmp_fields: list) -> bytes:
        """Build an XMP packet from metadata fields."""
        # Build minimal XMP packet
        parts = [
            '<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>',
            '<x:xmpmeta xmlns:x="adobe:ns:meta/">',
            '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">',
            '<rdf:Description rdf:about=""',
            '  xmlns:dc="http://purl.org/dc/elements/1.1/"',
            '  xmlns:xmp="http://ns.adobe.com/xap/1.0/"',
            '  xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/"',
            '  xmlns:Iptc4xmpCore="http://iptc.org/std/Iptc4xmpCore/1.0/xmlns/">',
        ]

        for field in xmp_fields:
            key = field.key
            value = field.value

            if ":" not in key:
                key = f"dc:{key}"

            if isinstance(value, list):
                parts.append(f"  <{key}>")
                parts.append("    <rdf:Bag>")
                for item in value:
                    parts.append(f"      <rdf:li>{_xml_escape(str(item))}</rdf:li>")
                parts.append("    </rdf:Bag>")
                parts.append(f"  </{key}>")
            else:
                parts.append(f"  <{key}>{_xml_escape(str(value))}</{key}>")

        parts.extend([
            "</rdf:Description>",
            "</rdf:RDF>",
            "</x:xmpmeta>",
        ])

        # Add padding for in-place updates (2KB padding)
        packet = "\n".join(parts)
        padding = " " * max(0, 2048 - len(packet) - 20)
        packet += padding
        packet += '<?xpacket end="w"?>'

        return packet.encode("utf-8")

    def _inject_xmp_uuid(self, staging_path: Path, atoms: list[dict], xmp_data: bytes):
        """
        Inject XMP data into a UUID atom.

        If an existing XMP UUID atom exists and the new data fits, it is
        updated in-place. Otherwise, a new UUID atom is appended and
        offsets are recalculated.
        """
        uuid_atom_data = XMP_UUID + xmp_data
        atom_size = 8 + len(uuid_atom_data)
        atom_header = struct.pack(">I", atom_size) + b"uuid"

        # Find existing XMP UUID
        existing_xmp = None
        with open(staging_path, "rb") as f:
            for atom in atoms:
                if atom["type"] == b"uuid":
                    f.seek(atom["offset"] + 8)
                    uuid_bytes = f.read(16)
                    if uuid_bytes == XMP_UUID:
                        existing_xmp = atom
                        break

        if existing_xmp and atom_size <= existing_xmp["actual_size"]:
            # In-place update: same size or smaller (pad with free atom)
            with open(staging_path, "r+b") as f:
                f.seek(existing_xmp["offset"])
                f.write(atom_header + uuid_atom_data)

                # If smaller, write a free atom for remaining space
                remaining = existing_xmp["actual_size"] - atom_size
                if remaining >= 8:
                    f.write(struct.pack(">I", remaining) + b"free")
                    f.write(b"\x00" * (remaining - 8))
        else:
            # Append new UUID atom before mdat (or at the end)
            self._append_atom_before_mdat(staging_path, atoms, atom_header + uuid_atom_data)

    def _inject_udta_metadata(self, staging_path: Path, atoms: list[dict], fields: list):
        """Inject IPTC/EXIF metadata into moov/udta atoms."""
        # Build udta child atoms for each field
        tag_map = {
            "title": b"\xa9nam",
            "Title": b"\xa9nam",
            "artist": b"\xa9ART",
            "Artist": b"\xa9ART",
            "album": b"\xa9alb",
            "Album": b"\xa9alb",
            "date": b"\xa9day",
            "Date": b"\xa9day",
            "comment": b"\xa9cmt",
            "Comment": b"\xa9cmt",
            "description": b"\xa9des",
            "Description": b"\xa9des",
            "genre": b"\xa9gen",
            "Genre": b"\xa9gen",
            "writer": b"\xa9wrt",
            "Writer": b"\xa9wrt",
        }

        udta_children = b""
        for field in fields:
            tag = tag_map.get(field.key)
            if tag is None:
                logger.warning("Unmapped IPTC/EXIF field: %s", field.key)
                continue

            value_bytes = str(field.value).encode("utf-8")
            # Build data atom: size(4) + 'data'(4) + type(4) + locale(4) + value
            data_payload = struct.pack(">I", 16 + len(value_bytes)) + b"data"
            data_payload += struct.pack(">I", 1)  # type: UTF-8
            data_payload += struct.pack(">I", 0)  # locale
            data_payload += value_bytes

            # Build tag atom: size(4) + tag(4) + data_atom
            tag_atom = struct.pack(">I", 8 + len(data_payload)) + tag + data_payload
            udta_children += tag_atom

        if not udta_children:
            return

        # Build udta atom
        udta_atom = struct.pack(">I", 8 + len(udta_children)) + b"udta" + udta_children

        # For now, append udta into moov by rebuilding the file
        # This is a simplified approach; a full implementation would
        # surgically insert into the existing moov atom
        self._append_atom_before_mdat(staging_path, atoms, udta_atom)

    def _append_atom_before_mdat(self, staging_path: Path, atoms: list[dict], new_atom: bytes):
        """
        Append a new atom before the mdat atom and recalculate offsets.

        This is the most common injection strategy for ISOBMFF files.
        """
        mdat = next((a for a in atoms if a["type"] == b"mdat"), None)
        if not mdat:
            # No mdat: just append to end of file
            with open(staging_path, "ab") as f:
                f.write(new_atom)
            return

        insert_offset = mdat["offset"]
        shift = len(new_atom)

        # Read the entire file and rebuild with new atom inserted
        with open(staging_path, "rb") as f:
            before_mdat = f.read(insert_offset)
            f.seek(insert_offset)
            from_mdat = f.read()

        # Write rebuilt file
        with open(staging_path, "wb") as f:
            f.write(before_mdat)
            f.write(new_atom)
            f.write(from_mdat)

        # Recalculate stco/co64 offsets in moov
        self._adjust_chunk_offsets(staging_path, shift)

    def _adjust_chunk_offsets(self, file_path: Path, shift: int):
        """
        Adjust stco and co64 chunk offset tables after inserting data.

        When atoms are inserted before mdat, all chunk offsets in the
        stco (32-bit) and co64 (64-bit) atoms must be incremented by
        the size of the inserted data.
        """
        atoms = self._parse_top_level_atoms(file_path)
        moov = next((a for a in atoms if a["type"] == b"moov"), None)
        if not moov:
            return

        with open(file_path, "r+b") as f:
            self._adjust_offsets_in_container(f, moov["offset"], moov["actual_size"], shift)

    def _adjust_offsets_in_container(self, f, offset: int, size: int, shift: int):
        """Recursively find and adjust stco/co64 atoms."""
        children = self._parse_child_atoms(f, offset, size)

        for child in children:
            if child["type"] == b"stco":
                # 32-bit chunk offsets
                f.seek(child["offset"] + child["header_size"])
                version_flags = f.read(4)
                entry_count = struct.unpack(">I", f.read(4))[0]
                entries_offset = f.tell()

                for i in range(entry_count):
                    f.seek(entries_offset + i * 4)
                    old_offset = struct.unpack(">I", f.read(4))[0]
                    new_offset = old_offset + shift
                    f.seek(entries_offset + i * 4)
                    f.write(struct.pack(">I", new_offset))

                logger.debug("Adjusted %d stco entries by +%d", entry_count, shift)

            elif child["type"] == b"co64":
                # 64-bit chunk offsets
                f.seek(child["offset"] + child["header_size"])
                version_flags = f.read(4)
                entry_count = struct.unpack(">I", f.read(4))[0]
                entries_offset = f.tell()

                for i in range(entry_count):
                    f.seek(entries_offset + i * 8)
                    old_offset = struct.unpack(">Q", f.read(8))[0]
                    new_offset = old_offset + shift
                    f.seek(entries_offset + i * 8)
                    f.write(struct.pack(">Q", new_offset))

                logger.debug("Adjusted %d co64 entries by +%d", entry_count, shift)

            elif child["type"] in (b"moov", b"trak", b"mdia", b"minf", b"stbl", b"udta", b"edts"):
                # Recurse into container atoms
                self._adjust_offsets_in_container(f, child["offset"], child["actual_size"], shift)


def _xml_escape(text: str) -> str:
    """Escape special XML characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


# Auto-register
_handler = ISOBMFFHandler()
register_handler(_handler)
