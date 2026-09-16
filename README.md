# Apex Meta-Injector

Inspect and edit media metadata through a Windows desktop app or a scriptable CLI.
**Version 0.0.2 is alpha software.** It uses staged writes, backups and integrity checks;
production camera formats and editing applications still need workflow-specific validation.

## Current capabilities

| Container | Implemented writes | Requirements / limits |
|---|---|---|
| MP4 / MOV | Native XMP; additional ExifTool-supported tags | Native writes preserve sample offsets. Additional tags require ExifTool + FFprobe. |
| WAV / BWF | BEXT, simple iXML fields, XMP, ID3 text | RIFF only; RF64 writing is rejected. ExifTool provides inspection only. |
| AIFF / AIFF-C | ID3 text | ExifTool provides inspection only. |
| MKV / MKA / WebM | Matroska title, date and global text tags | MKVToolNix (`mkvpropedit`, `mkvextract`, `mkvmerge`) + FFprobe. Existing unrelated tags are retained. |
| MXF | Read-only inspection | ExifTool for metadata. No MXF writing or re-wrapping. |

Support is determined by the **container and requested tags**, not simply by a codec name.
The codec catalog is an identification reference, not a certification of every ProRes,
DNx, XAVC or other variant. Unsupported fields fail the whole file operation instead
of being silently skipped. Mixed schemas must be supported by one writer.

### Deep inspection and edit risks

The **Inspect & Edit Metadata** page can use ExifTool to list embedded, duplicate,
unknown and structured tags with group/document identifiers. Filter the inventory and
add supported main-document tags to advanced edits. Embedded/duplicate tags and
unsupported containers are shown as read-only.

Reading a tag does not imply it can be written. Proprietary or opaque metadata may
remain unreadable or uneditable, and this app does not promise access to all metadata.
ExifTool may reject a tag based on the specific file even when its group is supported.

Before writing, the app displays a risk description for each field. Technical or
unfamiliar fields require explicit acknowledgement. Timing, dates and loudness have
specific warnings; filesystem/computed fields are blocked. CLI users acknowledge
higher-risk edits with `--accept-risk`. Acknowledgement never bypasses writer or
integrity checks. Rotation editing remains deferred.

### What protects files

1. Lock the source and serialize application transactions for its canonical path.
2. Write to a temporary file in the same directory.
3. Validate the container and compare media fingerprints before committing.
4. Check that the source has not changed and create a unique backup by default.
5. Replace the original using `ReplaceFileW` on Windows (`os.replace` on Linux).

Failed writes, verification failures and backup failures do not report success.
Earlier backups are retained. Cancellation finishes active file transactions and
cancels queued files. Native MP4/WAV processing streams media instead of loading
entire files into RAM.

Native checks hash media regions and selected structural fields. External writers
use FFprobe packet hashes, timing and codec parameters. These checks do not prove
that every player or NLE will interpret all metadata identically. Keep backups and
validate representative files in your actual workflow. Staging and backups require
additional disk space; automatic workers are capped conservatively at four.

## Installation

Python 3.11+ is required. The native desktop app targets Windows 10/11 with Edge
WebView2. The CLI and browser-based development UI also run on Linux.

```bash
git clone https://github.com/juulsgaard-kaiser/Meta-data-injector.git
cd Meta-data-injector
pip install -e .
python setup_tools.py
```

Install tools for the features you use, and configure their executable paths in
**Settings** or place them on `PATH`:

- [ExifTool](https://exiftool.org/) for deep inspection and additional writable tags.
  Keep its supporting files with the Windows executable; name it `exiftool.exe`.
- [FFprobe (FFmpeg)](https://ffmpeg.org/download.html) for external-writer integrity checks.
- [MKVToolNix](https://mkvtoolnix.download/) for Matroska editing.

Native XMP in MP4/MOV, RIFF metadata and AIFF ID3 do not require these executables.
FFmpeg is used to generate test fixtures. The legacy bmx setting is unused; MXF
writing is disabled.

## Usage

```bash
# Native desktop app
python -m apex_injector

# CLI subcommands route directly to the CLI
apex-injector inject video.mp4 --title "My title" --artist "Author"
apex-injector verify video.mp4 --deep
apex-injector batch manifest.json
apex-injector scan ./media --recursive --export results.csv

# Advanced group-qualified tag (ExifTool + FFprobe required)
apex-injector inject video.mov --meta "exiftool:QuickTime:CreateDate=2026:09:16 12:00:00" --accept-risk
```

In the desktop app, **Browse files** supplies full paths. When running the UI in a
browser, paste full file paths. **Preview risks** shows warnings before applying
changes; results include any error and the backup location. Settings persist across
restarts. Use `--help` on any CLI command for available options. `verify` displays
metadata; it is not a comparison against an earlier original file.

### JSON manifest

Paths are relative to the manifest's directory unless absolute. Schema names are
explicit and unknown schemas are rejected. Duplicate paths are rejected.

```json
[
  {
    "file": "video.mp4",
    "metadata": {
      "xmp": {
        "dc:Title": "My title",
        "dc:Creator": ["Author"],
        "dc:Subject": ["tag1", "tag2"]
      }
    }
  }
]
```

### CSV manifest

```csv
file,XMP:dc:Title,XMP:dc:Creator,XMP:dc:Subject
video1.mp4,My title,Author,tag1;tag2
video2.mov,Another title,Author,tag3
```

Advanced JSON accepts `exiftool` with explicit groups, for example
`{"exiftool":{"XMP-dc:Subject":["one","two"]}}`. Empty values and deletion are not
uniformly supported by writers; do not use them as a general metadata-removal API.

## Build and development

```bash
pip install -e ".[dev,build]"
pytest -ra
ruff check .
ruff format --check .
python -m build
pyinstaller apex_injector.spec --noconfirm
```

The Windows build produces `dist/ApexMetaInjector/ApexMetaInjector.exe` and
`ApexMetaInjectorCLI.exe` in the same folder. External media tools are not bundled.

Integration tests require FFmpeg, FFprobe, ExifTool and MKVToolNix. Without them,
tool-dependent tests are skipped. CI requires these tools and tests Linux and
native Windows I/O, wheel installation, and Windows executable packaging.
For the optional browser test:

```bash
pip install -e ".[dev,ui-test]"
python -m playwright install chromium
APEX_UI_TEST=1 pytest tests/test_gui.py  # Bash; set the environment variable in PowerShell on Windows
```

See [validation notes](docs/VALIDATION.md) for audit coverage and remaining limits.

## License

MIT — see [LICENSE](LICENSE).
