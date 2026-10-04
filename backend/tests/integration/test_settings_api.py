from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.platform.settings.models import AppSetting


def _scopes(client: TestClient) -> dict[str, dict[str, dict]]:  # type: ignore[type-arg]
    response = client.get("/api/v1/settings")
    assert response.status_code == 200, response.text
    return {scope["scope"]: {s["key"]: s for s in scope["settings"]} for scope in response.json()}


def test_admin_sees_platform_and_commercial_scopes_with_defaults(login) -> None:  # type: ignore[no-untyped-def]
    scopes = _scopes(login("admin"))
    assert list(scopes) == ["platform", "commercial"]
    assert scopes["platform"]["platform.currency"] == {
        "key": "platform.currency",
        "label": "Moneda",
        "description": "",
        "value": "ARS",
        "is_default": True,
    }
    assert scopes["commercial"]["commercial.active_window_days"]["value"] == 90


def test_scopes_do_not_mix_parameters(login) -> None:  # type: ignore[no-untyped-def]
    scopes = _scopes(login("admin"))
    for scope, settings in scopes.items():
        assert all(key.startswith(f"{scope}.") for key in settings)
    all_keys = {key for settings in scopes.values() for key in settings}
    # The business timezone is installation config (BUSINESS_TIMEZONE), not an editable parameter.
    assert not any(key.endswith("business_timezone") for key in all_keys)


def test_stored_override_wins_over_default(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    client = login("admin")
    db.add(AppSetting(key="commercial.active_window_days", value=120))
    db.commit()
    setting = _scopes(client)["commercial"]["commercial.active_window_days"]
    assert setting["value"] == 120
    assert setting["is_default"] is False
