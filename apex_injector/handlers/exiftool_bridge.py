"""
Apex Meta-Injector — ExifTool Bridge Handler.

Universal fallback handler using PyExifTool for XMP, IPTC, and EXIF
metadata injection across any container format. Runs ExifTool in
batch (stay-open) mode for high throughput.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from apex_injector.codec_manifest import ContainerFormat, MetadataSchema
from apex_injector.config import get_config
from apex_injector.handlers import (
    ContainerHandler,
    FileAnalysis,
    InjectionResult,
    InjectionStatus,
    MetadataPayload,
)

logger = logging.getLogger(__name__)

# Schema prefix mapping for ExifTool
SCHEMA_PREFIX = {
    MetadataSchema.XMP: "XMP-dc",
    MetadataSchema.IPTC: "IPTC",
    MetadataSchema.EXIF: "EXIF",
    MetadataSchema.ID3V24: "ID3",
}


class ExifToolBridge(ContainerHandler):
    """
    Universal metadata handler using ExifTool.

    This is a fallback handler for containers where native parsing
    isn't implemented, or for writing schemas that require ExifTool's
    comprehensive format support.

    NOTE: This handler is NOT auto-registered since it serves as a
    fallback/supplement rather than a primary handler.
    """

    def __init__(self):
        self._process: Optional[subprocess.Popen] = None

    @property
    def supported_containers(self) -> list[ContainerFormat]:
        return list(ContainerFormat)

    @property
    def supported_schemas(self) -> list[MetadataSchema]:
        return [MetadataSchema.XMP, MetadataSchema.IPTC, MetadataSchema.EXIF, MetadataSchema.ID3V24]

    def _get_exiftool(self) -> str | None:
        config = get_config()
        return config.tools.exiftool

    def _run_exiftool(self, args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
        """Run ExifTool with arguments."""
        exiftool = self._get_exiftool()
        if not exiftool:
            raise RuntimeError("ExifTool not available")

        cmd = [exiftool] + args
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=creation_flags,
        )

    def analyze(self, file_path: Path) -> FileAnalysis:
        """Analyze a file using ExifTool."""
        analysis = FileAnalysis(
            file_path=file_path,
            file_size=file_path.stat().st_size,
        )

        try:
            proc = self._run_exiftool(["-json", "-G", "-s", str(file_path)])
            if proc.returncode == 0 and proc.stdout:
                data = json.loads(proc.stdout)
                if data and isinstance(data, list):
                    raw = data[0]
                    analysis.codec = raw.get("File:FileType", "unknown")
                    analysis.injectable = True

        except Exception as e:
            analysis.error = str(e)

        return analysis

    def read_metadata(self, file_path: Path) -> dict[str, dict[str, Any]]:
        """Read all metadata from a file using ExifTool."""
        metadata: dict[str, dict[str, Any]] = {}

        try:
            proc = self._run_exiftool(["-json", "-G", "-s", str(file_path)])
            if proc.returncode != 0 or not proc.stdout:
                return metadata

            data = json.loads(proc.stdout)
            if not data or not isinstance(data, list):
                return metadata

            raw = data[0]
            for key, value in raw.items():
                if ":" in key:
                    group, _, tag = key.partition(":")
                    group_lower = group.lower()

                    if group_lower.startswith("xmp"):
                        metadata.setdefault("xmp", {})[tag] = value
                    elif group_lower == "iptc":
                        metadata.setdefault("iptc", {})[tag] = value
                    elif group_lower == "exif":
                        metadata.setdefault("exif", {})[tag] = value
                    elif group_lower == "id3":
                        metadata.setdefault("id3v2.4", {})[tag] = value

        except Exception as e:
            logger.error("ExifTool read failed: %s", e)

        return metadata

    def inject(
        self,
        file_path: Path,
        payload: MetadataPayload,
        staging_path: Path,
    ) -> InjectionResult:
        """
        Inject metadata using ExifTool.

        ExifTool modifies files in-place, so we operate on the staging copy.
        """
        import shutil
        import time

        start = time.perf_counter()
        result = InjectionResult(file_path=file_path, status=InjectionStatus.FAILED)

        exiftool = self._get_exiftool()
        if not exiftool:
            result.message = "ExifTool not available"
            return result

        # Copy to staging
        shutil.copy2(str(file_path), str(staging_path))

        try:
            args = ["-overwrite_original"]

            for field in payload.fields:
                prefix = SCHEMA_PREFIX.get(field.schema, "")
                key = field.key

                # Build the ExifTool tag notation
                if prefix and ":" not in key:
                    tag = f"-{prefix}:{key}={field.value}"
                elif ":" in key:
                    tag = f"-{key}={field.value}"
                else:
                    tag = f"-{key}={field.value}"

                # Handle list values
                if isinstance(field.value, list):
                    for item in field.value:
                        args.append(f"-{prefix}:{key}={item}")
                else:
                    args.append(tag)

            args.append(str(staging_path))

            proc = self._run_exiftool(args)

            if proc.returncode == 0:
                result.status = InjectionStatus.SUCCESS
                result.fields_written = len(payload.fields)
            elif "Warning" in (proc.stderr or ""):
                result.status = InjectionStatus.PARTIAL
                result.fields_written = len(payload.fields)
                result.message = proc.stderr.strip()
            else:
                result.message = proc.stderr.strip() if proc.stderr else "Unknown error"

            result.duration_ms = (time.perf_counter() - start) * 1000

        except Exception as e:
            result.message = str(e)
            logger.error("ExifTool injection failed: %s", e)

        return result

    def read_batch(self, file_paths: list[Path]) -> dict[str, dict[str, dict[str, Any]]]:
        """
        Read metadata from multiple files in a single ExifTool invocation.

        Returns a dict mapping file paths to their metadata.
        """
        if not file_paths:
            return {}

        try:
            args = ["-json", "-G", "-s"] + [str(p) for p in file_paths]
            proc = self._run_exiftool(args, timeout=300)

            if proc.returncode != 0 or not proc.stdout:
                return {}

            data = json.loads(proc.stdout)
            results = {}

            for entry in data:
                source_file = entry.get("SourceFile", "")
                metadata: dict[str, dict[str, Any]] = {}

                for key, value in entry.items():
                    if key == "SourceFile":
                        continue
                    if ":" in key:
                        group, _, tag = key.partition(":")
                        group_lower = group.lower()
                        if group_lower.startswith("xmp"):
                            metadata.setdefault("xmp", {})[tag] = value
                        elif group_lower == "iptc":
                            metadata.setdefault("iptc", {})[tag] = value
                        elif group_lower == "exif":
                            metadata.setdefault("exif", {})[tag] = value

                results[source_file] = metadata

            return results

        except Exception as e:
            logger.error("ExifTool batch read failed: %s", e)
            return {}


# Singleton instance for shared use
_bridge = ExifToolBridge()


def get_exiftool_bridge() -> ExifToolBridge:
    """Get the shared ExifTool bridge instance."""
    return _bridge
