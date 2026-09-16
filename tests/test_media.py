import struct
import subprocess
from xml.etree import ElementTree as ET

import pytest
from mutagen.aiff import AIFF
from mutagen.wave import WAVE

from apex_injector.engine import InjectionEngine
from apex_injector.handlers import InjectionStatus, MetadataPayload
from apex_injector.handlers.bwf_handler import BWFHandler
from apex_injector.handlers.isobmff_handler import ISOBMFFHandler


def inject(path, data):
    payload = MetadataPayload.from_dict(data)
    payload.acknowledge_risk = True
    return InjectionEngine().inject_single(path, payload)


@pytest.mark.parametrize(
    "extension,data",
    [
        ("mp4", {"xmp": {"dc:Title": "Rock & Roll"}}),
        ("mov", {"xmp": {"dc:Title": "Rock & Roll"}}),
        ("wav", {"bext": {"Description": "Description"}}),
        ("aiff", {"id3v2.4": {"Title": "Title"}}),
    ],
)
def test_real_media_commits_with_backup(media, extension, data):
    path = media(extension)
    before = path.read_bytes()
    result = inject(path, data)
    assert result.status == InjectionStatus.SUCCESS, result.message
    assert result.fields_written == 1
    assert path.read_bytes() != before
    assert result.backup_path.read_bytes() == before
    subprocess.run(["ffprobe", "-v", "error", str(path)], check=True)


def test_mp4_xmp_resize_and_preservation(media):
    path = media("mp4")
    handler = ISOBMFFHandler()
    original = handler.essence_fingerprint(path)
    for title in ("x" * 4000, "x" * 3999, "x" * 8000, "short"):
        result = inject(path, {"xmp": {"dc:Title": title, "dc:Creator": "Author"}})
        assert result.status == InjectionStatus.SUCCESS, result.message
        assert handler.read_metadata(path)["xmp"]["dc:title"] == title
        assert handler.essence_fingerprint(path) == original
        assert len(handler._xmp_boxes(path, handler._parse_top_level_atoms(path))) == 1
    assert inject(path, {"xmp": {"dc:Title": "final"}}).status == InjectionStatus.SUCCESS
    assert handler.read_metadata(path)["xmp"]["dc:creator"] == "Author"
    subprocess.run(["ffprobe", "-v", "error", str(path)], check=True)


def test_failed_verification_reports_failure(media, monkeypatch):
    path = media("mp4")
    before = path.read_bytes()
    monkeypatch.setattr("apex_injector.win32_io.StagedTransaction.verify", lambda self: False)
    result = inject(path, {"xmp": {"Title": "Title"}})
    assert result.status == InjectionStatus.FAILED
    assert result.fields_written == 0
    assert path.read_bytes() == before


def test_mixed_unsupported_payload_not_silently_dropped(media, isolated_config):
    isolated_config.tools.exiftool = None
    path = media("mp4")
    before = path.read_bytes()
    result = inject(path, {"xmp": {"Title": "Title"}, "iptc": {"Keywords": ["a"]}})
    assert result.status == InjectionStatus.UNSUPPORTED_FIELD
    assert result.fields_failed == 2
    assert path.read_bytes() == before


def test_wav_id3_is_written(media):
    path = media("wav")
    result = inject(path, {"id3v2.4": {"Title": "WAV title"}})
    assert result.status == InjectionStatus.SUCCESS, result.message
    assert WAVE(path).tags["TIT2"].text == ["WAV title"]


def test_aiff_save_failure(media, monkeypatch):
    path = media("aiff")
    before = path.read_bytes()

    def fail(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(AIFF, "save", fail)
    result = inject(path, {"id3v2.4": {"Title": "title"}})
    assert result.status == InjectionStatus.FAILED
    assert result.fields_written == 0
    assert path.read_bytes() == before


def test_wav_bext_preserves_unedited_bytes(media):
    path = media("wav")
    h = BWFHandler()
    assert inject(path, {"bext": {"Description": "old", "LoudnessValue": -1234}}).status == InjectionStatus.SUCCESS
    assert inject(path, {"bext": {"Description": "new"}}).status == InjectionStatus.SUCCESS
    chunk = next(c for c in h._chunks(path) if c["id"] == b"bext")
    data = path.read_bytes()[chunk["data_offset"] :]
    assert struct.unpack_from("<H", data, 346)[0] == 2
    assert struct.unpack_from("<5h", data, 412) == (-1234, 32767, 32767, 32767, 32767)


def test_wav_xml_escaped_and_merged(media):
    path = media("wav")
    h = BWFHandler()
    for fields in ({"dc:Title": "Rock & Roll", "dc:Creator": "A < B"}, {"dc:Title": "New"}):
        assert inject(path, {"xmp": fields}).status == InjectionStatus.SUCCESS
    xml = h.read_metadata(path)["xmp"]["raw_xml"]
    root = ET.fromstring(xml)
    assert "A < B" in "".join(root.itertext())


def test_rf64_is_explicitly_read_only(tmp_path):
    path = tmp_path / "large.wav"
    path.write_bytes(b"RF64" + b"\xff" * 4 + b"WAVE")
    before = path.read_bytes()
    result = inject(path, {"bext": {"Description": "test"}})
    assert result.status == InjectionStatus.FAILED
    assert path.read_bytes() == before


def test_packet_fingerprint_ignores_metadata(media):
    from apex_injector.verification import packet_fingerprint

    path = media("mp4")
    before = packet_fingerprint(path)
    assert inject(path, {"xmp": {"Title": "after"}}).status == InjectionStatus.SUCCESS
    assert packet_fingerprint(path) == before


@pytest.mark.parametrize(
    "fields",
    [
        {"Title": ["one", "two"]},
        {"Title": "one", "dc:title": "two"},
        {"Title": {"nested": "unsupported"}},
    ],
)
def test_native_xmp_rejects_lossy_values(media, fields):
    path = media("mp4")
    original = path.read_bytes()
    result = inject(path, {"xmp": fields})
    assert result.status == InjectionStatus.FAILED
    assert result.fields_written == 0
    assert path.read_bytes() == original
