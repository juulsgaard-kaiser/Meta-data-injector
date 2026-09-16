"""
Apex Meta-Injector — Container Handler Base.

Defines the abstract base class and common types for all container handlers.
Each handler implements format-specific metadata read/write operations
while adhering to the header-only manipulation constraint.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from apex_injector.codec_manifest import (
    CodecContainerMapping,
    ContainerFormat,
    MetadataSchema,
)

logger = logging.getLogger(__name__)


class InjectionStatus(StrEnum):
    """Result status for a metadata injection operation."""

    SUCCESS = "success"
    PARTIAL = "partial"  # Some fields written, others failed
    COMPLEX_WRAP_REQUIRED = "complex_wrap_required"  # Needs user permission
    HEADER_CORRUPT = "header_corrupt"
    UNSUPPORTED_FIELD = "unsupported_field"
    FAILED = "failed"


@dataclass
class MetadataField:
    """A single metadata field to inject."""

    schema: MetadataSchema
    key: str  # e.g. "dc:Title", "IPTC:Keywords", "EXIF:Artist"
    value: Any  # str, list[str], int, float, datetime
    namespace: str = ""  # Optional namespace URI for XMP


@dataclass
class MetadataPayload:
    """A collection of metadata fields to inject into a file."""

    fields: list[MetadataField] = field(default_factory=list)
    acknowledge_risk: bool = False

    def get_by_schema(self, schema: MetadataSchema) -> list[MetadataField]:
        """Get all fields for a specific metadata schema."""
        return [f for f in self.fields if f.schema == schema]

    def has_schema(self, schema: MetadataSchema) -> bool:
        return any(f.schema == schema for f in self.fields)

    def to_dict(self) -> dict[str, dict[str, Any]]:
        """Convert to a nested dict grouped by schema."""
        result: dict[str, dict[str, Any]] = {}
        for f in self.fields:
            schema_key = f.schema.value
            if schema_key not in result:
                result[schema_key] = {}
            result[schema_key][f.key] = f.value
        return result

    @classmethod
    def from_dict(cls, data: dict[str, dict[str, Any]]) -> MetadataPayload:
        """Create from a nested dict: {schema: {key: value}}."""
        if not isinstance(data, dict):
            raise ValueError("Metadata must be an object")
        payload = cls()
        for schema_str, fields in data.items():
            try:
                schema = MetadataSchema(schema_str)
            except ValueError:
                raise ValueError(f"Unknown metadata schema: {schema_str}") from None
            if not isinstance(fields, dict):
                raise ValueError(f"Metadata for {schema_str} must be an object")
            for key, value in fields.items():
                payload.fields.append(
                    MetadataField(
                        schema=schema,
                        key=key,
                        value=value,
                    )
                )
        return payload


@dataclass
class InjectionResult:
    """Result of a metadata injection operation on a single file."""

    file_path: Path
    status: InjectionStatus
    fields_written: int = 0
    fields_failed: int = 0
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    backup_path: Path | None = None
    duration_ms: float = 0.0
    complex_wrap_info: str | None = None


@dataclass
class FileAnalysis:
    """Analysis of a media file's container, codec, and current metadata."""

    file_path: Path
    file_size: int = 0
    container: ContainerFormat | None = None
    codec: str = ""
    codec_fourcc: str = ""
    mapping: CodecContainerMapping | None = None
    current_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    injectable: bool = False
    complex_wrap_risk: str = "none"
    error: str | None = None


class ContainerHandler(ABC):
    """
    Abstract base class for container-specific metadata handlers.

    Each handler implements reading and writing metadata for a specific
    container format while ensuring the bitstream is never modified.
    """

    @property
    @abstractmethod
    def supported_containers(self) -> list[ContainerFormat]:
        """Container formats this handler supports."""
        ...

    @property
    @abstractmethod
    def supported_schemas(self) -> list[MetadataSchema]:
        """Metadata schemas this handler can write."""
        ...

    @abstractmethod
    def analyze(self, file_path: Path) -> FileAnalysis:
        """
        Analyze a file to determine its codec, current metadata,
        and injection compatibility.
        """
        ...

    @abstractmethod
    def read_metadata(self, file_path: Path) -> dict[str, dict[str, Any]]:
        """
        Read all metadata from a file.

        Returns a dict grouped by schema:
        {
            "xmp": {"dc:Title": "...", ...},
            "iptc": {"Keywords": [...], ...},
            ...
        }
        """
        ...

    @abstractmethod
    def inject(
        self,
        file_path: Path,
        payload: MetadataPayload,
        staging_path: Path,
    ) -> InjectionResult:
        """
        Inject metadata into a file.

        Must write the result to staging_path, NOT to file_path directly.
        The caller (engine) handles the atomic replace via StagedTransaction.

        Parameters:
            file_path: Source file to read from
            payload: Metadata to inject
            staging_path: Path to write the modified file

        Returns:
            InjectionResult with status and details
        """
        ...

    def get_essence_region(self, file_path: Path) -> tuple[int, int]:
        """
        Get the offset and length of the essence (bitstream) data.

        Used by the engine for bitstream integrity verification.
        Returns (offset, length). Handlers without native region tracking
        use packet-based verification instead.
        """
        raise ValueError("This container requires packet-based verification")

    def get_essence_regions(self, file_path):
        return [self.get_essence_region(file_path)]

    def essence_fingerprint(self, file_path):
        from apex_injector.verification import packet_fingerprint

        return packet_fingerprint(file_path)

    def validate(self, file_path):
        from apex_injector.verification import packet_fingerprint

        packet_fingerprint(file_path)

    def supports_schema(self, schema: MetadataSchema) -> bool:
        """Check if this handler supports a specific metadata schema."""
        return schema in self.supported_schemas

    def filter_payload(self, payload: MetadataPayload) -> MetadataPayload:
        """Filter a payload to only include schemas this handler supports."""
        supported = MetadataPayload()
        for f in payload.fields:
            if self.supports_schema(f.schema):
                supported.fields.append(f)
        return supported


# Handler registry for dynamic dispatch
_handler_registry: dict[ContainerFormat, ContainerHandler] = {}


def register_handler(handler: ContainerHandler):
    """Register a handler for its supported container formats."""
    for container in handler.supported_containers:
        _handler_registry[container] = handler
        logger.debug("Registered handler for %s: %s", container.value, type(handler).__name__)


def get_handler(container: ContainerFormat) -> ContainerHandler | None:
    """Get the registered handler for a container format."""
    return _handler_registry.get(container)


def get_all_handlers() -> dict[ContainerFormat, ContainerHandler]:
    """Get all registered handlers."""
    return dict(_handler_registry)
