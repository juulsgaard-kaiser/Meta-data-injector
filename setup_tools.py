"""
Apex Meta-Injector — External Tool Setup & Validation.

Checks for required external tools (exiftool, mkvpropedit, bmxtranswrap)
and provides guidance for installation.
"""

import subprocess
import sys
import shutil
from pathlib import Path


REQUIRED_TOOLS = {
    "exiftool": {
        "description": "ExifTool — Universal metadata reader/writer",
        "url": "https://exiftool.org/",
        "install_guide": (
            "Download the Windows executable from https://exiftool.org/\n"
            "Rename 'exiftool(-k).exe' to 'exiftool.exe' and place it on your PATH."
        ),
        "version_cmd": ["exiftool", "-ver"],
    },
    "mkvpropedit": {
        "description": "MKVToolNix mkvpropedit — MKV metadata editor",
        "url": "https://mkvtoolnix.download/",
        "install_guide": (
            "Download and install MKVToolNix from https://mkvtoolnix.download/\n"
            "The installer will add tools to your PATH automatically."
        ),
        "version_cmd": ["mkvpropedit", "--version"],
    },
    "bmxtranswrap": {
        "description": "bmx transwrap — MXF container manipulation",
        "url": "https://github.com/bbc/bmx",
        "install_guide": (
            "Build bmx from source at https://github.com/bbc/bmx\n"
            "or download pre-built binaries and add to PATH."
        ),
        "version_cmd": ["bmxtranswrap", "--version"],
    },
}

OPTIONAL_TOOLS = {
    "ffmpeg": {
        "description": "FFmpeg — Media framework (for sample generation & verification)",
        "url": "https://ffmpeg.org/download.html",
        "version_cmd": ["ffmpeg", "-version"],
    },
    "ffprobe": {
        "description": "FFprobe — Media analyzer (for verification)",
        "url": "https://ffmpeg.org/download.html",
        "version_cmd": ["ffprobe", "-version"],
    },
}


def check_tool(name: str, version_cmd: list[str]) -> dict:
    """Check if a tool is available and get its version."""
    path = shutil.which(name)
    result = {
        "name": name,
        "available": path is not None,
        "path": path,
        "version": None,
        "error": None,
    }

    if path:
        try:
            proc = subprocess.run(
                version_cmd,
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            version_line = proc.stdout.strip().split("\n")[0] if proc.stdout else proc.stderr.strip().split("\n")[0]
            result["version"] = version_line
        except Exception as e:
            result["error"] = str(e)

    return result


def validate_all_tools() -> dict:
    """Validate all required and optional tools. Returns status dict."""
    status = {"required": {}, "optional": {}, "all_required_available": True}

    for name, info in REQUIRED_TOOLS.items():
        check = check_tool(name, info["version_cmd"])
        check["description"] = info["description"]
        check["url"] = info["url"]
        check["install_guide"] = info["install_guide"]
        status["required"][name] = check
        if not check["available"]:
            status["all_required_available"] = False

    for name, info in OPTIONAL_TOOLS.items():
        check = check_tool(name, info["version_cmd"])
        check["description"] = info["description"]
        check["url"] = info["url"]
        status["optional"][name] = check

    return status


def print_tool_status():
    """Print a formatted status report of all tools."""
    status = validate_all_tools()

    print("\n" + "=" * 60)
    print("  Apex Meta-Injector — Tool Dependency Check")
    print("=" * 60)

    print("\n  Required Tools:")
    print("  " + "-" * 56)
    for name, info in status["required"].items():
        icon = "✓" if info["available"] else "✗"
        ver = f" ({info['version']})" if info["version"] else ""
        print(f"  {icon}  {name:<16} {info['description']}{ver}")
        if not info["available"]:
            print(f"     └─ Install: {info['url']}")

    print("\n  Optional Tools:")
    print("  " + "-" * 56)
    for name, info in status["optional"].items():
        icon = "✓" if info["available"] else "○"
        ver = f" ({info['version']})" if info["version"] else ""
        print(f"  {icon}  {name:<16} {info['description']}{ver}")

    print("\n" + "=" * 60)
    if status["all_required_available"]:
        print("  Status: All required tools available ✓")
    else:
        missing = [n for n, i in status["required"].items() if not i["available"]]
        print(f"  Status: MISSING required tools: {', '.join(missing)}")
    print("=" * 60 + "\n")

    return status


if __name__ == "__main__":
    print_tool_status()
