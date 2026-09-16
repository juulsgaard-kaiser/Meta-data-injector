"""
Apex Meta-Injector — Codec Library Manifest.

Reference catalog of each codec to its container format(s), manipulation
library, supported metadata schemas, and injection strategy. The catalog identifies
codecs; executable capabilities come from the registered handlers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

logger = logging.getLogger(__name__)


class ManipulationStrategy(StrEnum):
    """How metadata is injected into a given codec/container combination."""

    ATOM_EDIT = "atom_edit"  # ISOBMFF atom-level manipulation (MP4/MOV)
    READ_ONLY = "read_only"
    KLV_HEADER = "klv_header"  # MXF KLV header partition edit
    EBML_EDIT = "ebml_edit"  # Matroska EBML property edit
    RIFF_CHUNK = "riff_chunk"  # RIFF/BWF chunk-level manipulation
    IFF_CHUNK = "iff_chunk"  # IFF/AIFF chunk manipulation
    EXIFTOOL = "exiftool"  # Fallback via ExifTool CLI


class ContainerFormat(StrEnum):
    """Supported container formats."""

    MP4 = "mp4"
    MOV = "mov"
    MXF = "mxf"
    MKV = "mkv"
    WAV = "wav"  # Includes BWF
    BWF = "bwf"
    AIFF = "aiff"


class MetadataSchema(StrEnum):
    """Supported metadata schemas."""

    EXIFTOOL = "exiftool"  # Explicit group-qualified tags handled by ExifTool
    XMP = "xmp"
    IPTC = "iptc"
    EXIF = "exif"
    ID3V24 = "id3v2.4"
    VORBIS = "vorbis_comments"
    SMPTE_377 = "smpte_st_377"
    IXML = "ixml"
    BEXT = "bext"
    MATROSKA_TAGS = "matroska_tags"


class ManipulationLibrary(StrEnum):
    """Backend library used for the actual manipulation."""

    PYMP4 = "pymp4"  # pymp4 + construct (ISOBMFF parsing)
    BMX = "bmx"  # bmxtranswrap CLI (MXF)
    MKVPROPEDIT = "mkvpropedit"  # mkvpropedit CLI (MKV)
    MUTAGEN = "mutagen"  # mutagen (AIFF, audio tags)
    WAVE_BWF_RF64 = "wave_bwf_rf64"  # wave-bwf-rf64 (BWF/WAV)
    STRUCT = "struct"  # Python struct (low-level RIFF)
    EXIFTOOL = "exiftool"  # ExifTool CLI (universal fallback)


class ComplexWrapRisk(StrEnum):
    """Risk level for requiring container re-wrapping."""

    NONE = "none"  # No container re-wrap required
    LOW = "low"  # Header edit possible but offset recalculation may be needed
    MEDIUM = "medium"  # May need atom relocation within container
    HIGH = "high"  # Likely requires full container re-wrap (MXF partition reindex)


@dataclass
class CodecContainerMapping:
    """A specific codec + container combination and how to handle it."""

    codec: str
    codec_fourcc: list[str]
    container: ContainerFormat
    library: ManipulationLibrary
    strategy: ManipulationStrategy
    supported_schemas: list[MetadataSchema]
    complex_wrap_risk: ComplexWrapRisk = ComplexWrapRisk.NONE
    notes: str = ""
    requires_tool: str | None = None  # External tool name from ToolPaths


@dataclass
class CodecFamily:
    """A codec family with its supported container mappings."""

    name: str
    category: str  # "professional_mezzanine" or "consumer_web"
    description: str
    mappings: list[CodecContainerMapping] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────
# The Master Manifest
# ─────────────────────────────────────────────────────────────────────

CODEC_MANIFEST: list[CodecFamily] = [
    # ── Professional Mezzanine ──────────────────────────────────────
    CodecFamily(
        name="Apple ProRes",
        category="professional_mezzanine",
        description="Apple ProRes 422/4444 family including LT, Proxy, HQ, 4444, and 4444 XQ",
        mappings=[
            CodecContainerMapping(
                codec="Apple ProRes",
                codec_fourcc=["ap4h", "ap4x", "apcn", "apcs", "apco", "aprh"],
                container=ContainerFormat.MOV,
                library=ManipulationLibrary.STRUCT,
                strategy=ManipulationStrategy.ATOM_EDIT,
                supported_schemas=[MetadataSchema.XMP],
                complex_wrap_risk=ComplexWrapRisk.LOW,
                notes="Native XMP packet edits preserve sample offsets. Additional tags require ExifTool.",
            ),
            CodecContainerMapping(
                codec="Apple ProRes",
                codec_fourcc=["ap4h", "ap4x", "apcn", "apcs", "apco", "aprh"],
                container=ContainerFormat.MXF,
                library=ManipulationLibrary.EXIFTOOL,
                strategy=ManipulationStrategy.READ_ONLY,
                supported_schemas=[],
                complex_wrap_risk=ComplexWrapRisk.HIGH,
                notes="Read-only. MXF writing and re-wrapping are not implemented.",
                requires_tool="exiftool",
            ),
        ],
    ),
    CodecFamily(
        name="Avid DNxHR/DNxHD",
        category="professional_mezzanine",
        description="Avid DNxHR (all profiles: LB, SQ, HQ, HQX, 444) and legacy DNxHD",
        mappings=[
            CodecContainerMapping(
                codec="Avid DNxHR/DNxHD",
                codec_fourcc=["AVdh", "AVdn"],
                container=ContainerFormat.MOV,
                library=ManipulationLibrary.STRUCT,
                strategy=ManipulationStrategy.ATOM_EDIT,
                supported_schemas=[MetadataSchema.XMP],
                complex_wrap_risk=ComplexWrapRisk.LOW,
            ),
            CodecContainerMapping(
                codec="Avid DNxHR/DNxHD",
                codec_fourcc=["AVdh", "AVdn"],
                container=ContainerFormat.MXF,
                library=ManipulationLibrary.EXIFTOOL,
                strategy=ManipulationStrategy.READ_ONLY,
                supported_schemas=[],
                complex_wrap_risk=ComplexWrapRisk.HIGH,
                notes="Read-only, including OP-Atom. No conversion to OP-1a.",
                requires_tool="exiftool",
            ),
        ],
    ),
    CodecFamily(
        name="Sony XAVC",
        category="professional_mezzanine",
        description="Sony XAVC / XAVC-S / XAVC-I codec family",
        mappings=[
            CodecContainerMapping(
                codec="Sony XAVC",
                codec_fourcc=["xvc1", "xvci"],
                container=ContainerFormat.MXF,
                library=ManipulationLibrary.EXIFTOOL,
                strategy=ManipulationStrategy.READ_ONLY,
                supported_schemas=[],
                complex_wrap_risk=ComplexWrapRisk.HIGH,
                requires_tool="exiftool",
            ),
            CodecContainerMapping(
                codec="Sony XAVC",
                codec_fourcc=["avc1"],
                container=ContainerFormat.MP4,
                library=ManipulationLibrary.STRUCT,
                strategy=ManipulationStrategy.ATOM_EDIT,
                supported_schemas=[MetadataSchema.XMP],
                complex_wrap_risk=ComplexWrapRisk.LOW,
            ),
        ],
    ),
    CodecFamily(
        name="Panasonic AVC-Intra",
        category="professional_mezzanine",
        description="Panasonic AVC-Intra 50/100/200/4:4:4",
        mappings=[
            CodecContainerMapping(
                codec="AVC-Intra",
                codec_fourcc=["ai55", "ai15", "ai12", "ai13"],
                container=ContainerFormat.MXF,
                library=ManipulationLibrary.EXIFTOOL,
                strategy=ManipulationStrategy.READ_ONLY,
                supported_schemas=[],
                complex_wrap_risk=ComplexWrapRisk.HIGH,
                requires_tool="exiftool",
            ),
        ],
    ),
    # ── Consumer / Web ──────────────────────────────────────────────
    CodecFamily(
        name="H.264/AVC",
        category="consumer_web",
        description="ITU-T H.264 / MPEG-4 Part 10 Advanced Video Coding",
        mappings=[
            CodecContainerMapping(
                codec="H.264/AVC",
                codec_fourcc=["avc1", "avc3"],
                container=ContainerFormat.MP4,
                library=ManipulationLibrary.STRUCT,
                strategy=ManipulationStrategy.ATOM_EDIT,
                supported_schemas=[MetadataSchema.XMP],
                complex_wrap_risk=ComplexWrapRisk.LOW,
            ),
            CodecContainerMapping(
                codec="H.264/AVC",
                codec_fourcc=["avc1", "avc3"],
                container=ContainerFormat.MOV,
                library=ManipulationLibrary.STRUCT,
                strategy=ManipulationStrategy.ATOM_EDIT,
                supported_schemas=[MetadataSchema.XMP],
                complex_wrap_risk=ComplexWrapRisk.LOW,
            ),
            CodecContainerMapping(
                codec="H.264/AVC",
                codec_fourcc=["V_MPEG4/ISO/AVC"],
                container=ContainerFormat.MKV,
                library=ManipulationLibrary.MKVPROPEDIT,
                strategy=ManipulationStrategy.EBML_EDIT,
                supported_schemas=[MetadataSchema.MATROSKA_TAGS],
                complex_wrap_risk=ComplexWrapRisk.NONE,
                requires_tool="mkvpropedit",
            ),
        ],
    ),
    CodecFamily(
        name="H.265/HEVC",
        category="consumer_web",
        description="ITU-T H.265 / MPEG-H Part 2 High Efficiency Video Coding",
        mappings=[
            CodecContainerMapping(
                codec="H.265/HEVC",
                codec_fourcc=["hvc1", "hev1"],
                container=ContainerFormat.MP4,
                library=ManipulationLibrary.STRUCT,
                strategy=ManipulationStrategy.ATOM_EDIT,
                supported_schemas=[MetadataSchema.XMP],
                complex_wrap_risk=ComplexWrapRisk.LOW,
            ),
            CodecContainerMapping(
                codec="H.265/HEVC",
                codec_fourcc=["hvc1", "hev1"],
                container=ContainerFormat.MOV,
                library=ManipulationLibrary.STRUCT,
                strategy=ManipulationStrategy.ATOM_EDIT,
                supported_schemas=[MetadataSchema.XMP],
                complex_wrap_risk=ComplexWrapRisk.LOW,
            ),
            CodecContainerMapping(
                codec="H.265/HEVC",
                codec_fourcc=["V_MPEGH/ISO/HEVC"],
                container=ContainerFormat.MKV,
                library=ManipulationLibrary.MKVPROPEDIT,
                strategy=ManipulationStrategy.EBML_EDIT,
                supported_schemas=[MetadataSchema.MATROSKA_TAGS],
                complex_wrap_risk=ComplexWrapRisk.NONE,
                requires_tool="mkvpropedit",
            ),
        ],
    ),
    CodecFamily(
        name="AV1",
        category="consumer_web",
        description="AOMedia Video 1 — next-gen open codec",
        mappings=[
            CodecContainerMapping(
                codec="AV1",
                codec_fourcc=["av01"],
                container=ContainerFormat.MP4,
                library=ManipulationLibrary.STRUCT,
                strategy=ManipulationStrategy.ATOM_EDIT,
                supported_schemas=[MetadataSchema.XMP],
                complex_wrap_risk=ComplexWrapRisk.LOW,
            ),
            CodecContainerMapping(
                codec="AV1",
                codec_fourcc=["V_AV1"],
                container=ContainerFormat.MKV,
                library=ManipulationLibrary.MKVPROPEDIT,
                strategy=ManipulationStrategy.EBML_EDIT,
                supported_schemas=[MetadataSchema.MATROSKA_TAGS],
                complex_wrap_risk=ComplexWrapRisk.NONE,
                requires_tool="mkvpropedit",
            ),
        ],
    ),
    CodecFamily(
        name="VP9",
        category="consumer_web",
        description="Google VP9 video codec",
        mappings=[
            CodecContainerMapping(
                codec="VP9",
                codec_fourcc=["vp09", "V_VP9"],
                container=ContainerFormat.MKV,
                library=ManipulationLibrary.MKVPROPEDIT,
                strategy=ManipulationStrategy.EBML_EDIT,
                supported_schemas=[MetadataSchema.MATROSKA_TAGS, MetadataSchema.VORBIS],
                complex_wrap_risk=ComplexWrapRisk.NONE,
                requires_tool="mkvpropedit",
            ),
            CodecContainerMapping(
                codec="VP9",
                codec_fourcc=["vp09"],
                container=ContainerFormat.MP4,
                library=ManipulationLibrary.STRUCT,
                strategy=ManipulationStrategy.ATOM_EDIT,
                supported_schemas=[MetadataSchema.XMP],
                complex_wrap_risk=ComplexWrapRisk.LOW,
            ),
        ],
    ),
    CodecFamily(
        name="MPEG-D USAC",
        category="consumer_web",
        description="MPEG-D Unified Speech and Audio Coding (xHE-AAC)",
        mappings=[
            CodecContainerMapping(
                codec="MPEG-D USAC",
                codec_fourcc=["mp4a"],
                container=ContainerFormat.MP4,
                library=ManipulationLibrary.STRUCT,
                strategy=ManipulationStrategy.ATOM_EDIT,
                supported_schemas=[MetadataSchema.XMP],
                complex_wrap_risk=ComplexWrapRisk.LOW,
            ),
        ],
    ),
    # ── Audio Containers ────────────────────────────────────────────
    CodecFamily(
        name="PCM/BWF (Broadcast Wave)",
        category="audio",
        description="Linear PCM in BWF/WAV containers with broadcast extension chunks",
        mappings=[
            CodecContainerMapping(
                codec="PCM",
                codec_fourcc=["0x0001"],  # WAVE_FORMAT_PCM
                container=ContainerFormat.WAV,
                library=ManipulationLibrary.STRUCT,
                strategy=ManipulationStrategy.RIFF_CHUNK,
                supported_schemas=[
                    MetadataSchema.BEXT,
                    MetadataSchema.IXML,
                    MetadataSchema.XMP,
                    MetadataSchema.ID3V24,
                ],
                complex_wrap_risk=ComplexWrapRisk.NONE,
                notes="Streaming RIFF chunk read/write. RF64 writing is not supported.",
            ),
            CodecContainerMapping(
                codec="PCM",
                codec_fourcc=["0x0001"],
                container=ContainerFormat.BWF,
                library=ManipulationLibrary.STRUCT,
                strategy=ManipulationStrategy.RIFF_CHUNK,
                supported_schemas=[
                    MetadataSchema.BEXT,
                    MetadataSchema.IXML,
                    MetadataSchema.XMP,
                    MetadataSchema.ID3V24,
                ],
                complex_wrap_risk=ComplexWrapRisk.NONE,
            ),
        ],
    ),
    CodecFamily(
        name="PCM/AIFF",
        category="audio",
        description="Linear PCM in AIFF/AIFF-C containers",
        mappings=[
            CodecContainerMapping(
                codec="PCM",
                codec_fourcc=["NONE", "sowt", "twos"],
                container=ContainerFormat.AIFF,
                library=ManipulationLibrary.MUTAGEN,
                strategy=ManipulationStrategy.IFF_CHUNK,
                supported_schemas=[MetadataSchema.ID3V24],
                complex_wrap_risk=ComplexWrapRisk.NONE,
                notes="Mutagen handles IFF chunk structure and ID3v2.4 injection.",
            ),
        ],
    ),
]


# ─────────────────────────────────────────────────────────────────────
# Lookup Functions
# ─────────────────────────────────────────────────────────────────────

# Magic bytes for container identification
CONTAINER_MAGIC: dict[ContainerFormat, list[tuple[int, bytes]]] = {
    ContainerFormat.MP4: [
        (4, b"ftyp"),  # ISO Base Media File Format
    ],
    ContainerFormat.MOV: [
        (4, b"ftyp"),  # Also starts with ftyp; disambiguate via brand
        (4, b"moov"),  # Legacy MOV
        (4, b"mdat"),  # Legacy MOV
    ],
    ContainerFormat.MXF: [
        (0, b"\x06\x0e\x2b\x34"),  # SMPTE UL prefix
    ],
    ContainerFormat.MKV: [
        (0, b"\x1a\x45\xdf\xa3"),  # EBML header
    ],
    ContainerFormat.WAV: [
        (0, b"RIFF"),  # RIFF header; check for WAVE at offset 8
    ],
    ContainerFormat.BWF: [
        (0, b"RIFF"),  # Same as WAV; differentiated by bext chunk presence
    ],
    ContainerFormat.AIFF: [
        (0, b"FORM"),  # IFF FORM header; check for AIFF/AIFC at offset 8
    ],
}

# File extension → container format mapping
EXTENSION_MAP: dict[str, ContainerFormat] = {
    ".mp4": ContainerFormat.MP4,
    ".m4v": ContainerFormat.MP4,
    ".m4a": ContainerFormat.MP4,
    ".mov": ContainerFormat.MOV,
    ".qt": ContainerFormat.MOV,
    ".mxf": ContainerFormat.MXF,
    ".mkv": ContainerFormat.MKV,
    ".mka": ContainerFormat.MKV,
    ".webm": ContainerFormat.MKV,  # WebM is Matroska subset
    ".wav": ContainerFormat.WAV,
    ".bwf": ContainerFormat.BWF,
    ".aif": ContainerFormat.AIFF,
    ".aiff": ContainerFormat.AIFF,
    ".aifc": ContainerFormat.AIFF,
}

# ISOBMFF ftyp brands to disambiguate MP4 vs MOV
MOV_BRANDS = {b"qt  ", b"MSNV"}
MP4_BRANDS = {b"isom", b"iso2", b"iso5", b"iso6", b"mp41", b"mp42", b"M4V ", b"M4A ", b"dash", b"avc1"}


def detect_container(file_path: Path) -> ContainerFormat | None:
    """
    Detect container format using magic bytes and file extension.

    Reads the first 16 bytes of the file to identify the container.
    Falls back to file extension if magic bytes are ambiguous.
    """
    ext = file_path.suffix.lower()
    ext_format = EXTENSION_MAP.get(ext)

    try:
        with open(file_path, "rb") as f:
            header = f.read(16)
    except OSError as e:
        logger.warning("Cannot read header of %s: %s", file_path, e)
        return ext_format

    if not header or len(header) < 8:
        return ext_format

    # MXF — SMPTE UL prefix
    if header[:4] == b"\x06\x0e\x2b\x34":
        return ContainerFormat.MXF

    # EBML — Matroska/WebM
    if header[:4] == b"\x1a\x45\xdf\xa3":
        return ContainerFormat.MKV

    # RIFF — WAV/BWF
    if header[:4] == b"RIFF" and len(header) >= 12 and header[8:12] == b"WAVE":
        # BWF is detected later by checking for bext chunk
        return ext_format if ext_format in (ContainerFormat.WAV, ContainerFormat.BWF) else ContainerFormat.WAV

    # FORM — AIFF
    if header[:4] == b"FORM" and len(header) >= 12 and header[8:12] in (b"AIFF", b"AIFC"):
        return ContainerFormat.AIFF

    # ISOBMFF — MP4/MOV (ftyp box)
    if len(header) >= 8 and header[4:8] == b"ftyp":
        brand = header[8:12] if len(header) >= 12 else b""
        if brand in MOV_BRANDS:
            return ContainerFormat.MOV
        if brand in MP4_BRANDS:
            return ContainerFormat.MP4
        # Fallback to extension
        if ext_format in (ContainerFormat.MP4, ContainerFormat.MOV):
            return ext_format
        return ContainerFormat.MP4  # Default ISOBMFF → MP4

    return ext_format


def find_mapping(
    container: ContainerFormat,
    codec_fourcc: str | None = None,
) -> CodecContainerMapping | None:
    """
    Find the best codec-container mapping for a detected file.

    If codec_fourcc is not provided, returns the first mapping
    matching the container format.
    """
    for family in CODEC_MANIFEST:
        for mapping in family.mappings:
            if mapping.container != container:
                continue
            if codec_fourcc is None:
                return mapping
            if codec_fourcc in mapping.codec_fourcc:
                return mapping
    return None


def find_all_mappings(container: ContainerFormat) -> list[CodecContainerMapping]:
    """Find all codec mappings for a given container format."""
    results = []
    for family in CODEC_MANIFEST:
        for mapping in family.mappings:
            if mapping.container == container:
                results.append(mapping)
    return results


def get_manifest_summary() -> list[dict]:
    """Return a JSON-serializable summary of the entire codec manifest."""
    from apex_injector.engine import InjectionEngine
    from apex_injector.handlers import get_handler

    InjectionEngine()  # Register all handlers before reporting current capabilities.
    summary = []
    for family in CODEC_MANIFEST:
        entry = {
            "name": family.name,
            "category": family.category,
            "description": family.description,
            "mappings": [],
        }
        for m in family.mappings:
            handler = get_handler(m.container)
            schemas = handler.supported_schemas if handler else []
            entry["mappings"].append(
                {
                    "codec": m.codec,
                    "container": m.container.value,
                    "library": (
                        "struct" if m.container in (ContainerFormat.MP4, ContainerFormat.MOV) else m.library.value
                    ),
                    "strategy": m.strategy.value,
                    "schemas": [s.value for s in schemas],
                    "writable": bool(schemas),
                    "validation": "Container-based support; individual codec variants require validation",
                    "complex_wrap_risk": m.complex_wrap_risk.value,
                    "requires_tool": m.requires_tool,
                    "notes": m.notes,
                }
            )
        summary.append(entry)
    return summary
