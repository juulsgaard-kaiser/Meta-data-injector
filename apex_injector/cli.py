"""
Apex Meta-Injector — CLI Interface.

Command-line interface for headless/scripting use.
Supports inject, batch, verify, and scan operations.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from apex_injector import __app_name__, __version__


def cli_main(argv: list[str] | None = None):
    """Main CLI entry point."""
    # Windows redirected output otherwise uses a legacy code page, which cannot
    # represent metadata values or the CLI's status symbols.
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        prog="apex-injector",
        description=f"{__app_name__} v{__version__} — High-speed batch metadata injection",
    )
    parser.add_argument("--version", action="version", version=f"{__app_name__} {__version__}")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ── inject ──────────────────────────────────────────────
    inject_parser = subparsers.add_parser("inject", help="Inject metadata into file(s)")
    inject_parser.add_argument("files", nargs="+", type=Path, help="File(s) to inject metadata into")
    inject_parser.add_argument("--title", help="Set title")
    inject_parser.add_argument("--artist", help="Set artist/creator")
    inject_parser.add_argument("--description", help="Set description")
    inject_parser.add_argument("--keywords", help="Set keywords (semicolon-separated)")
    inject_parser.add_argument("--date", help="Set date")
    inject_parser.add_argument("--comment", help="Set comment")
    inject_parser.add_argument("--copyright", help="Set copyright")
    inject_parser.add_argument("--meta", action="append", help="Key=value metadata (e.g. --meta 'XMP:dc:Title=Hello')")
    inject_parser.add_argument(
        "--accept-risk", action="store_true", help="Acknowledge technical/unknown metadata edit risks"
    )
    inject_parser.add_argument("--no-backup", action="store_true", help="Don't create backup files")
    inject_parser.add_argument("--no-verify", action="store_true", help="Skip bitstream verification")

    # ── batch ──────────────────────────────────────────────
    batch_parser = subparsers.add_parser("batch", help="Batch inject from manifest")
    batch_parser.add_argument("manifest", type=Path, help="JSON or CSV manifest file")
    batch_parser.add_argument(
        "--accept-risk", action="store_true", help="Acknowledge higher-risk edits in the manifest"
    )
    batch_parser.add_argument("--workers", type=int, default=0, help="Thread pool workers (0=auto)")
    batch_parser.add_argument("--no-backup", action="store_true")
    batch_parser.add_argument("--auto-approve-wrap", action="store_true", help="Auto-approve Complex Wrap operations")

    # ── scan ───────────────────────────────────────────────
    scan_parser = subparsers.add_parser("scan", help="Scan directory for media files")
    scan_parser.add_argument("directory", type=Path, help="Directory to scan")
    scan_parser.add_argument("--recursive", "-r", action=argparse.BooleanOptionalAction, default=True)
    scan_parser.add_argument("--export", type=Path, help="Export scan as CSV")
    scan_parser.add_argument("--format", choices=["table", "json", "csv"], default="table")
    scan_parser.add_argument("--output", "-o", type=Path, help="Output file (default: stdout)")

    # ── verify ─────────────────────────────────────────────
    verify_parser = subparsers.add_parser("verify", help="Verify metadata in file(s)")
    verify_parser.add_argument("files", nargs="+", type=Path, help="File(s) to verify")
    verify_parser.add_argument(
        "--deep", action="store_true", help="Inspect unknown, duplicate and embedded tags with ExifTool"
    )
    verify_parser.add_argument("--format", choices=["table", "json"], default="table")

    # ── tools ──────────────────────────────────────────────
    subparsers.add_parser("tools", help="Check external tool dependencies")

    args = parser.parse_args(argv)

    # Configure logging
    level = logging.DEBUG if args.debug else (logging.INFO if args.verbose else logging.WARNING)
    logging.basicConfig(
        level=level,
        format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
        datefmt="%H:%M:%S",
    )

    if not args.command:
        parser.print_help()
        sys.exit(0)

    # Dispatch
    if args.command == "tools":
        _cmd_tools()
    elif args.command == "scan":
        _cmd_scan(args)
    elif args.command == "inject":
        _cmd_inject(args)
    elif args.command == "batch":
        _cmd_batch(args)
    elif args.command == "verify":
        _cmd_verify(args)


def _cmd_tools():
    """Check tool dependencies."""
    from apex_injector.tool_status import print_tool_status

    status = print_tool_status()
    sys.exit(0 if status["all_required_available"] else 1)


def _cmd_scan(args):
    """Scan directory for media files."""
    from apex_injector.engine import InjectionEngine

    engine = InjectionEngine()
    if args.export:
        args.format, args.output = "csv", args.export
    results = engine.scan_directory(args.directory, recursive=args.recursive)

    if args.format == "json":
        data = []
        for r in results:
            data.append(
                {
                    "file": str(r.file_path),
                    "container": r.container.value if r.container else None,
                    "codec": r.codec,
                    "size": r.file_size,
                    "injectable": r.injectable,
                    "complex_wrap_risk": r.complex_wrap_risk,
                    "error": r.error,
                }
            )
        output = json.dumps(data, indent=2)
    elif args.format == "csv":
        import csv
        import io

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["File", "Container", "Codec", "Size", "Injectable", "Risk", "Error"])
        for r in results:
            writer.writerow(
                [
                    str(r.file_path),
                    r.container.value if r.container else "",
                    r.codec,
                    r.file_size,
                    r.injectable,
                    r.complex_wrap_risk,
                    r.error or "",
                ]
            )
        output = buf.getvalue()
    else:
        # Table format
        lines = [
            f"\n{'File':<50} {'Container':<8} {'Codec':<16} {'Status':<12} {'Risk':<8}",
            "─" * 94,
        ]
        for r in results:
            status = "✓ Ready" if r.injectable else f"✗ {r.error or 'N/A'}"
            lines.append(
                f"{str(r.file_path.name):<50} "
                f"{(r.container.value if r.container else '?'):<8} "
                f"{r.codec:<16} "
                f"{status:<12} "
                f"{r.complex_wrap_risk:<8}"
            )
        lines.append(f"\nTotal: {len(results)} files")
        output = "\n".join(lines)

    if args.output:
        args.output.write_text(output, encoding="utf-8")
        print(f"Scan results written to {args.output}")
    else:
        print(output)


def _cmd_inject(args):
    """Inject metadata into files."""
    from apex_injector.codec_manifest import MetadataSchema
    from apex_injector.config import get_config
    from apex_injector.engine import InjectionEngine
    from apex_injector.handlers import MetadataField, MetadataPayload

    config = get_config()
    if args.no_backup:
        config.engine.create_backups = False
    if args.no_verify:
        config.engine.verify_bitstream = False

    # Build payload
    payload = MetadataPayload()
    field_map = {
        "title": ("Title", MetadataSchema.XMP),
        "artist": ("Creator", MetadataSchema.XMP),
        "description": ("Description", MetadataSchema.XMP),
        "date": ("Date", MetadataSchema.XMP),
        "comment": ("Description", MetadataSchema.XMP),
        "copyright": ("Rights", MetadataSchema.XMP),
    }

    for arg_name, (key, schema) in field_map.items():
        value = getattr(args, arg_name, None)
        if value:
            payload.fields.append(MetadataField(schema=schema, key=key, value=value))

    if args.keywords:
        keywords = [k.strip() for k in args.keywords.split(";") if k.strip()]
        payload.fields.append(MetadataField(schema=MetadataSchema.XMP, key="Subject", value=keywords))

    if args.meta:
        from apex_injector.manifest import _parse_flat_metadata

        for text in args.meta:
            key, separator, value = text.partition("=")
            if not separator:
                parser_error = "--meta requires schema:tag=value"
                raise SystemExit(parser_error)
            payload.fields.extend(_parse_flat_metadata({key: value}).fields)
    payload.acknowledge_risk = args.accept_risk

    if not payload.fields:
        print("Error: No metadata specified. Use --title, --artist, etc.")
        sys.exit(1)

    engine = InjectionEngine(
        on_complex_wrap=lambda path, info: _prompt_complex_wrap(path, info),
    )

    failed = False
    for file_path in args.files:
        print(f"Injecting: {file_path}")
        # Common CLI flags use the native schema of the selected container.
        import copy

        from apex_injector.codec_manifest import ContainerFormat, detect_container

        selected = copy.deepcopy(payload)
        container = detect_container(file_path)
        if not args.meta:
            if container == ContainerFormat.AIFF:
                for field in selected.fields:
                    field.schema = MetadataSchema.ID3V24
            elif container == ContainerFormat.MKV:
                for field in selected.fields:
                    field.schema = MetadataSchema.MATROSKA_TAGS
        from apex_injector.risk import assess_risks

        for risk in assess_risks(selected)["fields"]:
            if risk["level"] != "low":
                print(f"  {risk['level'].upper()}: {risk['tag']} — {risk['reason']}")
        result = engine.inject_with_retry(file_path, selected)
        failed |= result.status.value != "success"
        status_icon = "✓" if result.status.value == "success" else "✗"
        print(f"  {status_icon} {result.status.value}: {result.message or f'{result.fields_written} fields written'}")
        if result.backup_path:
            print(f"  Backup: {result.backup_path}")
    if failed:
        raise SystemExit(1)


def _cmd_batch(args):
    """Run batch injection from manifest."""
    from apex_injector.config import get_config
    from apex_injector.engine import InjectionEngine
    from apex_injector.manifest import parse_manifest

    config = get_config()
    if args.no_backup:
        config.engine.create_backups = False
    if args.workers > 0:
        config.engine.max_workers = args.workers

    manifest = parse_manifest(args.manifest)

    if manifest.errors:
        print("Manifest errors:")
        for err in manifest.errors:
            print(f"  ✗ {err}")
        sys.exit(1)

    warnings = manifest.validate()
    if warnings:
        print("Warnings:")
        for w in warnings:
            print(f"  ⚠ {w}")

    print(f"\nBatch: {manifest.file_count} files from {args.manifest}")

    def on_progress(progress):
        pct = progress.percent
        bar = "█" * int(pct / 2) + "░" * (50 - int(pct / 2))
        print(f"\r  [{bar}] {pct:.0f}% ({progress.completed}/{progress.total})", end="", flush=True)

    complex_wrap_fn = (lambda p, i: True) if args.auto_approve_wrap else (lambda p, i: _prompt_complex_wrap(p, i))

    engine = InjectionEngine(
        on_progress=on_progress,
        on_complex_wrap=complex_wrap_fn,
    )

    from apex_injector.risk import assess_risks

    for entry in manifest.entries:
        for risk in assess_risks(entry.metadata)["fields"]:
            if risk["level"] != "low":
                print(f"  {entry.file_path}: {risk['level'].upper()} {risk['tag']} — {risk['reason']}")
        entry.metadata.acknowledge_risk = args.accept_risk
    batch_result = engine.run_batch(manifest)

    print("\n\nResults:")
    print(f"  ✓ Succeeded: {batch_result.succeeded}")
    print(f"  ✗ Failed:    {batch_result.failed}")
    print(f"  ⏳ Pending:   {batch_result.complex_wrap_pending}")
    print(f"  ⏱ Time:      {batch_result.elapsed_ms / 1000:.1f}s")

    if batch_result.errors:
        print("\nErrors:")
        for err in batch_result.errors[:10]:
            print(f"  ✗ {err.get('file', 'batch')}: {err['error']}")
        if len(batch_result.errors) > 10:
            print(f"  ... and {len(batch_result.errors) - 10} more")

    if batch_result.failed or batch_result.skipped or batch_result.complex_wrap_pending:
        raise SystemExit(1)


def _cmd_verify(args):
    """Inspect metadata; --deep includes unknown and embedded tags."""
    from apex_injector.engine import InjectionEngine
    from apex_injector.handlers.exiftool_bridge import get_exiftool_bridge

    results = []
    failed = False
    for path in args.files:
        try:
            if args.deep:
                metadata = get_exiftool_bridge().inspect_all(path)
            else:
                analysis = InjectionEngine().analyze_file(path)
                if analysis.error:
                    raise ValueError(analysis.error)
                metadata = analysis.current_metadata
            results.append({"file": str(path), "metadata": metadata})
        except Exception as e:
            failed = True
            results.append({"file": str(path), "error": str(e)})
    print(json.dumps(results, indent=2, ensure_ascii=False))
    if failed:
        raise SystemExit(1)


def _prompt_complex_wrap(path: Path, info: str) -> bool:
    """Prompt user for Complex Wrap approval."""
    print(f"\n⚠ COMPLEX WRAP REQUIRED: {path.name}")
    print(f"  {info}")
    response = input("  Proceed? [y/N] ").strip().lower()
    return response in ("y", "yes")
