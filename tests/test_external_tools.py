import os
import shutil

import pytest

from apex_injector.engine import InjectionEngine
from apex_injector.handlers import InjectionStatus, MetadataPayload
from apex_injector.handlers.exiftool_bridge import ExifToolBridge
from apex_injector.verification import packet_fingerprint


@pytest.fixture
def exiftool(isolated_config):
    path = os.environ.get("APEX_TEST_EXIFTOOL") or shutil.which("exiftool")
    if not path:
        pytest.skip("Install ExifTool or set APEX_TEST_EXIFTOOL")
    isolated_config.tools.exiftool = path
    return ExifToolBridge()


def test_exiftool_list_write_and_deep_read(media, exiftool):
    path = media("mp4")
    before = packet_fingerprint(path)
    payload = MetadataPayload.from_dict(
        {"exiftool": {"XMP-dc:Title": "Café & 日本語", "XMP-dc:Subject": ["one", "two"]}}
    )
    result = InjectionEngine().inject_single(path, payload)
    assert result.status == InjectionStatus.SUCCESS, result.message
    assert result.fields_written == 2
    assert packet_fingerprint(path) == before
    tags = exiftool.inspect_all(path)["tags"][0]
    assert tags["XMP-dc:Main:Title"] == "Café & 日本語"
    assert tags["XMP-dc:Main:Subject"] == ["one", "two"]
    assert any("ChunkOffset" in key for key in tags)


def test_unknown_exiftool_tag_does_not_commit(media, exiftool):
    path = media("mp4")
    before = path.read_bytes()
    payload = MetadataPayload.from_dict({"exiftool": {"XMP-dc:DoesNotExistAtAll": "value"}})
    payload.acknowledge_risk = True
    result = InjectionEngine().inject_single(path, payload)
    assert result.status == InjectionStatus.FAILED
    assert result.fields_written == 0
    assert path.read_bytes() == before


def test_mkv_tags_preserve_existing_tags(media, isolated_config):
    tools = {name: shutil.which(name) for name in ("mkvpropedit", "mkvextract")}
    if not all(tools.values()):
        pytest.skip("MKVToolNix is required")
    for name, path in tools.items():
        setattr(isolated_config.tools, name, path)
    path = media("mkv")
    before = packet_fingerprint(path)
    engine = InjectionEngine()
    for fields in ({"ARTIST": "Original æøå artist"}, {"title": "Title", "DESCRIPTION": "Rock & Roll"}):
        payload = MetadataPayload.from_dict({"matroska_tags": fields})
        result = engine.inject_single(path, payload)
        assert result.status == InjectionStatus.SUCCESS, result.message
    from apex_injector.handlers.matroska_handler import MatroskaHandler

    root = MatroskaHandler()._read_tags(path)
    values = {node.findtext("Name"): node.findtext("String") for node in root.findall(".//Simple")}
    assert values["ARTIST"] == "Original æøå artist"
    assert values["DESCRIPTION"] == "Rock & Roll"
    assert packet_fingerprint(path) == before


@pytest.mark.parametrize("extension", ["wav", "aiff"])
def test_audio_inventory_is_read_only_for_exiftool_route(media, exiftool, extension):
    from fastapi.testclient import TestClient

    from apex_injector.gui.server import create_app

    path = media(extension)
    engine = InjectionEngine()
    original = path.read_bytes()
    result = engine.inject_single(path, MetadataPayload.from_dict({"exiftool": {"XMP-dc:Title": "Title"}}))
    assert result.status == InjectionStatus.UNSUPPORTED_FIELD
    assert path.read_bytes() == original
    with TestClient(create_app()) as client:
        response = client.post("/api/inspect", json={"file": str(path)}).json()
    assert response["inventory"]
    assert all(row["risk"]["level"] == "read_only" for row in response["inventory"])
