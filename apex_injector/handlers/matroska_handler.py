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
    MetadataPayload,
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

        analysis.injectable = self._get_mkvpropedit() is not None
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

    def inject(
        self,
        file_path: Path,
        payload: MetadataPayload,
        staging_path: Path,
    ) -> InjectionResult:
        """
        Inject metadata into an MKV file using mkvpropedit.

        mkvpropedit modifies the file in-place, so we operate on the
        staging copy. It handles EBML element sizing automatically.
        """
        import shutil
        import time

        start = time.perf_counter()
        result = InjectionResult(file_path=file_path, status=InjectionStatus.FAILED)

        mkvpropedit = self._get_mkvpropedit()
        if not mkvpropedit:
            result.message = "mkvpropedit not available. Install MKVToolNix."
            return result

        # Copy original to staging
        shutil.copy2(str(file_path), str(staging_path))

        fields_written = 0
        fields_failed = 0

        # Build mkvpropedit commands
        # Split into info-level (title, date) and tag-level properties
        info_edits = []
        tag_edits = []

        for field in payload.fields:
            mapping = MATROSKA_TAG_MAP.get(field.key)
            if mapping:
                level, prop_name = mapping
                if level == "info":
                    info_edits.append((prop_name, str(field.value)))
                else:
                    tag_edits.append((prop_name, str(field.value)))
            else:
                # Try as a direct Matroska tag
                tag_edits.append((field.key, str(field.value)))

        try:
            # Apply info-level edits (title, date, etc.)
            if info_edits:
                cmd = [mkvpropedit, str(staging_path), "--edit", "info"]
                for prop_name, value in info_edits:
                    cmd.extend(["--set", f"{prop_name}={value}"])

                proc = self._run_tool(cmd)
                if proc.returncode == 0:
                    fields_written += len(info_edits)
                else:
                    fields_failed += len(info_edits)
                    logger.warning("mkvpropedit info edit failed: %s", proc.stderr)

            # Apply tag-level edits using XML tags
            if tag_edits:
                tag_xml = self._build_matroska_tags_xml(tag_edits)
                tag_file = staging_path.parent / f".{staging_path.stem}_tags.xml"

                try:
                    tag_file.write_text(tag_xml, encoding="utf-8")
                    cmd = [mkvpropedit, str(staging_path), "--tags", f"global:{tag_file}"]
                    proc = self._run_tool(cmd)

                    if proc.returncode == 0:
                        fields_written += len(tag_edits)
                    else:
                        fields_failed += len(tag_edits)
                        logger.warning("mkvpropedit tag edit failed: %s", proc.stderr)
                finally:
                    tag_file.unlink(missing_ok=True)

            result.fields_written = fields_written
            result.fields_failed = fields_failed
            result.status = (
                InjectionStatus.SUCCESS if fields_failed == 0
                else InjectionStatus.PARTIAL if fields_written > 0
                else InjectionStatus.FAILED
            )
            result.duration_ms = (time.perf_counter() - start) * 1000

        except Exception as e:
            result.message = str(e)
            logger.error("MKV injection failed: %s", e)

        return result

    def _build_matroska_tags_xml(self, tags: list[tuple[str, str]]) -> str:
        """Build a Matroska tags XML file for mkvpropedit."""
        lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<!DOCTYPE Tags SYSTEM \"matroskatags.dtd\">", "<Tags>", "  <Tag>", "    <Targets/>"]

        for name, value in tags:
            lines.extend([
                "    <Simple>",
                f"      <Name>{name}</Name>",
                f"      <String>{value}</String>",
                "    </Simple>",
            ])

        lines.extend(["  </Tag>", "</Tags>"])
        return "\n".join(lines)


# Auto-register
_handler = MatroskaHandler()
register_handler(_handler)
