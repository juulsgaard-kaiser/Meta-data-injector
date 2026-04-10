# Changelog

All notable changes to Apex Meta-Injector will be documented in this file.

## [0.0.1] - 2026-04-10

### Added

- **Core Engine** — Atomic Stage-Verify-Commit injection pipeline
- **Win32 I/O** — `ReplaceFileW` atomic replace, `LockFileEx` file locking, long path support
- **Container Handlers**
  - ISOBMFF (MP4/MOV) — native atom parsing via pymp4/construct
  - MXF — bmxtranswrap re-wrapping with Complex Wrap safeguard
  - Matroska (MKV) — mkvpropedit property editing
  - BWF/WAV — native RIFF chunk manipulation
  - AIFF — mutagen-based metadata injection
  - ExifTool Bridge — universal fallback handler
- **Metadata Schema Support** — XMP, IPTC, EXIF, ID3v2.4, Vorbis Comments, SMPTE ST 377
- **Codec Manifest** — 11 codec families, 21 container mappings with full schema compatibility matrix
- **Batch Processing** — JSON and CSV manifest parsing with thread-pooled execution
- **Bitstream Verification** — xxHash3-128 essence region hashing to ensure zero re-encoding
- **Desktop GUI** — Dark-themed native Windows app (pywebview + Edge WebView2)
  - Dashboard with system status and tool health
  - Drag-and-drop file injection with metadata editor
  - Batch manifest processing with live WebSocket progress
  - Directory scanning with CSV export
  - Settings panel for tool paths and engine configuration
- **CLI Interface** — Full command-line mode for scripted workflows
- **PyInstaller Build** — Single-directory Windows executable packaging
- **Tool Validation** — External tool detection and health reporting (ExifTool, MKVToolNix, bmx)
