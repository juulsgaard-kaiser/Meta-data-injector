"""
Apex Meta-Injector — ExifTool Bridge Handler.

ExifTool-backed inspection and writes to selected containers.
Uses a separate ExifTool process for each operation.
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
    Additional metadata support using ExifTool.

    This is a fallback handler for containers where native parsing
    isn't implemented, or for writing schemas that require ExifTool's
    comprehensive format support.

    NOTE: This handler is NOT auto-registered since it serves as a
    fallback/supplement rather than a primary handler.
    """

    def __init__(self):
        self._process: subprocess.Popen | None = None

    @property
    def supported_containers(self) -> list[ContainerFormat]:
        return [ContainerFormat.MP4, ContainerFormat.MOV, ContainerFormat.WAV, ContainerFormat.AIFF]

    @property
    def supported_schemas(self) -> list[MetadataSchema]:
        return [MetadataSchema.XMP, MetadataSchema.IPTC, MetadataSchema.EXIF, MetadataSchema.EXIFTOOL]

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

        from apex_injector.codec_manifest import detect_container

        if detect_container(file_path) not in self.supported_containers:
            result.message = "ExifTool writing is not supported for this container"
            return result

        # Copy to staging
        shutil.copy2(str(file_path), str(staging_path))

        try:
            import tempfile

            record = {"SourceFile": str(staging_path.resolve())}
            for field in payload.fields:
                tag = self._tag(field)
                if tag in record:
                    raise ValueError(f"Duplicate tag: {tag}")
                record[tag] = field.value
            with tempfile.TemporaryDirectory(prefix="apex-tags-") as directory:
                updates = Path(directory) / "metadata.json"
                updates.write_text(json.dumps([record], ensure_ascii=False), encoding="utf-8")
                proc = self._run_exiftool(
                    ["-overwrite_original", "-q", "-struct", "-n", f"-j={updates}", str(staging_path.resolve())]
                )

            if proc.returncode == 0 and not proc.stderr.strip():
                self._verify_fields(staging_path, payload)
                result.status = InjectionStatus.SUCCESS
                result.fields_written = len(payload.fields)
            elif "Warning" in (proc.stderr or ""):
                result.status = InjectionStatus.FAILED
                result.fields_failed = len(payload.fields)
                result.message = proc.stderr.strip()
            else:
                result.message = proc.stderr.strip() if proc.stderr else "Unknown error"

            result.duration_ms = (time.perf_counter() - start) * 1000

        except Exception as e:
            result.message = str(e)
            logger.error("ExifTool injection failed: %s", e)

        return result

    @staticmethod
    def _tag(field):
        import re

        key = field.key
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_:.-]*", key):
            raise ValueError(f"Invalid metadata tag: {key}")
        if field.schema == MetadataSchema.EXIFTOOL:
            if ":" not in key:
                raise ValueError("Advanced tags must include a group, e.g. XMP-dc:Title")
            if key.split(":")[0].lower() in ("file", "system", "exiftool"):
                raise ValueError("Filesystem/tool pseudo-tags are read-only")
            return key
        prefix = SCHEMA_PREFIX.get(field.schema, "")
        if field.schema == MetadataSchema.XMP and ":" in key:
            namespace, name = key.split(":", 1)
            return f"{namespace if namespace.lower().startswith('xmp') else 'XMP-' + namespace}:{name}"
        if ":" in key:
            return key
        return f"{prefix}:{key}"

    def inspect_all(self, path):
        """Deep read: duplicates, unknown tags and embedded-document metadata.

        Group/document/instance qualifiers keep unrelated values distinguishable.
        Returned tags are an inventory, not a promise that every tag is writable.
        """
        proc = self._run_exiftool(["-json", "-G1:3:4", "-a", "-u", "-struct", "-n", "-ee3", str(path)], timeout=300)
        if proc.returncode:
            raise ValueError(proc.stderr.strip() or "ExifTool inspection failed")
        return {"tags": json.loads(proc.stdout), "warnings": proc.stderr.strip()}

    def _verify_fields(self, path, payload):
        for field in payload.fields:
            tag = self._tag(field)
            proc = self._run_exiftool(["-j", "-s", "-struct", "-n", f"-{tag}", str(path)])
            if proc.returncode or proc.stderr.strip():
                raise ValueError(f"Metadata readback failed: {tag}")
            records = json.loads(proc.stdout)
            values = [v for k, v in records[0].items() if k != "SourceFile"] if records else []

            def normalize(value):
                if isinstance(value, dict):
                    return {k: normalize(v) for k, v in value.items()}
                if isinstance(value, list):
                    return [normalize(v) for v in value]
                return str(value)

            wanted = field.value if isinstance(field.value, list) else [field.value]
            if not any(
                normalize(value if isinstance(value, list) else [value]) == normalize(wanted) for value in values
            ):
                raise ValueError(f"Metadata was not written: {tag}")

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
