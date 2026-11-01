"""R9-5-5 (O5): the CSP report receiver. Open (no session, no CSRF header), bounded in size, tolerant of any body,
logging only the directive, a category of what was blocked, the disposition and the format: never a URL, a query
string, a page snippet or the policy, and nothing stored."""

import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import csp
from app.core.logging import JsonFormatter

URL = "/api/v1/csp-report"
PAGE = "https://districom.example.com.ar/rendiciones/clientes?cuit=30712345671&nombre=Juana"
SECRET_BITS = ("30712345671", "Juana", "cuit=", "districom.example.com.ar", "evil.example", "token=abc", "alert(")


def _legacy(**fields: Any) -> dict[str, Any]:
    """A `report-uri` report as Chrome sends it (Content-Type application/csp-report)."""
    report = {
        "document-uri": PAGE,
        "referrer": "https://districom.example.com.ar/login?next=/rendiciones",
        "violated-directive": "script-src-elem",
        "effective-directive": "script-src-elem",
        "original-policy": "default-src 'self'; report-uri /api/v1/csp-report",
        "disposition": "report",
        "blocked-uri": "https://evil.example/x.js?token=abc",
        "status-code": 200,
        "script-sample": "alert(document.cookie)",
        "source-file": PAGE,
        "line-number": 12,
    }
    report.update(fields)
    return {"csp-report": report}


def _post(client: TestClient, body: object | bytes, content_type: str = "application/csp-report") -> Any:
    content = body if isinstance(body, bytes) else json.dumps(body).encode()
    # No X-Requested-With and no session cookie: what a browser sends.
    return client.post(URL, content=content, headers={"Content-Type": content_type})


@pytest.fixture
def raw() -> Iterator[TestClient]:
    from app.main import app

    with TestClient(app) as client:  # no default X-Requested-With header, no login
        yield client


@pytest.fixture(autouse=True)
def budget(monkeypatch: pytest.MonkeyPatch) -> csp._LogBudget:
    """A fresh per-minute log budget for each test (it is per process)."""
    fresh = csp._LogBudget(csp.MAX_LOG_LINES_PER_MINUTE)
    monkeypatch.setattr(csp, "budget", fresh)
    return fresh


def _reports(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in records if r["logger"] == "app.csp"]


