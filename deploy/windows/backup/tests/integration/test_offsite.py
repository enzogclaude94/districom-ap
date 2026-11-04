"""Encrypted off-site copy with rclone crypt against a LOCAL folder (A-13, A-14).

This proves the encryption and the copy → restore circuit, not a validated remote backup: the
real bucket, its object lock (immutability) and its retention stay PENDIENTE.
"""

import os
import shutil
from pathlib import Path

import pytest

from districom_backup.base_backup import list_backups, take_base_backup
from districom_backup.compare import compare, fingerprint
from districom_backup.offsite import CryptRemote, OffsiteError, pull, push
from districom_backup.repo import MARKER
from districom_backup.restore import RestoreStatus
from districom_backup.walnames import is_history_name, is_segment_name
from tests.integration.support import DBNAME, Primary

RCLONE = os.environ.get("DISTRICOM_RCLONE", "rclone")
KEY = "w2a-poc-key-not-a-real-secret"
CANARY = "W2A-PLAINTEXT-CANARY-7f3c"


@pytest.fixture(autouse=True)
def _require_rclone() -> None:
    if shutil.which(RCLONE) is None:
        pytest.fail(f"rclone not found ({RCLONE}); set DISTRICOM_RCLONE")


def _contains(path: Path, needle: bytes) -> bool:
    return any(needle in p.read_bytes() for p in path.rglob("*") if p.is_file())


@pytest.mark.criterion("A-13")
def test_encrypted_copy_restores_identical_without_the_local_repository(primary: Primary) -> None:
    take_base_backup(primary.repo, port=primary.instance.port)
    primary.write(200, "offsite")
    with primary.connect() as conn:
        conn.execute("INSERT INTO poc.movements (account_id, amount, tag) VALUES (3, 1, %s)", (CANARY,))
    expected = primary.fingerprint()
    primary.switch_and_wait()
    assert _contains(primary.repo, CANARY.encode()), "the canary must be readable in repo1, or the check proves nothing"

    remote_dir = primary.root / "remote-folder"
    remote = CryptRemote(str(remote_dir), KEY, RCLONE)
    push(primary.repo, remote)

    stored = [p for p in remote_dir.rglob("*") if p.is_file()]
    assert stored
    for item in remote_dir.rglob("*"):
        assert not is_segment_name(item.name), f"plaintext name {item.name}"
        assert not is_history_name(item.name), f"plaintext name {item.name}"
        assert item.name not in {MARKER, "wal", "base", "manifests", "backup_manifest", "backup_label"}
    assert not _contains(remote_dir, CANARY.encode()), "plaintext data in the remote copy"
    assert not _contains(remote_dir, b"PostgreSQL"), "plaintext data in the remote copy"

    # Total loss of the premises: repo1 is gone; only the remote copy and the key remain.
    shutil.rmtree(primary.repo)
    recovered = primary.root / "repo2-pulled"
    pull(remote, recovered)
    backup = list_backups(recovered)[-1]
    outcome = primary.restore(backup, "from-remote", repo=recovered)
    assert outcome.status is RestoreStatus.COMPLETE, outcome.detail
    assert compare(expected, fingerprint(outcome.instance, DBNAME)) == ("IDENTICAL", [])

    # A second push only adds files (copy --immutable, never sync).
    push(recovered, remote)


@pytest.mark.criterion("A-14")
def test_wrong_key_fails_clearly(primary: Primary) -> None:
    take_base_backup(primary.repo, port=primary.instance.port)
    primary.switch_and_wait()
    remote_dir = primary.root / "remote-folder"
    push(primary.repo, CryptRemote(str(remote_dir), KEY, RCLONE))

    destination = primary.root / "pulled-with-wrong-key"
    with pytest.raises(OffsiteError, match="no manifest could be read"):
        pull(CryptRemote(str(remote_dir), "a-different-key", RCLONE), destination)
    assert not (destination / MARKER).exists()


@pytest.mark.criterion("A-14")
def test_tampered_remote_file_is_rejected(primary: Primary) -> None:
    take_base_backup(primary.repo, port=primary.instance.port)
    primary.switch_and_wait()
    remote_dir = primary.root / "remote-folder"
    remote = CryptRemote(str(remote_dir), KEY, RCLONE)
    push(primary.repo, remote)
    victim = max((p for p in remote_dir.rglob("*") if p.is_file()), key=lambda p: p.stat().st_size)
    data = bytearray(victim.read_bytes())
    data[len(data) // 2] ^= 0x01
    victim.write_bytes(bytes(data))
    with pytest.raises(OffsiteError):
        pull(remote, primary.root / "pulled-tampered")


@pytest.mark.criterion("A-13")
def test_immutable_copy_refuses_a_changed_file(primary: Primary) -> None:
    take_base_backup(primary.repo, port=primary.instance.port)
    remote = CryptRemote(str(primary.root / "remote-folder"), KEY, RCLONE)
    push(primary.repo, remote)
    marker = primary.repo / MARKER
    marker.write_bytes(marker.read_bytes() + b"changed\n")
    with pytest.raises(OffsiteError, match="rclone copy failed"):
        push(primary.repo, remote)
