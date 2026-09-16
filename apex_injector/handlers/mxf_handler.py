"""Read-only MXF identification and metadata inspection via ExifTool."""

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

# SMPTE UL prefix for MXF identification
MXF_UL_PREFIX = b"\x06\x0e\x2b\x34"


class MXFHandler(ContainerHandler):
    """
    Handler for MXF (Material Exchange Format) containers.

    Identifies the MXF signature. No write or re-wrap implementation is enabled.
    """

    @property
    def supported_containers(self) -> list[ContainerFormat]:
        return [ContainerFormat.MXF]

    @property
    def supported_schemas(self) -> list[MetadataSchema]:
        return []

    def _run_tool(self, cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
        """Run a metadata inspection tool."""
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=creation_flags,
        )

    def analyze(self, file_path: Path) -> FileAnalysis:
        """Analyze an MXF file for codec, operational pattern, and metadata."""
        analysis = FileAnalysis(
            file_path=file_path,
            file_size=file_path.stat().st_size,
            container=ContainerFormat.MXF,
            complex_wrap_risk="high",
        )

        # Check if file starts with SMPTE UL
        try:
            with open(file_path, "rb") as f:
                header = f.read(4)
                if header != MXF_UL_PREFIX:
                    analysis.error = "Not a valid MXF file"
                    return analysis
        except Exception as e:
            analysis.error = str(e)
            return analysis

        analysis.injectable = False
        analysis.error = "MXF is read-only; metadata writing is not implemented"
        analysis.current_metadata = self.read_metadata(file_path)
        return analysis

    def read_metadata(self, file_path: Path) -> dict[str, dict[str, Any]]:
        """Read metadata from MXF file using ExifTool (most reliable for MXF reading)."""
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
                    smpte_fields = {}
                    xmp_fields = {}
                    for key, value in raw.items():
                        if key.startswith("MXF:"):
                            smpte_fields[key[4:]] = value
                        elif key.startswith("XMP:"):
                            xmp_fields[key[4:]] = value

                    if smpte_fields:
                        metadata["smpte_st_377"] = smpte_fields
                    if xmp_fields:
                        metadata["xmp"] = xmp_fields

        except Exception as e:
            logger.debug("Failed to read MXF metadata: %s", e)

        return metadata

    def inject(self, file_path, payload, staging_path):
        return InjectionResult(
            file_path=file_path,
            status=InjectionStatus.UNSUPPORTED_FIELD,
            fields_failed=len(payload.fields),
            message="MXF writing is unavailable: ExifTool reads MXF but cannot write it. "
            "No rewrap or operational-pattern conversion was performed.",
        )

    def execute_complex_wrap(self, file_path, payload, staging_path):
        return self.inject(file_path, payload, staging_path)

    def validate(self, path):
        raise ValueError("MXF metadata writing is not implemented")


# Auto-register
_handler = MXFHandler()
register_handler(_handler)