def test_a_report_is_logged_with_only_the_allowed_fields(raw: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    response = _post(raw, _legacy())
    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["Cache-Control"] == "no-store"
    formatted = [json.loads(JsonFormatter().format(r)) for r in caplog.records if r.name in ("app.csp", "app.access")]
    (report,) = _reports(formatted)
    assert {k: report[k] for k in ("message", "directive", "blocked", "disposition", "format")} == {
        "message": "csp_violation",
        "directive": "script-src-elem",
        "blocked": "external",
        "disposition": "report",
        "format": "csp-report",
    }
    assert set(report) == {"ts", "level", "logger", "message", "request_id", "directive", "blocked", "disposition",
                           "format"}  # fmt: skip
    text = json.dumps(formatted, ensure_ascii=False)
    for bit in SECRET_BITS:
        assert bit not in text
    (access,) = [r for r in formatted if r["logger"] == "app.access"]
    assert access["path"] == URL
    assert access["queries"] == 0  # nothing stored: not one statement
    assert access["user_id"] is None


def test_the_reporting_api_batch_keeps_only_csp_violations(raw: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    batch = [
        {
            "type": "csp-violation",
            "url": PAGE,
            "body": {
                "documentURL": PAGE,
                "blockedURL": "inline",
                "effectiveDirective": "style-src-attr",
                "disposition": "report",
                "sample": "color: red; /* Juana */",
            },
        },
        {"type": "deprecation", "url": PAGE, "body": {"message": "Juana 30712345671"}},
        {
            "type": "csp-violation",
            "url": PAGE,
            "body": {
                "documentURL": PAGE,
                "blockedURL": "eval",
                "effectiveDirective": "script-src",
                "disposition": "enforce",
            },
        },
    ]
    assert _post(raw, batch, "application/reports+json").status_code == 204
    reports = _reports([json.loads(JsonFormatter().format(r)) for r in caplog.records if r.name == "app.csp"])
    assert [(r["directive"], r["blocked"], r["disposition"], r["format"]) for r in reports] == [
        ("style-src-attr", "inline", "report", "reports+json"),
        ("script-src", "eval", "enforce", "reports+json"),
    ]
    assert all(bit not in caplog.text for bit in SECRET_BITS)


@pytest.mark.parametrize(
    ("blocked", "category"),
    [
        ("inline", "inline"),
        ("eval", "eval"),
        ("data:image/png;base64,AAAA", "data"),
        ("blob:https://districom.example.com.ar/123", "blob"),
        ("https://districom.example.com.ar/api/v1/x?cuit=30712345671", "same-origin"),
        ("https://evil.example/x.js?token=abc", "external"),
        ("wss://evil.example/socket", "external"),
        ("javascript:alert(1)", "other"),
        ("", "none"),
        (12, "none"),
    ],
)
def test_what_was_blocked_is_a_category_never_the_url(
    raw: TestClient, caplog: pytest.LogCaptureFixture, blocked: object, category: str
) -> None:
    caplog.set_level(logging.INFO)
    assert _post(raw, _legacy(**{"blocked-uri": blocked})).status_code == 204
    (record,) = [r for r in caplog.records if r.name == "app.csp"]
    assert record.blocked == category  # type: ignore[attr-defined]
    assert all(bit not in caplog.text for bit in SECRET_BITS)


@pytest.mark.parametrize(
    "directive",
    ["script-src\nINJECTED line", "'; DROP TABLE", "a" * 200, 42],
)
def test_an_odd_directive_is_logged_as_unknown(
    raw: TestClient, caplog: pytest.LogCaptureFixture, directive: object
) -> None:
    caplog.set_level(logging.INFO)
    body = _legacy(**{"effective-directive": directive, "violated-directive": directive})
    assert _post(raw, body).status_code == 204
    (record,) = [r for r in caplog.records if r.name == "app.csp"]
    assert record.directive in ("unknown", "script-src")  # type: ignore[attr-defined]
    assert "INJECTED" not in caplog.text
    assert "DROP" not in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not json at all Juana 30712345671",
        b'{"csp-report": "Juana"}',
        b'{"something": {"cuit": "30712345671"}}',
        b"[1, 2, 3]",
        b'[{"type": "deprecation", "body": {}}]',
        b"[" * 7_000 + b"]" * 7_000,  # nesting deep enough to break a recursive parser, under the size limit
        '{"csp-report": {"blocked-uri": "\udc80"}}'.encode("utf-8", "surrogatepass"),
    ],
    ids=["empty", "text", "string-report", "other-object", "numbers", "other-reports", "deep", "bad-utf8"],
)
def test_an_invalid_body_is_204_and_logged_as_invalid(
    raw: TestClient, caplog: pytest.LogCaptureFixture, body: bytes
) -> None:
    caplog.set_level(logging.INFO)
    response = _post(raw, body, "application/json")
    assert response.status_code == 204
    records = [r for r in caplog.records if r.name == "app.csp"]
    assert [r.getMessage() for r in records] in (["csp_report_invalid"], ["csp_violation"])
    assert "30712345671" not in caplog.text
    assert "Juana" not in caplog.text


def test_a_body_over_the_limit_is_413_and_not_logged(raw: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    big = _legacy(**{"script-sample": "x" * csp.MAX_REPORT_BYTES})
    response = _post(raw, big)
    assert response.status_code == 413
    assert response.json()["code"] == "request_too_large"
    assert not [r for r in caplog.records if r.name == "app.csp"]


def test_a_chunked_body_over_the_limit_is_413(raw: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    chunk = b"x" * 4096

    def endless() -> Iterator[bytes]:
        for _ in range(100):  # 400 KiB, well under the app's 1 MiB limit
            yield chunk

    response = raw.post(URL, content=endless(), headers={"Content-Type": "application/csp-report"})
    assert response.status_code == 413
    assert not [r for r in caplog.records if r.name == "app.csp"]


def test_the_log_is_capped_per_minute(
    raw: TestClient, caplog: pytest.LogCaptureFixture, budget: csp._LogBudget
) -> None:
    budget.per_minute = 3
    caplog.set_level(logging.INFO)
    for _ in range(5):
        assert _post(raw, _legacy()).status_code == 204
    assert len([r for r in caplog.records if r.name == "app.csp"]) == 3


def test_a_report_from_another_origin_is_accepted_without_session(raw: TestClient) -> None:
    response = raw.post(
        URL,
        content=json.dumps(_legacy()).encode(),
        headers={
            "Content-Type": "application/csp-report",
            "Origin": "https://evil.example",
            "Sec-Fetch-Site": "cross-site",
        },
    )
    assert response.status_code == 204


def test_the_csrf_exemption_is_only_this_path(raw: TestClient) -> None:
    """Every other mutating API call still needs the CSRF header."""
    assert raw.post("/api/v1/auth/logout").status_code == 403
    assert raw.post(f"{URL}/extra", content=b"{}").status_code == 403
    assert raw.get(URL).status_code == 405
