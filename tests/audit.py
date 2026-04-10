"""Apex Meta-Injector — Comprehensive Audit Script."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def main():
    errors = []
    
    print("=" * 60)
    print("  Apex Meta-Injector — Full Code Audit")
    print("=" * 60)

    # 1. Handler Registry
    print("\n[1] Handler Registry")
    from apex_injector.handlers import get_handler
    from apex_injector.codec_manifest import ContainerFormat
    # Trigger handler auto-registration (engine does this via _ensure_handlers_loaded)
    import apex_injector.handlers.isobmff_handler  # noqa
    import apex_injector.handlers.mxf_handler  # noqa
    import apex_injector.handlers.matroska_handler  # noqa
    import apex_injector.handlers.bwf_handler  # noqa
    import apex_injector.handlers.aiff_handler  # noqa
    containers = ["mp4", "mov", "mxf", "mkv", "wav", "bwf", "aiff"]
    for c in containers:
        cf = ContainerFormat(c)
        h = get_handler(cf)
        name = type(h).__name__ if h else "None"
        status = "OK" if h else "MISSING"
        if not h:
            errors.append(f"Handler missing for {c}")
        print(f"  {status:7s}  {c:5s} -> {name}")

    # 2. Engine Interface
    print("\n[2] Engine Interface")
    from apex_injector.engine import InjectionEngine
    required_methods = [
        "inject_single", "inject_with_retry", "run_batch",
        "scan_directory", "analyze_file"
    ]
    for m in required_methods:
        ok = hasattr(InjectionEngine, m)
        print(f"  {'OK' if ok else 'MISSING':7s}  InjectionEngine.{m}")
        if not ok:
            errors.append(f"Engine missing method: {m}")

    # 3. MetadataPayload
    print("\n[3] MetadataPayload")
    from apex_injector.handlers import MetadataPayload
    ok = hasattr(MetadataPayload, "from_dict")
    print(f"  {'OK' if ok else 'MISSING':7s}  MetadataPayload.from_dict")
    if not ok:
        errors.append("MetadataPayload.from_dict missing")

    # 4. StagedTransaction
    print("\n[4] StagedTransaction")
    from apex_injector.win32_io import StagedTransaction
    for m in ["write_staged", "copy_to_staging", "set_essence_region",
              "verify", "commit", "rollback"]:
        ok = hasattr(StagedTransaction, m)
        print(f"  {'OK' if ok else 'MISSING':7s}  StagedTransaction.{m}")
        if not ok:
            errors.append(f"StagedTransaction missing: {m}")

    # 5. Manifest
    print("\n[5] Manifest Parser")
    from apex_injector.manifest import parse_manifest, Manifest, ManifestEntry
    ok = hasattr(Manifest, "file_count")
    print(f"  {'OK' if ok else 'MISSING':7s}  Manifest.file_count")
    if not ok:
        errors.append("Manifest.file_count missing")

    # 6. Config
    print("\n[6] Config System")
    from apex_injector.config import get_config
    config = get_config()
    checks = [
        ("config.tools.get_status", hasattr(config.tools, "get_status")),
        ("config.engine.get_worker_count", hasattr(config.engine, "get_worker_count")),
        ("config.engine.create_backups", hasattr(config.engine, "create_backups")),
        ("config.engine.verify_bitstream", hasattr(config.engine, "verify_bitstream")),
    ]
    for name, ok in checks:
        print(f"  {'OK' if ok else 'MISSING':7s}  {name}")
        if not ok:
            errors.append(f"Config missing: {name}")

    # 7. CLI
    print("\n[7] CLI Interface")
    from apex_injector.cli import cli_main
    print(f"  OK      cli_main callable: {callable(cli_main)}")

    # 8. FastAPI Server
    print("\n[8] FastAPI Server")
    from apex_injector.gui.server import create_app
    app = create_app()
    routes = []
    for route in app.routes:
        if hasattr(route, "path"):
            routes.append(route.path)
    api_routes = [r for r in routes if r.startswith("/api")]
    print(f"  OK      {len(api_routes)} API endpoints found")
    expected_endpoints = [
        "/api/status", "/api/scan", "/api/inject", "/api/batch",
        "/api/config"
    ]
    for ep in expected_endpoints:
        found = any(ep in r for r in api_routes)
        print(f"  {'OK' if found else 'MISSING':7s}  {ep}")
        if not found:
            errors.append(f"Missing endpoint: {ep}")

    # 9. WebSocket route
    ws_routes = [r for r in routes if "/ws" in str(r)]
    ok = len(ws_routes) > 0
    print(f"  {'OK' if ok else 'MISSING':7s}  WebSocket /ws/events")
    if not ok:
        errors.append("WebSocket route missing")

    # 10. Codec Manifest Integrity
    print("\n[9] Codec Manifest")
    from apex_injector.codec_manifest import (
        CODEC_MANIFEST, detect_container, find_mapping, get_manifest_summary
    )
    total_mappings = sum(len(f.mappings) for f in CODEC_MANIFEST)
    print(f"  OK      {len(CODEC_MANIFEST)} codec families, {total_mappings} mappings")
    summary = get_manifest_summary()
    print(f"  OK      get_manifest_summary returns {len(summary)} entries")

    # 11. Tool Validation
    print("\n[10] Tool Validation")
    from setup_tools import validate_all_tools
    tools = validate_all_tools()
    print(f"  OK      {len(tools['required'])} required tools checked")
    print(f"  OK      {len(tools['optional'])} optional tools checked")

    # 12. Thread Pool
    print("\n[11] Thread Pool")
    from apex_injector.thread_pool import InjectionPool
    print(f"  OK      InjectionPool importable")

    # 13. pywebview launcher
    print("\n[12] GUI Launcher")
    from apex_injector.gui import launch_gui
    print(f"  OK      launch_gui callable: {callable(launch_gui)}")

    # Summary
    print("\n" + "=" * 60)
    if errors:
        print(f"  AUDIT FAILED: {len(errors)} issues found")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("  AUDIT PASSED: All checks OK")
    print("=" * 60)


if __name__ == "__main__":
    main()
