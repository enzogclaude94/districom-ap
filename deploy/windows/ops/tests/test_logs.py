"""C1-3: logs without secrets or personal data."""

import json
import logging
from pathlib import Path

import pytest

from districom_ops.logs import Redactor, setup_logging
from districom_ops.oplog import Kind, OperationsLog

PASSWORD = "Fict1cia-ops-7d1x0PzW"
URL = f"postgresql://districom_ops:{PASSWORD}@127.0.0.1:5432/postgres"


@pytest.fixture
def redactor() -> Redactor:
    r = Redactor()
    r.register(URL, PASSWORD)
    return r


def lines(log_dir: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in (log_dir / "ops.log").read_text(encoding="utf-8").splitlines()]


@pytest.mark.criterion("C1-3")
def test_registered_secrets_and_credentials_never_reach_the_log(tmp_path: Path, redactor: Redactor) -> None:
    logger = setup_logging(tmp_path, redactor=redactor, stream=False)
    logger.info("connecting to %s", URL)
    logger.info("retry", extra={"detail": f"password={PASSWORD}", "url": "https://user:hunter22@example.com/x"})
    logger.info("fields", extra={"db_password": "anything", "api_token": "abc", "nested": {"secret": "s", "ok": 1}})
    logger.info("free text", extra={"note": "token: abcdef123 and pwd=qwerty"})
    text = (tmp_path / "ops.log").read_text(encoding="utf-8")
    for leaked in (PASSWORD, "hunter22", "anything", "abcdef123", "qwerty"):
        assert leaked not in text
    records = lines(tmp_path)
    assert records[0]["event"] == "connecting to ***"
    assert records[1]["url"] == "https://***@example.com/x"
    assert records[2]["db_password"] == "***"
    assert records[2]["nested"] == {"secret": "***", "ok": 1}


@pytest.mark.criterion("C1-3")
def test_cuit_is_masked_and_ordinary_numbers_are_kept(redactor: Redactor) -> None:
    assert redactor.text("cliente 20-12345678-3 y 30123456789") == "cliente [CUIT] y [CUIT]"
    assert (
        redactor.text("LSN 0/16DCA690 a las 2026-11-06 12:00, 1024 MB")
        == "LSN 0/16DCA690 a las 2026-11-06 12:00, 1024 MB"
    )


@pytest.mark.criterion("C1-3")
def test_an_exception_is_logged_by_type_and_redacted_message(tmp_path: Path, redactor: Redactor) -> None:
    logger = setup_logging(tmp_path, redactor=redactor, stream=False)
    try:
        raise RuntimeError(f"could not connect with {URL}")
    except RuntimeError:
        logger.exception("connect_failed")
    record = lines(tmp_path)[-1]
    assert record["error"] == "RuntimeError"
    assert PASSWORD not in json.dumps(record)
    assert "Traceback" not in (tmp_path / "ops.log").read_text(encoding="utf-8")


@pytest.mark.criterion("C1-3")
def test_the_operations_record_is_redacted_too(tmp_path: Path) -> None:
    from districom_ops.logs import REDACTOR

    REDACTOR.register(PASSWORD)
    oplog = OperationsLog(tmp_path / "operations.jsonl")
    op_id = oplog.start(Kind.MANUAL, f"prueba con {PASSWORD} y CUIT 20-12345678-3")
    oplog.step(op_id, "connect", password=PASSWORD)
    text = oplog.path.read_text(encoding="utf-8")
    assert PASSWORD not in text
    assert "12345678" not in text


def test_setup_logging_is_idempotent(tmp_path: Path) -> None:
    logger = setup_logging(tmp_path, stream=False)
    setup_logging(tmp_path, stream=False)
    assert len(logger.handlers) == 1
    logging.getLogger("districom_ops").info("once")
    assert len(lines(tmp_path)) == 1
