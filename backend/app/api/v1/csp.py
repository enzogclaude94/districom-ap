"""CSP violation reports (R9-5-5, O5): the browser posts them while the policy is in `Report-Only`.

The endpoint is open (a browser sends reports without the session or the CSRF header) and only writes a log line,
so what it accepts is narrow:
- at most `MAX_REPORT_BYTES` of body, counted while reading; more is 413 and nothing is logged;
- any body is answered 204: an invalid one is logged as such, never echoed or parsed further;
- from each report it keeps only the directive, a category of what was blocked (never its URL), the disposition
  and the format. URLs (`document-uri`, `blocked-uri`, `source-file`, `referrer`), their query strings, the
  `script-sample` (a snippet of the page) and the policy text are dropped;
- nothing is stored, and at most `MAX_LOG_LINES_PER_MINUTE` lines per process reach the log.
"""

import json
import logging
import re
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, Response

from app.core.body_limit import RequestTooLarge

router = APIRouter(tags=["security"])
logger = logging.getLogger("app.csp")

REPORT_PATH = "/api/v1/csp-report"
MAX_REPORT_BYTES = 16 * 1024
MAX_REPORTS_PER_BODY = 20
MAX_LOG_LINES_PER_MINUTE = 60

_DIRECTIVE_RE = re.compile(r"^[a-z][a-z-]{0,39}$")
_KEYWORDS = {"inline", "eval", "wasm-eval", "trusted-types-policy", "trusted-types-sink"}
_SCHEMES = {"data", "blob", "filesystem", "about", "chrome-extension", "moz-extension"}


class _LogBudget:
    """A per-process cap on report lines, so an open endpoint cannot flood the log."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._lock = threading.Lock()
        self._window = 0
        self._used = 0

    def take(self) -> bool:
        window = int(time.monotonic() // 60)
        with self._lock:
            if window != self._window:
                self._window, self._used = window, 0
            if self._used >= self.per_minute:
                return False
            self._used += 1
            return True


budget = _LogBudget(MAX_LOG_LINES_PER_MINUTE)


def _origin(url: str) -> tuple[str, str | None, int | None] | None:
    try:
        parts = urlsplit(url)
        return (parts.scheme, parts.hostname, parts.port)
    except ValueError:
        return None


def _blocked_category(blocked: object, document: object) -> str:
    """What was blocked, as a category: a CSP keyword, a URL scheme, `same-origin` or `external`. Never the URL."""
    if not isinstance(blocked, str) or not blocked:
        return "none"
    if blocked in _KEYWORDS:
        return blocked
    scheme = blocked.split(":", 1)[0].lower() if ":" in blocked else ""
    if scheme in _SCHEMES:
        return scheme
    if scheme in ("http", "https", "ws", "wss"):
        own = _origin(document) if isinstance(document, str) else None
        return "same-origin" if own is not None and _origin(blocked) == own else "external"
    return "other"


def _directive(report: dict[str, Any]) -> str:
    for key in ("effective-directive", "effectiveDirective", "violated-directive", "violatedDirective"):
        value = report.get(key)
        if isinstance(value, str) and value:
            token = value.split()[0].lower()
            return token if _DIRECTIVE_RE.match(token) else "unknown"
    return "unknown"


def _summaries(payload: object) -> tuple[str, list[dict[str, str]]] | None:
    """The allowed fields of each report, or None when the payload is not a CSP report in either format."""
    if isinstance(payload, dict) and isinstance(payload.get("csp-report"), dict):  # report-uri
        reports, kind = [payload["csp-report"]], "csp-report"
    elif isinstance(payload, list):  # Reporting API (report-to): a batch, CSP ones among them
        reports = [
            item["body"]
            for item in payload[:MAX_REPORTS_PER_BODY]
            if isinstance(item, dict) and item.get("type") == "csp-violation" and isinstance(item.get("body"), dict)
        ]
        kind = "reports+json"
        if not reports:
            return None
    else:
        return None
    summaries = []
    for report in reports:
        disposition = report.get("disposition")
        summaries.append(
            {
                "directive": _directive(report),
                "blocked": _blocked_category(
                    report.get("blocked-uri", report.get("blockedURL")),
                    report.get("document-uri", report.get("documentURL")),
                ),
                "disposition": disposition if disposition in ("enforce", "report") else "unknown",
            }
        )
    return kind, summaries


async def _read_capped(request: Request) -> bytes:
    received = bytearray()
    async for chunk in request.stream():
        received += chunk
        if len(received) > MAX_REPORT_BYTES:
            raise RequestTooLarge()
    return bytes(received)


@router.post(REPORT_PATH.removeprefix("/api/v1"), status_code=204, include_in_schema=False)
async def csp_report(request: Request) -> Response:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_REPORT_BYTES:
        raise RequestTooLarge()
    body = await _read_capped(request)
    try:
        parsed = _summaries(json.loads(body))
    except (ValueError, RecursionError):
        parsed = None
    if parsed is None:
        if budget.take():
            logger.warning("csp_report_invalid", extra={"bytes": len(body)})
        return Response(status_code=204)
    kind, summaries = parsed
    for summary in summaries:
        if not budget.take():
            break
        logger.warning("csp_violation", extra={**summary, "format": kind})
    return Response(status_code=204)
