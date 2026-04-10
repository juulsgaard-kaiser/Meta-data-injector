# Apex Meta-Injector

> **High-speed batch metadata injection for professional and consumer media containers.**
> Header-only / atom-only manipulation — no bitstream re-encoding. Ever.

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D6.svg)](https://www.microsoft.com/windows)

---

## What It Does

Apex Meta-Injector writes metadata into media file headers **without touching the bitstream**. It performs atomic, Stage-Verify-Commit transactions using Win32 API calls (`ReplaceFileW`, `LockFileEx`) so your media essence is never at risk.

### Supported Formats

| Category | Codecs / Containers |
|----------|-------------------|
| **Professional Mezzanine** | Apple ProRes (all profiles incl. 4444 XQ), Avid DNxHR/DNxHD, Sony XAVC, Panasonic AVC-Intra |
| **Consumer / Web** | H.264/AVC, H.265/HEVC, AV1, VP9, MPEG-D USAC |
| **Containers** | MOV, MP4, MXF (OP-1a, OP-Atom), MKV, BWF (Broadcast Wave), AIFF |
| **Metadata Schemas** | XMP, IPTC, EXIF, ID3v2.4, Vorbis Comments, SMPTE ST 377 (MXF) |

### Key Features

- **Atomic transactions** — Stage → Verify bitstream → Commit via `ReplaceFileW`
- **Thread-pooled batch processing** — auto-tuned worker count for NVMe throughput
- **Complex Wrap safeguard** — MXF re-wrapping flagged for user approval
- **File-in-use retry** — exponential backoff for locked files
- **Bitstream integrity verification** — xxHash3-128 essence hashing
- **Desktop GUI** — dark-themed native Windows app (pywebview + FastAPI)
- **CLI mode** — scriptable batch injection from JSON/CSV manifests

---

## Installation

### Prerequisites

- **Python 3.11+** on Windows 10/11
- **ExifTool** — [exiftool.org](https://exiftool.org/) (rename to `exiftool.exe`, add to PATH)
- **MKVToolNix** — [mkvtoolnix.download](https://mkvtoolnix.download/) (for MKV support)
- **bmx tools** — [github.com/bbc/bmx](https://github.com/bbc/bmx) (for MXF support, optional)

### From Source

```bash
git clone https://github.com/juulsgaard-kaiser/Meta-data-injector.git
cd Meta-data-injector
pip install -e .
```

### Verify External Tools

```bash
python setup_tools.py
```

---

## Usage

### GUI Mode (Default)

```bash
python -m apex_injector
# or
apex-injector --gui
```

Launches a native Windows desktop application with:
- **Dashboard** — system status, tool health, quick actions
- **Inject** — drag-and-drop file selection + metadata editor
- **Batch** — JSON/CSV manifest processing with live progress
- **Scan** — directory analysis with CSV export
- **Settings** — tool paths, thread pool, backup/verify toggles

### CLI Mode

```bash
# Single file injection
apex-injector inject video.mp4 --title "My Title" --artist "Author"

# Batch from manifest
apex-injector batch manifest.json

# Directory scan
apex-injector scan ./media --recursive --export results.csv
```

### Manifest Format (JSON)

```json
[
  {
    "file": "video.mp4",
    "metadata": {
      "xmp": {
        "dc:Title": "My Title",
        "dc:Creator": "Author Name"
      },
      "iptc": {
        "Keywords": ["tag1", "tag2"]
      }
    }
  }
]
```

### Manifest Format (CSV)

```csv
file,Title,Artist,Keywords
video1.mp4,"My Title","Author","tag1;tag2"
video2.mov,"Another","Author2","tag3"
```

---

## Architecture

```
apex_injector/
├── __main__.py          # Entry point (CLI/GUI routing)
├── config.py            # Configuration system
├── codec_manifest.py    # Codec ↔ container ↔ schema mapping
├── engine.py            # Core injection engine
├── manifest.py          # JSON/CSV manifest parser
├── thread_pool.py       # Windows-optimized thread pool
├── win32_io.py          # Win32 API (atomic replace, file locking)
├── handlers/
│   ├── __init__.py      # Handler base class + registry
│   ├── isobmff_handler.py   # MP4/MOV (pymp4 + construct)
│   ├── mxf_handler.py       # MXF (bmxtranswrap)
│   ├── matroska_handler.py  # MKV (mkvpropedit)
│   ├── bwf_handler.py       # BWF/WAV (RIFF chunk)
│   ├── aiff_handler.py      # AIFF (mutagen)
│   └── exiftool_bridge.py   # Universal fallback (ExifTool)
└── gui/
    ├── __init__.py      # pywebview launcher
    ├── server.py        # FastAPI backend + WebSocket
    └── static/          # Frontend SPA (HTML/CSS/JS)
```

### Transaction Workflow

```
1. STAGE   → Copy file to staging path (same volume)
2. INJECT  → Write metadata into staging copy
3. VERIFY  → xxHash3-128 the essence region (original vs staged)
4. COMMIT  → ReplaceFileW (atomic swap with optional backup)
5. FAIL?   → Rollback (delete staging file, original untouched)
```

---

## Building the Executable

```bash
pip install -e ".[build]"
pyinstaller apex_injector.spec --noconfirm
```

Output: `dist/ApexMetaInjector/ApexMetaInjector.exe`

---

## Development

```bash
pip install -e ".[dev]"

# Run audit
python tests/audit.py

# Run tests
pytest
```

---

## License

MIT — see [LICENSE](LICENSE).
