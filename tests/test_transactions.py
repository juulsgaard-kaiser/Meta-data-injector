import os

import pytest

from apex_injector.win32_io import (
    FileInUseError,
    StagedTransaction,
    VerificationError,
    hash_regions,
)


def test_rollback_is_terminal(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"original")
    with StagedTransaction(path, verify_essence=False) as txn:
        txn.write_staged(b"changed")
        txn.rollback()
    assert path.read_bytes() == b"original"
    assert not txn.staging_path.exists()


def test_missing_verifier_fails_closed(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"original")
    with pytest.raises(VerificationError):
        with StagedTransaction(path) as txn:
            txn.write_staged(b"corrupted")
    assert path.read_bytes() == b"original"
    assert not txn.staging_path.exists()


@pytest.mark.skipif(os.name == "nt", reason="Windows denies source writes while the transaction is open")
def test_concurrent_source_edit_aborts(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"old!ESSENCE")
    with pytest.raises(VerificationError, match="Source changed"):
        with StagedTransaction(path, fingerprint=lambda p: p.read_bytes()[4:]) as txn:
            txn.write_staged(b"oursESSENCE")
            path.write_bytes(b"themESSENCE")
    assert path.read_bytes() == b"themESSENCE"


def test_backup_is_not_overwritten(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"original")
    with StagedTransaction(path, verify_essence=False) as first:
        first.write_staged(b"first")
    with StagedTransaction(path, verify_essence=False) as second:
        second.write_staged(b"second")
    assert first.backup_path != second.backup_path
    assert first.backup_path.read_bytes() == b"original"
    assert second.backup_path.read_bytes() == b"first"


def test_backup_failure_preserves_source(tmp_path, monkeypatch):
    path = tmp_path / "file"
    path.write_bytes(b"original")

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr("apex_injector.win32_io.shutil.copyfileobj", fail)
    with pytest.raises(OSError):
        with StagedTransaction(path, verify_essence=False) as txn:
            txn.write_staged(b"new")
    assert path.read_bytes() == b"original"


def test_second_transaction_cannot_enter(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"original")
    with StagedTransaction(path, verify_essence=False) as first:
        with pytest.raises(FileInUseError):
            with StagedTransaction(path):
                pass
        first.rollback()


def test_regions_reject_truncated_and_empty(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"abc")
    for regions in ([], [(0, 4)], [(5, 1)], [(0, 0)]):
        with pytest.raises(ValueError):
            hash_regions(path, regions)


@pytest.mark.skipif(os.name != "nt", reason="Tests Windows mandatory sharing protection")
def test_windows_denies_writes_during_transaction(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"original")
    with StagedTransaction(path, verify_essence=False) as txn:
        with pytest.raises(PermissionError):
            path.write_bytes(b"external change")
        txn.write_staged(b"our change")
    assert path.read_bytes() == b"our change"
    assert txn.backup_path.read_bytes() == b"original"
