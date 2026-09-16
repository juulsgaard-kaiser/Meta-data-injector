"""Optional real-browser test: APEX_UI_TEST=1 pytest tests/test_gui.py."""

import os
import socket
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("APEX_UI_TEST") != "1", reason="Set APEX_UI_TEST=1 for browser tests")


def test_edit_risk_and_settings_in_browser(media, isolated_config, tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    import uvicorn

    from apex_injector.config import AppConfig
    from apex_injector.gui.server import create_app

    path = media("wav")
    original = path.read_bytes()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(), log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.02)
    assert server.started
    errors = []
    try:
        with sync_api.sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ.get("APEX_TEST_BROWSER"), headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{port}/#inject")
            page.locator("#inject-paths").fill(str(path))
            page.locator("#inspect-files").click()
            page.locator("#inspection-card").wait_for(state="visible")
            page.locator("#advanced-metadata").fill('{"bext":{"TimeReference":123}}')
            page.locator("#preview-edit").click()
            sync_api.expect(page.locator("#risk-preview")).to_contain_text("HIGH")
            page.locator("#apply-edit").click()
            page.locator("#risk-cancel").click()
            assert path.read_bytes() == original
            page.locator("#apply-edit").click()
            page.locator("#risk-accept").click()
            sync_api.expect(page.locator("#inject-results")).to_contain_text("success")
            sync_api.expect(page.locator("#inject-results")).to_contain_text("Backup:")
            assert path.read_bytes() != original
            page.screenshot(path=str(tmp_path / "edit-results.png"), full_page=True)
            page.goto(f"http://127.0.0.1:{port}/#settings")
            page.locator("#settings-workers").fill("3")
            page.get_by_role("button", name="Save Changes").click()
            sync_api.expect(page.locator("#toast-container")).to_contain_text("Settings saved")
            assert AppConfig.load().engine.max_workers == 3
            import json

            manifest = tmp_path / "batch.json"
            manifest.write_text(json.dumps([{"file": str(path), "metadata": {"bext": {"Description": "Batch title"}}}]))
            page.goto(f"http://127.0.0.1:{port}/#batch")
            page.locator("#batch-manifest-path").fill(str(manifest))
            page.get_by_role("button", name="Start Batch", exact=True).click()
            sync_api.expect(page.locator("#batch-file-table")).to_contain_text("sample.wav")
            sync_api.expect(page.locator("#batch-cancel-btn")).to_have_text("Done")
            browser.close()
        assert not errors
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
