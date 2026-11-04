"""Base backups, verification and point-in-time recovery (A-6 to A-10, A-16)."""

import shutil
import time

import pytest

from districom_backup.base_backup import is_valid_backup, list_backups, take_base_backup
from districom_backup.compare import compare, fingerprint
from districom_backup.restore import RestoreStatus
from tests.integration.support import DBNAME, Primary


@pytest.mark.criterion("A-6")
def test_base_backup_is_verified_and_listed(primary: Primary) -> None:
    primary.write(200, "seed")
    info = take_base_backup(primary.repo, port=primary.instance.port)
    path = info.path(primary.repo)
    assert (path / "backup_manifest").is_file()
    assert (path / "backup_label").is_file()
    assert is_valid_backup(path) == (True, "backup successfully verified")
    assert [b.label for b in list_backups(primary.repo)] == [info.label]
    assert info.timeline == 1
    assert not [p for p in (primary.repo / "base").iterdir() if p.name.startswith(".tmp-")]


@pytest.mark.criterion("A-7")
def test_altered_backup_is_detected(primary: Primary) -> None:
    primary.write(200, "seed")
    info = take_base_backup(primary.repo, port=primary.instance.port)
    damaged = primary.root / "damaged"
    shutil.copytree(info.path(primary.repo), damaged)
    victim = max((p for p in (damaged / "base").rglob("*") if p.is_file()), key=lambda p: p.stat().st_size)
    data = bytearray(victim.read_bytes())
    data[len(data) // 2] ^= 0xFF  # one byte, same size
    victim.write_bytes(bytes(data))
    ok, message = is_valid_backup(damaged)
    assert not ok
    assert "checksum mismatch" in message

    missing = primary.root / "missing"
    shutil.copytree(info.path(primary.repo), missing)
    (missing / victim.relative_to(damaged)).unlink()
    ok, message = is_valid_backup(missing)
    assert not ok
    assert "not on disk" in message


@pytest.mark.criterion("A-8")
def test_pitr_to_a_time_before_a_known_operation(primary: Primary) -> None:
    backup = take_base_backup(primary.repo, port=primary.instance.port)
    primary.write(300, "before")
    expected = primary.fingerprint()
    time.sleep(1.2)
    target = str(primary.scalar("SELECT clock_timestamp()::text"))
    time.sleep(1.2)
    with primary.connect() as conn:
        conn.execute("INSERT INTO poc.movements (account_id, amount, tag) VALUES (2, 999999.99, 'known-operation')")
    primary.write(100, "after")
    primary.switch_and_wait()

    outcome = primary.restore(backup, "time", target_time=target)
    assert outcome.status is RestoreStatus.COMPLETE, outcome.detail + "\n" + "\n".join(outcome.log_tail)
    restored = outcome.instance
    count = restored.psql("SELECT count(*) FROM poc.movements WHERE tag = 'known-operation'", dbname=DBNAME)
    assert count == "0"
    assert compare(expected, fingerprint(restored, DBNAME)) == ("IDENTICAL", [])


@pytest.mark.criterion("A-9")
def test_pitr_to_a_named_restore_point(primary: Primary) -> None:
    backup = take_base_backup(primary.repo, port=primary.instance.port)
    primary.write(300, "day")
    expected = primary.fingerprint()
    primary.scalar("SELECT pg_create_restore_point('cierre-20261104')", "postgres")
    primary.write(200, "next-day")
    primary.switch_and_wait()

    outcome = primary.restore(backup, "name", target_name="cierre-20261104")
    assert outcome.status is RestoreStatus.COMPLETE, outcome.detail + "\n" + "\n".join(outcome.log_tail)
    assert compare(expected, fingerprint(outcome.instance, DBNAME)) == ("IDENTICAL", [])
    assert primary.fingerprint() != expected  # the source moved on: the match is not trivial


@pytest.mark.criterion("A-10")
def test_restore_to_the_end_of_the_archive(primary: Primary) -> None:
    backup = take_base_backup(primary.repo, port=primary.instance.port)
    for batch in range(3):
        primary.write(200, f"batch-{batch}")
        primary.switch_and_wait()
    primary.write(50, "last")
    expected = primary.fingerprint()
    primary.switch_and_wait()

    outcome = primary.restore(backup, "end")
    assert outcome.status is RestoreStatus.COMPLETE, outcome.detail
    assert compare(expected, fingerprint(outcome.instance, DBNAME)) == ("IDENTICAL", [])


NAMES = ["oso", "ñandú", "Álamo", "nube", "abeja", "árbol", "Ñoño", "zorro"]
SPANISH_ORDER = ["abeja", "Álamo", "árbol", "nube", "ñandú", "Ñoño", "oso", "zorro"]


@pytest.mark.criterion("A-16")
def test_utf8_icu_es_ar_and_text_order_survive_a_restore(primary: Primary) -> None:
    locale = primary.scalar(
        "SELECT datlocprovider::text || ' ' || coalesce(to_jsonb(d) ->> 'datlocale', to_jsonb(d) ->> 'daticulocale') "
        "|| ' ' || pg_encoding_to_char(encoding) FROM pg_database d WHERE datname = current_database()"
    )
    assert locale == "i es-AR UTF8"
    with primary.connect() as conn:
        conn.execute("CREATE TABLE poc.names (name text NOT NULL)")
        for name in NAMES:
            conn.execute("INSERT INTO poc.names VALUES (%s)", (name,))
    order_sql = "SELECT string_agg(name, '|' ORDER BY name) FROM poc.names"
    assert str(primary.scalar(order_sql)).split("|") == SPANISH_ORDER
    backup = take_base_backup(primary.repo, port=primary.instance.port)
    expected = primary.fingerprint()
    primary.switch_and_wait()

    outcome = primary.restore(backup, "icu")
    assert outcome.status is RestoreStatus.COMPLETE, outcome.detail
    assert outcome.instance.psql(order_sql, dbname=DBNAME).split("|") == SPANISH_ORDER
    assert outcome.instance.psql("SELECT name FROM poc.names WHERE name = 'ñandú'", dbname=DBNAME) == "ñandú"
    assert compare(expected, fingerprint(outcome.instance, DBNAME)) == ("IDENTICAL", [])
