import threading
import time
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from apex_injector.cli import cli_main
from apex_injector.config import AppConfig, get_config
from apex_injector.engine import InjectionEngine
from apex_injector.gui.server import create_app
from apex_injector.handlers import InjectionResult, InjectionStatus, MetadataPayload
from apex_injector.manifest import Manifest, ManifestEntry, _parse_flat_metadata, parse_manifest
from apex_injector.thread_pool import InjectionPool, TaskStatus


def test_settings_persist_and_invalid_update_is_atomic(isolated_config):
    with TestClient(create_app()) as client:
        assert client.put("/api/config", json={"engine": {"max_workers": 3}}).status_code == 200
        assert AppConfig.load().engine.max_workers == 3
        assert client.put("/api/config", json={"engine": {"max_workers": -1}}).status_code == 422
        assert client.put("/api/config", json={"engine": {"get_worker_count": 4}}).status_code == 422
        assert client.put("/api/config", json={"engine": {"verify_bitstream": "false"}}).status_code == 422
        assert get_config().engine.max_workers == 3
        assert AppConfig.load().engine.max_workers == 3


def test_risk_preview_and_acknowledgement(media):
    path = media("wav")
    before = path.read_bytes()
    request = {"files": [str(path)], "metadata": {"bext": {"TimeReference": 100}}}
    with TestClient(create_app()) as client:
        preview = client.post("/api/preview", json=request).json()
        assert preview["requires_acknowledgement"]
        assert preview["fields"][0]["level"] == "high"
        rejected = client.post("/api/inject", json=request).json()["results"][0]
        assert rejected["status"] == "unsupported_field"
        assert path.read_bytes() == before
        accepted = client.post("/api/inject", json={**request, "acknowledge_risk": True}).json()["results"][0]
        assert accepted["status"] == "success", accepted
        assert accepted["backup_path"]


def test_api_rejects_relative_paths_and_unknown_schemas():
    with TestClient(create_app()) as client:
        assert (
            client.post("/api/inject", json={"files": ["x.mp4"], "metadata": {"xmp": {"Title": "x"}}}).status_code
            == 422
        )
        assert (
            client.post("/api/preview", json={"files": ["x.mp4"], "metadata": {"typo": {"Title": "x"}}}).status_code
            == 422
        )
        assert client.post("/api/preview", json={"files": ["x.mp4"], "metadata": {"xmp": {}}}).status_code == 422
        assert client.put("/api/config", json={}, headers={"Origin": "https://unrelated.example"}).status_code == 403
        assert client.post("/api/batch/no-such-batch/cancel").status_code == 404


def test_inventory_qualifiers_and_read_only_tags(media, isolated_config, monkeypatch):
    from apex_injector.handlers.exiftool_bridge import ExifToolBridge

    isolated_config.tools.exiftool = "test-tool"
    monkeypatch.setattr(
        ExifToolBridge,
        "inspect_all",
        lambda *a: {
            "tags": [
                {
                    "XMP-dc:Main:Title": "Title",
                    "System:Main:FileName": "name",
                    "Track1:Doc1:GPSLatitude": 55,
                    "XMP-dc:Main:Copy1:Title": "duplicate",
                }
            ],
            "warnings": "",
        },
    )
    with TestClient(create_app()) as client:
        data = client.post("/api/inspect", json={"file": str(media("mp4"))}).json()
        assert data["inventory"][0]["edit_tag"] == "XMP-dc:Title"
        assert data["inventory"][0]["risk"]["level"] == "low"
        assert all(r["risk"]["level"] == "read_only" for r in data["inventory"][1:])


def test_documented_cli_dispatch(monkeypatch):
    import apex_injector.__main__ as entry
    import apex_injector.cli as cli

    handler = Mock()
    monkeypatch.setattr(cli, "cli_main", handler)
    monkeypatch.setattr("sys.argv", ["apex-injector", "inject", "file.mp4", "--title", "Title"])
    entry.main()
    handler.assert_called_once_with(["inject", "file.mp4", "--title", "Title"])


def test_cli_failure_returns_nonzero(tmp_path):
    with pytest.raises(SystemExit) as e:
        cli_main(["inject", str(tmp_path / "missing.mp4"), "--title", "Title"])
    assert e.value.code == 1


