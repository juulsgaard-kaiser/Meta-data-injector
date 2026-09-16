"""
Apex Meta-Injector — Manifest Parser.

Parses JSON and CSV manifests that map metadata to specific file paths.
Supports recursive directory structures and glob pattern matching.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from apex_injector.codec_manifest import MetadataSchema
from apex_injector.handlers import MetadataField, MetadataPayload

logger = logging.getLogger(__name__)


@dataclass
class ManifestEntry:
    """A single entry in a batch manifest."""

    file_path: Path
    metadata: MetadataPayload
    priority: int = 0
    tags: list[str] = field(default_factory=list)


@dataclass
class Manifest:
    """A collection of manifest entries for batch processing."""

    entries: list[ManifestEntry] = field(default_factory=list)
    source_path: Path | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def file_count(self) -> int:
        return len(self.entries)

    def get_unique_files(self) -> set[Path]:
        return {e.file_path for e in self.entries}

    def validate(self) -> list[str]:
        """Validate all entries. Returns list of warnings."""
        warnings = []
        for i, entry in enumerate(self.entries):
            if not entry.file_path.is_file():
                warnings.append(f"Entry {i}: File not found: {entry.file_path}")
            if not entry.metadata.fields:
                warnings.append(f"Entry {i}: No metadata fields defined for {entry.file_path}")
        canonical = [entry.file_path.resolve() for entry in self.entries]
        if len(set(canonical)) != len(canonical):
            warnings.append("Duplicate file paths; combine their metadata into a single entry")
        return warnings


def parse_json_manifest(path: Path) -> Manifest:
    """
    Parse a JSON manifest file.

    Expected format:
    ```json
    [
        {
            "file": "path/to/video.mp4",
            "metadata": {
                "xmp": {
                    "dc:Title": "My Title",
                    "dc:Creator": "Author Name"
                },
                "iptc": {
                    "Keywords": ["tag1", "tag2"]
                }
            },
            "priority": 0,
            "tags": ["batch1"]
        }
    ]
    ```

    Also supports flat format:
    ```json
    [
        {
            "file": "path/to/video.mp4",
            "Title": "My Title",
            "Artist": "Author"
        }
    ]
    ```
    """
    manifest = Manifest(source_path=path)

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            data = [data]

        for i, item in enumerate(data):
            if not isinstance(item, dict):
                manifest.errors.append(f"Entry {i}: Expected object, got {type(item).__name__}")
                continue

            file_path = item.get("file") or item.get("path") or item.get("file_path")
            if not file_path:
                manifest.errors.append(f"Entry {i}: Missing 'file' field")
                continue

            file_path = Path(file_path)
            if not file_path.is_absolute():
                file_path = path.resolve().parent / file_path

            # Check for nested metadata format
            metadata_dict = item.get("metadata")
            if metadata_dict and isinstance(metadata_dict, dict):
                payload = MetadataPayload.from_dict(metadata_dict)
            else:
                # Flat format: all non-special keys are metadata
                flat = {k: v for k, v in item.items() if k not in ("file", "path", "file_path", "priority", "tags")}
                if flat:
                    # Guess schema from key names
                    payload = _parse_flat_metadata(flat)
                else:
                    payload = MetadataPayload()

            entry = ManifestEntry(
                file_path=file_path,
                metadata=payload,
                priority=int(item.get("priority", 0)),
                tags=item.get("tags", []),
            )
            manifest.entries.append(entry)

    except json.JSONDecodeError as e:
        manifest.errors.append(f"JSON parse error: {e}")
    except Exception as e:
        manifest.errors.append(f"Failed to parse JSON manifest: {e}")

    return manifest


def parse_csv_manifest(path: Path) -> Manifest:
    """
    Parse a CSV manifest file.

    Expected format:
    ```csv
    file,Title,Artist,Description,Keywords
    video1.mp4,"My Title","Author","Description","tag1;tag2"
    video2.mov,"Another","Author2","","tag3"
    ```

    The first column must be the file path. All other columns are
    treated as metadata fields. The schema is auto-detected from
    column names.
    """
    manifest = Manifest(source_path=path)

    try:
        with open(path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)

            if not reader.fieldnames:
                manifest.errors.append("CSV has no header row")
                return manifest

            # Find the file path column
            file_col = None
            for col in reader.fieldnames:
                if col.lower() in ("file", "path", "file_path", "filepath", "filename"):
                    file_col = col
                    break

            if not file_col:
                file_col = reader.fieldnames[0]
                logger.info("Using first column '%s' as file path", file_col)

            metadata_cols = [c for c in reader.fieldnames if c != file_col]

            for i, row in enumerate(reader):
                file_path = row.get(file_col)
                if not file_path:
                    manifest.errors.append(f"Row {i + 2}: Empty file path")
                    continue

                flat = {}
                for col in metadata_cols:
                    value = (row.get(col) or "").strip()
                    if value:
                        # Handle semicolon-separated values as lists
                        if ";" in value:
                            flat[col] = [v.strip() for v in value.split(";") if v.strip()]
                        else:
                            flat[col] = value

                payload = _parse_flat_metadata(flat) if flat else MetadataPayload()

                entry = ManifestEntry(
                    file_path=(Path(file_path) if Path(file_path).is_absolute() else path.resolve().parent / file_path),
                    metadata=payload,
                )
                manifest.entries.append(entry)

    except Exception as e:
        manifest.errors.append(f"Failed to parse CSV manifest: {e}")

    return manifest


def parse_manifest(path: Path) -> Manifest:
    """Auto-detect manifest format and parse."""
    suffix = path.suffix.lower()
    if suffix == ".json":
        return parse_json_manifest(path)
    elif suffix == ".csv":
        return parse_csv_manifest(path)
    return Manifest(source_path=path, errors=["Manifest must have a .json or .csv extension"])


def build_manifest_from_directory(
    directory: Path,
    metadata: MetadataPayload,
    glob_pattern: str = "*",
    recursive: bool = True,
) -> Manifest:
    """
    Build a manifest by applying the same metadata to all matching
    files in a directory.
    """
    manifest = Manifest()

    from apex_injector.codec_manifest import EXTENSION_MAP

    pattern = f"**/{glob_pattern}" if recursive else glob_pattern
    for file_path in directory.glob(pattern):
        if file_path.is_file() and file_path.suffix.lower() in EXTENSION_MAP:
            manifest.entries.append(
                ManifestEntry(
                    file_path=file_path,
                    metadata=metadata,
                )
            )

    manifest.entries.sort(key=lambda e: str(e.file_path))
    return manifest


# ─────────────────────────────────────────────────────────────────────
# Schema detection helpers
# ─────────────────────────────────────────────────────────────────────


def _parse_flat_metadata(flat: dict[str, Any]) -> MetadataPayload:
    """Resolve explicit schema prefixes without losing the actual namespace."""
    payload = MetadataPayload()
    schema_aliases = {s.value: s for s in MetadataSchema}
    schema_aliases["id3"] = MetadataSchema.ID3V24
    for key, value in flat.items():
        prefix, separator, remainder = key.partition(":")
        if separator and prefix.lower() in schema_aliases:
            schema, tag = schema_aliases[prefix.lower()], remainder
        elif separator and prefix.lower() in ("dc", "photoshop", "iptc4xmpcore", "xmp-dc"):
            schema = MetadataSchema.XMP
            tag = "dc:" + remainder if prefix.lower() == "xmp-dc" else key
        elif separator:
            schema, tag = MetadataSchema.EXIFTOOL, key
        else:
            schema, tag = MetadataSchema.XMP, key
        payload.fields.append(MetadataField(schema=schema, key=tag, value=value))
    return payload
