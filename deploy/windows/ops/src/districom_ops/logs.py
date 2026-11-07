"""Structured logging without sensitive data: one JSON object per line in ``<root>\\logs\\ops.log``.

The operations tools never handle customer data, so a log line holds an event name and the fields the
code passes. Even so, every line goes through :class:`Redactor` before it is written:

* every secret value registered by the configuration (database URLs and their passwords) → ``***``;
* credentials inside any URL (``scheme://user:password@``) → ``scheme://***@``;
* ``password=...``, ``secret=...``, ``token=...`` pairs in free text → ``password=***``;
* fields whose name says they hold a secret (``password``, ``secret``, ``token``, ``key``) → ``***``;
* a CUIT/CUIL (``20-12345678-3`` or 11 digits) → ``[CUIT]``.

Nothing is printed with ``print``; the CLI writes its result to stdout through this logger too.
"""

import json
import logging
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SECRET_FIELD = re.compile(r"pass(word)?|secret|token|(^|_)key($|_)|credential", re.IGNORECASE)
URL_CREDENTIALS = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/@\s:]+(:[^/@\s]*)?@", re.IGNORECASE)
KEY_VALUE = re.compile(r"(?P<key>\b(?:password|passwd|pwd|secret|token)\b\s*[=:]\s*)\S+", re.IGNORECASE)
CUIT = re.compile(r"\b(?:20|23|24|25|26|27|30|33|34)-?\d{8}-?\d\b")
LOGGER_NAME = "districom_ops"


class Redactor:
    def __init__(self) -> None:
        self._secrets: set[str] = set()

    def register(self, *values: str) -> None:
        self._secrets.update(v for v in values if v)

    def text(self, value: str) -> str:
        for secret in sorted(self._secrets, key=len, reverse=True):  # longest first: a URL before its password
            value = value.replace(secret, "***")
        value = URL_CREDENTIALS.sub(lambda m: f"{m['scheme']}***@", value)
        value = KEY_VALUE.sub(lambda m: f"{m['key']}***", value)
        return CUIT.sub("[CUIT]", value)

    def value(self, key: str, value: Any) -> Any:
        if SECRET_FIELD.search(key):
            return "***"
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {k: self.value(str(k), v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [self.value(key, v) for v in value]
        if isinstance(value, int | float | bool) or value is None:
            return value
        return self.text(str(value))


REDACTOR = Redactor()
_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self.redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        fields = {k: v for k, v in vars(record).items() if k not in _RESERVED}
        payload: dict[str, Any] = {
            "at": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "event": self.redactor.text(record.getMessage()),
            **{k: self.redactor.value(k, v) for k, v in fields.items()},
        }
        if record.exc_info and record.exc_info[0] is not None:
            # The exception type and its redacted message; never the traceback's local values.
            payload["error"] = record.exc_info[0].__name__
            payload["error_message"] = self.redactor.text(str(record.exc_info[1]))
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(log_dir: Path | None, *, redactor: Redactor = REDACTOR, stream: bool = True) -> logging.Logger:
    """Log to ``<log_dir>/ops.log`` (and to stderr). Idempotent: calling it again replaces the handlers."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    formatter = JsonFormatter(redactor)
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "ops.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    if stream:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
    return logger