def test_cli_scan_export_and_nonrecursive(media, tmp_path):
    media("mp4")
    output = tmp_path / "report.csv"
    cli_main(["scan", str(tmp_path), "--no-recursive", "--export", str(output)])
    assert "sample.mp4" in output.read_text()


@pytest.mark.parametrize(
    "suffix,content",
    [
        ("json", '[{"file":"clip.mp4","metadata":{"xmp":{"dc:Title":"Title"}}}]'),
        ("csv", "file,Title\nclip.mp4,Title\n"),
    ],
)
def test_relative_manifest_paths_use_manifest_directory(tmp_path, suffix, content):
    manifest_path = tmp_path / ("batch." + suffix)
    manifest_path.write_text(content)
    manifest = parse_manifest(manifest_path)
    assert not manifest.errors
    assert manifest.entries[0].file_path == tmp_path / "clip.mp4"


def test_schema_prefixes_are_not_lost():
    payload = _parse_flat_metadata(
        {
            "XMP:dc:Title": "title",
            "EXIF:Artist": "artist",
            "QuickTime:CreateDate": "date",
            "exiftool:XMP-dc:Subject": ["one", "two"],
        }
    )
    assert [(f.schema.value, f.key) for f in payload.fields] == [
        ("xmp", "dc:Title"),
        ("exif", "Artist"),
        ("exiftool", "QuickTime:CreateDate"),
        ("exiftool", "XMP-dc:Subject"),
    ]


def test_batch_counts_worker_exceptions(tmp_path, monkeypatch):
    engine = InjectionEngine()

    def fail(*a):
        raise RuntimeError("deliberate worker exception")

    monkeypatch.setattr(engine, "inject_with_retry", fail)
    manifest = Manifest(
        entries=[ManifestEntry(tmp_path / "one.mp4", MetadataPayload.from_dict({"xmp": {"Title": "x"}}))]
    )
    result = engine.run_batch(manifest)
    assert result.failed == 1 and result.skipped == 0
    assert result.errors[0]["error"] == "deliberate worker exception"


def test_duplicate_manifest_paths_are_rejected(tmp_path, monkeypatch):
    engine = InjectionEngine()
    run = Mock()
    monkeypatch.setattr(engine, "inject_with_retry", run)
    entry = ManifestEntry(tmp_path / "one.mp4", MetadataPayload.from_dict({"xmp": {"Title": "x"}}))
    result = engine.run_batch(Manifest(entries=[entry, entry]))
    assert result.failed == 2
    run.assert_not_called()


def test_cancellation_collects_running_results_and_cancels_queue():
    pool = InjectionPool(max_workers=1)
    started, finish = threading.Event(), threading.Event()
    calls = []

    def slow():
        calls.append(1)
        started.set()
        finish.wait(5)
        return "finished"

    thread = threading.Thread(target=lambda: pool.submit_batch([(str(i), slow, (), {}) for i in range(5)]))
    thread.start()
    assert started.wait(5)
    pool.cancel()
    finish.set()
    thread.join(5)
    assert not thread.is_alive()
    assert len(calls) == 1
    assert len(pool.results) == 5
    assert sum(r.status == TaskStatus.CANCELLED for r in pool.results) == 4
    assert pool.progress.cancelled == 4
    assert pool.progress.completed == 1
    assert pool.progress.percent == 100


def test_api_cancellation_keeps_terminal_state(tmp_path, monkeypatch):
    started, finish = threading.Event(), threading.Event()
    file = tmp_path / "one.mp4"
    file.write_bytes(b"fixture")

    def slow(self, path, payload):
        started.set()
        finish.wait(5)
        return InjectionResult(path, InjectionStatus.SUCCESS, fields_written=1)

    monkeypatch.setattr(InjectionEngine, "inject_with_retry", slow)
    with TestClient(create_app()) as client:
        response = client.post(
            "/api/batch", json={"manifest_data": [{"file": str(file), "metadata": {"xmp": {"Title": "x"}}}]}
        )
        batch_id = response.json()["batch_id"]
        assert started.wait(5)
        assert client.post(f"/api/batch/{batch_id}/cancel").json()["status"] == "cancelling"
        finish.set()
        for _ in range(100):
            state = client.get(f"/api/batch/{batch_id}/status").json()
            if state["status"] != "cancelling":
                break
            time.sleep(0.01)
        assert state["status"] == "cancelled"
        assert state["result"]["succeeded"] == 1
        assert state["result"]["file_results"][0]["status"] == "success"
