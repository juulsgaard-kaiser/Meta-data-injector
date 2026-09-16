"""
Apex Meta-Injector — Matroska/MKV Container Handler.

Handles metadata injection for MKV/MKA/WebM containers using mkvpropedit
from MKVToolNix. This is the industry-standard tool for EBML property
editing without full container remuxing.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from apex_injector.codec_manifest import ContainerFormat, MetadataSchema
from apex_injector.config import get_config
from apex_injector.handlers import (
    ContainerHandler,
    FileAnalysis,
    InjectionResult,
    InjectionStatus,
    register_handler,
)

logger = logging.getLogger(__name__)

# Mapping from common metadata keys to Matroska tag names
MATROSKA_TAG_MAP = {
    # Title / Description
    "title": ("info", "title"),
    "Title": ("info", "title"),
    "dc:Title": ("info", "title"),
    "description": ("tags", "DESCRIPTION"),
    "Description": ("tags", "DESCRIPTION"),
    "dc:Description": ("tags", "DESCRIPTION"),
    # People
    "artist": ("tags", "ARTIST"),
    "Artist": ("tags", "ARTIST"),
    "dc:Creator": ("tags", "ARTIST"),
    "director": ("tags", "DIRECTOR"),
    "Director": ("tags", "DIRECTOR"),
    # Dates
    "date": ("info", "date"),
    "Date": ("info", "date"),
    "dc:Date": ("info", "date"),
    # Classification
    "genre": ("tags", "GENRE"),
    "Genre": ("tags", "GENRE"),
    "comment": ("tags", "COMMENT"),
    "Comment": ("tags", "COMMENT"),
    "keywords": ("tags", "KEYWORDS"),
    "Keywords": ("tags", "KEYWORDS"),
    # Technical
    "encoder": ("tags", "ENCODER"),
    "copyright": ("tags", "COPYRIGHT"),
    "Copyright": ("tags", "COPYRIGHT"),
}


class MatroskaHandler(ContainerHandler):
    """
    Handler for Matroska (MKV / MKA / WebM) containers.

    Delegates all metadata manipulation to mkvpropedit, which handles
    the complex EBML element sizing and repositioning.
    """

    @property
    def supported_containers(self) -> list[ContainerFormat]:
        return [ContainerFormat.MKV]

    @property
    def supported_schemas(self) -> list[MetadataSchema]:
        return [MetadataSchema.MATROSKA_TAGS, MetadataSchema.VORBIS]

    def _get_mkvpropedit(self) -> str | None:
        config = get_config()
        return config.tools.mkvpropedit

    def _get_mkvmerge(self) -> str | None:
        config = get_config()
        return config.tools.mkvmerge

    def _run_tool(self, cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8-sig",
            errors="replace",
            timeout=timeout,
            creationflags=creation_flags,
        )

    def analyze(self, file_path: Path) -> FileAnalysis:
        """Analyze an MKV file using mkvmerge --identify."""
        analysis = FileAnalysis(
            file_path=file_path,
            file_size=file_path.stat().st_size,
            container=ContainerFormat.MKV,
        )

        # Verify EBML header
        try:
            with open(file_path, "rb") as f:
                if f.read(4) != b"\x1a\x45\xdf\xa3":
                    analysis.error = "Not a valid Matroska file"
                    return analysis
        except Exception as e:
            analysis.error = str(e)
            return analysis

        # Use mkvmerge --identify for detailed info
        mkvmerge = self._get_mkvmerge()
        if mkvmerge:
            try:
                proc = self._run_tool([mkvmerge, "--identify", "--identification-format", "json", str(file_path)])
                if proc.returncode == 0:
                    info = json.loads(proc.stdout)
                    tracks = info.get("tracks", [])
                    for track in tracks:
                        if track.get("type") == "video":
                            analysis.codec = track.get("codec", "unknown")
                            analysis.codec_fourcc = track.get("properties", {}).get("codec_id", "")
                            break
                    if not analysis.codec and tracks:
                        analysis.codec = tracks[0].get("codec", "unknown")
            except Exception as e:
                logger.debug("mkvmerge identify failed: %s", e)

        analysis.injectable = all((self._get_mkvpropedit(), get_config().tools.mkvextract, get_config().tools.ffprobe))
        analysis.complex_wrap_risk = "none"
        analysis.current_metadata = self.read_metadata(file_path)

        return analysis

    def read_metadata(self, file_path: Path) -> dict[str, dict[str, Any]]:
        """Read metadata from an MKV file using ExifTool."""
        metadata: dict[str, dict[str, Any]] = {}

        config = get_config()
        exiftool = config.tools.exiftool
        if not exiftool:
            return metadata

        try:
            proc = self._run_tool([exiftool, "-json", "-G", "-s", str(file_path)])
            if proc.returncode == 0 and proc.stdout:
                data = json.loads(proc.stdout)
                if data and isinstance(data, list):
                    raw = data[0]
                    tags = {}
                    for key, value in raw.items():
                        if key.startswith("Matroska:"):
                            tags[key[9:]] = value
                    if tags:
                        metadata["matroska_tags"] = tags
        except Exception as e:
            logger.debug("Failed to read MKV metadata: %s", e)

        return metadata

    def _read_tags(self, path):
        from xml.etree import ElementTree as ET

        from apex_injector.handlers.xml_metadata import parse_xml

        tool = get_config().tools.mkvextract
        if not tool:
            raise ValueError("mkvextract is required to preserve existing tags")
        proc = self._run_tool([tool, str(path), "tags"])
        if proc.returncode != 0:
            raise ValueError(f"mkvextract failed: {proc.stderr}")
        # mkvextract emits the standard external DTD declaration. It is not
        # needed to interpret tags; remove only this exact known declaration.
        xml = proc.stdout.replace('<!DOCTYPE Tags SYSTEM "matroskatags.dtd">', "")
        return parse_xml(xml.encode()) if xml.strip() else ET.Element("Tags")

    def inject(self, file_path, payload, staging_path):
        import shutil
        from xml.etree import ElementTree as ET

        result = InjectionResult(file_path=file_path, status=InjectionStatus.FAILED)
        tag_file = None
        try:
            if not payload.fields or any(f.schema not in self.supported_schemas for f in payload.fields):
                raise ValueError("Use matroska_tags or vorbis_comments for Matroska")
            tool = self._get_mkvpropedit()
            if not tool:
                raise ValueError("mkvpropedit is required")
            root = self._read_tags(file_path)
            global_tags = [
                tag for tag in root.findall("Tag") if tag.find("Targets") is None or len(tag.find("Targets")) == 0
            ]
            if not global_tags:
                tag = ET.SubElement(root, "Tag")
                ET.SubElement(tag, "Targets")
                global_tags = [tag]
            info_edits = []
            expected_info = {}
            expected = {}
            for field in payload.fields:
                level, name = MATROSKA_TAG_MAP.get(field.key, ("tags", field.key.upper()))
                if level == "info":
                    expected_info[name] = str(field.value)
                    info_edits.extend(["--set", f"{name}={field.value}"])
                    continue
                values = field.value if isinstance(field.value, list) else [field.value]
                expected[name] = [str(v) for v in values]
                for tag in global_tags:
                    for node in list(tag):
                        if node.tag == "Simple" and node.findtext("Name") == name:
                            tag.remove(node)
                for value in values:
                    node = ET.SubElement(global_tags[0], "Simple")
                    ET.SubElement(node, "Name").text = name
                    ET.SubElement(node, "String").text = str(value)
            shutil.copy2(file_path, staging_path)
            cmd = [tool, str(staging_path)]
            if info_edits:
                cmd += ["--edit", "info"] + info_edits
            if expected:
                tag_file = staging_path.with_suffix(".tags.xml")
                tag_file.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
                cmd += ["--tags", f"all:{tag_file}"]
            proc = self._run_tool(cmd)
            if proc.returncode != 0:
                raise ValueError(f"mkvpropedit failed or warned: {proc.stderr or proc.stdout}")
            if expected_info:
                identify = self._get_mkvmerge()
                if not identify:
                    raise ValueError("mkvmerge is required to read back Matroska information fields")
                info = self._run_tool([identify, "-J", str(staging_path)])
                if info.returncode:
                    raise ValueError("Matroska information readback failed")
                properties = json.loads(info.stdout).get("container", {}).get("properties", {})
                for name, value in expected_info.items():
                    actual_value = properties.get("date_utc" if name == "date" else name)
                    if name == "date":
                        from datetime import datetime

                        if datetime.fromisoformat(str(actual_value).replace("Z", "+00:00")) != datetime.fromisoformat(
                            value.replace("Z", "+00:00")
                        ):
                            raise ValueError("Matroska date readback failed")
                    elif actual_value != value:
                        raise ValueError(f"Matroska info readback failed: {name}")
            if expected:
                actual = self._read_tags(staging_path)
                for name, values in expected.items():
                    found = [
                        node.findtext("String")
                        for tag in actual.findall("Tag")
                        if tag.find("Targets") is None or len(tag.find("Targets")) == 0
                        for node in tag.findall("Simple")
                        if node.findtext("Name") == name
                    ]
                    if found != values:
                        raise ValueError(f"Matroska tag readback failed: {name}")
            result.status = InjectionStatus.SUCCESS
            result.fields_written = len(payload.fields)
        except Exception as e:
            result.message = str(e)
            result.fields_failed = len(payload.fields)
        finally:
            if tag_file:
                tag_file.unlink(missing_ok=True)
        return result


# Auto-register
_handler = MatroskaHandler()
register_handler(_handler)
