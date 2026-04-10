# -*- mode: python ; coding: utf-8 -*-
"""
Apex Meta-Injector — PyInstaller Build Specification.

Produces a single-directory Windows executable with all dependencies
bundled. The GUI static files are included as data.

Usage:
    pip install pyinstaller
    pyinstaller apex_injector.spec
"""

import os
import sys
from pathlib import Path

block_cipher = None

# Project root
ROOT = Path(SPECPATH)
SRC = ROOT / "apex_injector"
STATIC = SRC / "gui" / "static"

# Collect all static GUI assets
datas = []
if STATIC.exists():
    for root, dirs, files in os.walk(str(STATIC)):
        for f in files:
            src = os.path.join(root, f)
            dst = os.path.relpath(root, str(SRC))
            datas.append((src, os.path.join("apex_injector", dst)))

# Include setup_tools.py at root level
datas.append((str(ROOT / "setup_tools.py"), "."))

a = Analysis(
    [str(SRC / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # FastAPI / Starlette / Uvicorn
        "uvicorn",
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "fastapi",
        "starlette",
        "starlette.routing",
        "starlette.staticfiles",
        "starlette.responses",
        "pydantic",
        "websockets",
        "anyio",
        "anyio._backends",
        "anyio._backends._asyncio",
        # pywebview
        "webview",
        "webview.platforms",
        "webview.platforms.edgechromium",
        # Media handling
        "construct",
        "mutagen",
        "mutagen.aiff",
        "mutagen.id3",
        "xxhash",
        # Apex Injector modules
        "apex_injector",
        "apex_injector.cli",
        "apex_injector.config",
        "apex_injector.codec_manifest",
        "apex_injector.engine",
        "apex_injector.manifest",
        "apex_injector.thread_pool",
        "apex_injector.win32_io",
        "apex_injector.gui",
        "apex_injector.gui.server",
        "apex_injector.handlers",
        "apex_injector.handlers.isobmff_handler",
        "apex_injector.handlers.mxf_handler",
        "apex_injector.handlers.matroska_handler",
        "apex_injector.handlers.bwf_handler",
        "apex_injector.handlers.aiff_handler",
        "apex_injector.handlers.exiftool_bridge",
        "setup_tools",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "unittest",
        "test",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ApexMetaInjector",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # GUI application — no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # TODO: Add .ico file
    version=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="ApexMetaInjector",
)
