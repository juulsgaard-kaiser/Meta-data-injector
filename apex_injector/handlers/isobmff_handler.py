"""MP4/MOV XMP updates without moving any existing media or sample tables."""

import hashlib
import shutil
import struct
from pathlib import Path

from apex_injector.codec_manifest import ContainerFormat, MetadataSchema
from apex_injector.handlers import ContainerHandler, FileAnalysis, InjectionResult, InjectionStatus, register_handler
from apex_injector.handlers.xml_metadata import merge_xmp, read_xmp
from apex_injector.win32_io import hash_regions

XMP_UUID = bytes.fromhex("be7acfcb97a942e89c71999491e3afac")
CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"udta", b"edts", b"mvex", b"moof", b"traf"}


class ISOBMFFHandler(ContainerHandler):
    supported_containers = [ContainerFormat.MP4, ContainerFormat.MOV]
    supported_schemas = [MetadataSchema.XMP]

    def _boxes(self, f, start, end, depth=0):
        if depth > 32:
            raise ValueError("MP4 nesting limit exceeded")
        boxes = []
        offset = start
        while offset < end:
            f.seek(offset)
            header = f.read(8)
            if len(header) != 8:
                raise ValueError("Truncated MP4 atom header")
            size, kind = struct.unpack(">I4s", header)
            header_size = 8
            actual = size
            if size == 1:
                extra = f.read(8)
                if len(extra) != 8:
                    raise ValueError("Truncated extended atom")
                actual = struct.unpack(">Q", extra)[0]
                header_size = 16
            elif size == 0:
                actual = end - offset
            if actual < header_size or offset + actual > end:
                raise ValueError("Invalid MP4 atom size or boundary")
            box = dict(type=kind, offset=offset, size=size, actual_size=actual, header_size=header_size)
            if kind in CONTAINERS:
                box["children"] = self._boxes(f, offset + header_size, offset + actual, depth + 1)
            boxes.append(box)
            if len(boxes) > 100000:
                raise ValueError("MP4 atom count limit exceeded")
            offset += actual
        return boxes

    def _parse_top_level_atoms(self, path):
        with open(path, "rb") as f:
            return self._boxes(f, 0, Path(path).stat().st_size)

    def _walk(self, boxes):
        for box in boxes:
            yield box
            yield from self._walk(box.get("children", []))

    def _xmp_boxes(self, path, boxes):
        found = []
        with open(path, "rb") as f:
            for box in self._walk(boxes):
                if box["type"] == b"uuid":
                    f.seek(box["offset"] + box["header_size"])
                    if f.read(16) == XMP_UUID:
                        found.append(box)
        return found

    def _packet(self, path, box):
        size = box["actual_size"] - box["header_size"] - 16
        if size < 0 or size > 16 * 1024 * 1024:
            raise ValueError("Invalid XMP packet size")
        with open(path, "rb") as f:
            f.seek(box["offset"] + box["header_size"] + 16)
            return f.read(size)

    def validate(self, path):
        boxes = self._parse_top_level_atoms(path)
        if not any(b["type"] == b"moov" for b in boxes) or not any(b["type"] == b"mdat" for b in boxes):
            raise ValueError("MP4 requires moov and mdat atoms")
        regions = self.get_essence_regions(path)
        with open(path, "rb") as f:
            for box in self._walk(boxes):
                if box["type"] in (b"stco", b"co64"):
                    width = 4 if box["type"] == b"stco" else 8
                    f.seek(box["offset"] + box["header_size"])
                    header = f.read(8)
                    if len(header) != 8:
                        raise ValueError("Truncated chunk table")
                    count = struct.unpack(">I", header[4:])[0]
                    if box["actual_size"] != box["header_size"] + 8 + width * count:
                        raise ValueError("Invalid chunk offset table")
                    for _ in range(count):
                        offset = int.from_bytes(f.read(width), "big")
                        if not any(start <= offset < start + size for start, size in regions):
                            raise ValueError("Chunk offset points outside media data")
        for box in self._xmp_boxes(path, boxes):
            read_xmp(self._packet(path, box))

    def get_essence_regions(self, path):
        return [
            (b["offset"] + b["header_size"], b["actual_size"] - b["header_size"])
            for b in self._parse_top_level_atoms(path)
            if b["type"] == b"mdat" and b["actual_size"] > b["header_size"]
        ]

    def get_essence_region(self, path):
        regions = self.get_essence_regions(path)
        if not regions:
            raise ValueError("No media data found")
        return regions[0]

    def essence_fingerprint(self, path):
        # Also protect sample tables, codec descriptors and fragment addressing.
        boxes = self._parse_top_level_atoms(path)
        h = hashlib.sha256(hash_regions(path, self.get_essence_regions(path)).encode())
        protected = [
            b
            for b in self._walk(boxes)
            if b["type"]
            in {b"stco", b"co64", b"stsd", b"stsz", b"stz2", b"stsc", b"stts", b"ctts", b"tfhd", b"tfdt", b"trun"}
        ]
        if protected:
            h.update(hash_regions(path, [(b["offset"], b["actual_size"]) for b in protected]).encode())
        return h.hexdigest()

    def read_metadata(self, path):
        boxes = self._xmp_boxes(path, self._parse_top_level_atoms(path))
        if len(boxes) > 1:
            raise ValueError("Multiple XMP packets require manual reconciliation")
        return {"xmp": read_xmp(self._packet(path, boxes[0]))} if boxes else {}

    def analyze(self, path):
        result = FileAnalysis(
            file_path=path,
            file_size=path.stat().st_size,
            container=ContainerFormat.MOV if path.suffix.lower() in (".mov", ".qt") else ContainerFormat.MP4,
        )
        try:
            self.validate(path)
            result.current_metadata = self.read_metadata(path)
            with open(path, "rb") as f:
                for box in self._walk(self._parse_top_level_atoms(path)):
                    if box["type"] == b"stsd":
                        f.seek(box["offset"] + box["header_size"] + 12)
                        result.codec_fourcc = f.read(4).decode("ascii", errors="replace")
                        result.codec = result.codec_fourcc
                        break
            result.injectable = True
        except Exception as e:
            result.error = str(e)
        return result

    def inject(self, file_path, payload, staging_path):
        result = InjectionResult(file_path=file_path, status=InjectionStatus.FAILED)
        try:
            if any(f.schema not in self.supported_schemas for f in payload.fields) or not payload.fields:
                raise ValueError("Native MP4/MOV editing supports XMP fields only")
            self.validate(file_path)
            atoms = self._parse_top_level_atoms(file_path)
            existing = self._xmp_boxes(file_path, atoms)
            if len(existing) > 1:
                raise ValueError("Multiple XMP packets require manual reconciliation")
            packet = merge_xmp(self._packet(file_path, existing[0]) if existing else None, payload.fields)
            new_atom = struct.pack(">I4s", 24 + len(packet), b"uuid") + XMP_UUID + packet
            shutil.copy2(file_path, staging_path)
            with open(staging_path, "r+b") as f:
                for box in existing:
                    f.seek(box["offset"] + 4)
                    f.write(b"free")
                # A size-zero last atom must stop at its old end before appending.
                last = atoms[-1]
                if last["size"] == 0:
                    if last["actual_size"] > 0xFFFFFFFF:
                        raise ValueError("Cannot safely extend an EOF-sized atom larger than 4 GiB")
                    f.seek(last["offset"])
                    f.write(struct.pack(">I", last["actual_size"]))
                f.seek(0, 2)
                f.write(new_atom)
            self.validate(staging_path)
            result.status = InjectionStatus.SUCCESS
            result.fields_written = len(payload.fields)
        except Exception as e:
            result.message = str(e)
            result.fields_failed = len(payload.fields)
        return result


register_handler(ISOBMFFHandler())
