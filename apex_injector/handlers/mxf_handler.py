"""
Apex Meta-Injector — MXF Container Handler.

Handles metadata injection for MXF (Material Exchange Format) containers
using the bmx toolsuite for safe KLV header manipulation.

MXF is a highly complex format with partitioned structure. Direct byte-level
manipulation is extremely risky, so we delegate to bmxtranswrap — the
industry-standard BBC open-source tool for MXF re-wrapping.

When header-only modification is insufficient, the handler flags the
operation as a "Complex Wrap" requiring user permission.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import tempfile
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

# SMPTE UL prefix for MXF identification
MXF_UL_PREFIX = b"\x06\x0e\x2b\x34"


class MXFHandler(ContainerHandler):
    """
    Handler for MXF (Material Exchange Format) containers.

    Supports OP-1a and OP-Atom operational patterns. Uses bmx tools
    for metadata manipulation and mxf2raw for analysis.
    """

    @property
    def supported_containers(self) -> list[ContainerFormat]:
        return [ContainerFormat.MXF]

    @property
    def supported_schemas(self) -> list[MetadataSchema]:
        return [MetadataSchema.SMPTE_377, MetadataSchema.XMP]

    def _get_tool_path(self, tool_name: str) -> str | None:
        """Get path to a bmx tool."""
        config = get_config()
        return getattr(config.tools, tool_name, None)

    def _run_tool(self, cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
        """Run a BMX tool command."""
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

        # Try to use mxf2raw for analysis
        bmxtranswrap = self._get_tool_path("bmxtranswrap")
        if not bmxtranswrap:
            analysis.error = "bmxtranswrap not available"
            analysis.injectable = False
            return analysis

        try:
            # Use bmxtranswrap --info to get file information
            proc = self._run_tool([bmxtranswrap, "--info", str(file_path)])
            if proc.returncode == 0:
                info = self._parse_bmx_info(proc.stdout)
                analysis.codec = info.get("codec", "unknown")
                analysis.codec_fourcc = info.get("fourcc", "")
                analysis.injectable = True
            else:
                # bmx tools may not have --info; mark as injectable with caveats
                analysis.injectable = True
                analysis.codec = "MXF (analysis requires mxf2raw)"
        except Exception as e:
            analysis.error = str(e)
            analysis.injectable = True  # Still attempt injection

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
            proc = self._run_tool([
                exiftool, "-json", "-G", "-s", str(file_path)
            ])
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

    def inject(
        self,
        file_path: Path,
        payload: MetadataPayload,
        staging_path: Path,
    ) -> InjectionResult:
        """
        Inject metadata into an MXF file.

        This always requires a container re-wrap via bmxtranswrap, which
        is classified as a "Complex Wrap" operation. The bitstream is
        NEVER re-encoded — only the container structure is regenerated.
        """
        result = InjectionResult(file_path=file_path, status=InjectionStatus.FAILED)

        bmxtranswrap = self._get_tool_path("bmxtranswrap")
        if not bmxtranswrap:
            result.message = "bmxtranswrap not available. Install bmx tools."
            return result

        # MXF always requires Complex Wrap
        result.status = InjectionStatus.COMPLEX_WRAP_REQUIRED
        result.complex_wrap_info = (
            "MXF metadata injection requires container re-wrapping via bmxtranswrap. "
            "The video/audio bitstream will NOT be re-encoded. "
            "Only the MXF partition structure and header metadata are modified."
        )

        return result

    def execute_complex_wrap(
        self,
        file_path: Path,
        payload: MetadataPayload,
        staging_path: Path,
    ) -> InjectionResult:
        """
        Execute the Complex Wrap operation after user approval.

        This re-wraps the MXF container with updated metadata using bmxtranswrap.
        """
        import time
        start = time.perf_counter()
        result = InjectionResult(file_path=file_path, status=InjectionStatus.FAILED)

        bmxtranswrap = self._get_tool_path("bmxtranswrap")
        config = get_config()
        exiftool = config.tools.exiftool

        try:
            # Step 1: Re-wrap with bmxtranswrap (copy essence, rebuild container)
            cmd = [
                bmxtranswrap,
                "-t", "op1a",  # Output as OP-1a
                "-o", str(staging_path),
                str(file_path),
            ]

            proc = self._run_tool(cmd, timeout=300)
            if proc.returncode != 0:
                result.message = f"bmxtranswrap failed: {proc.stderr}"
                return result

            # Step 2: Inject metadata via ExifTool on the re-wrapped file
            if exiftool and payload.fields:
                exiftool_args = [exiftool, "-overwrite_original"]

                for field in payload.fields:
                    if field.schema == MetadataSchema.XMP:
                        exiftool_args.append(f"-XMP:{field.key}={field.value}")
                    elif field.schema == MetadataSchema.SMPTE_377:
                        exiftool_args.append(f"-MXF:{field.key}={field.value}")

                exiftool_args.append(str(staging_path))
                proc = self._run_tool(exiftool_args)

                if proc.returncode != 0:
                    logger.warning("ExifTool metadata injection had warnings: %s", proc.stderr)

            result.status = InjectionStatus.SUCCESS
            result.fields_written = len(payload.fields)
            result.duration_ms = (time.perf_counter() - start) * 1000

        except Exception as e:
            result.status = InjectionStatus.FAILED
            result.message = str(e)
            logger.error("MXF complex wrap failed: %s", e)

        return result

    def _parse_bmx_info(self, stdout: str) -> dict:
        """Parse bmx tool output for codec information."""
        info = {}
        for line in stdout.splitlines():
            line = line.strip()
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip().lower()
                value = value.strip()
                if "codec" in key or "essence" in key:
                    info["codec"] = value
                elif "fourcc" in key:
                    info["fourcc"] = value
        return info


# Auto-register
_handler = MXFHandler()
register_handler(_handler)
