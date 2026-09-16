# Changelog

All notable changes to Apex Meta-Injector will be documented in this file.

## [0.0.2] - 2026-09-16

### Fixed

- Fail-closed staged transactions, source locking, source-change detection, unique
  backups and accurate results after verification and commit failures.
- MP4/MOV XMP growth/shrink corruption and media verification after relocation.
- Streaming MP4/WAV writes; preservation of unrelated XML, RIFF chunks, BEXT bytes
  and Matroska tags. WAV ID3 writes and AIFF save errors are handled correctly.
- Unsupported/no-op fields no longer count as successful writes. MXF and RF64
  writes are explicitly disabled instead of unsafe re-wrapping or false success.
- CLI routing, failure exit codes, CSV export, relative manifest paths and duplicate
  rejection. Batch errors and cancellation retain accurate final results.
- Desktop full-path selection, persistent settings, batch isolation, escaped
  metadata display and packaged tool-status imports.

### Added

- Deep ExifTool inventory with embedded/duplicate/unknown tags and explicit
  group-qualified edits, structured JSON import and metadata readback.
- Visible per-field risks, high-risk acknowledgement, read-only indicators and
  warnings when backup or verification protection is disabled.
- Regression and real-media integration tests, an optional browser test, Linux /
  Windows CI, wheel checks and a separate console executable in Windows builds.

### Status

- Alpha: container-level support does not certify every codec/camera/NLE workflow.
- Rotation editing remains deferred. MXF is read-only; RF64 writing is unsupported.
- Earlier 0.0.1 feature descriptions below describe initial claims, several of which
  were incomplete. The README's current capability table supersedes those claims.

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
