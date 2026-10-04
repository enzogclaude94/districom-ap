import pytest

from app.modules.commercial.settings import SETTINGS as COMMERCIAL
from app.platform.settings.registry import PLATFORM_SCOPE, SettingSpec, SettingsRegistry, SettingsScope


def test_unknown_or_unscoped_keys_are_rejected() -> None:
    registry = SettingsRegistry()
    registry.register(PLATFORM_SCOPE)
    assert registry.spec("platform.currency").default == "ARS"
    for key in ("currency", "platform.missing", "commercial.active_window_days"):
        with pytest.raises(KeyError):
            registry.spec(key)


def test_registering_a_scope_twice_replaces_it() -> None:
    registry = SettingsRegistry()
    registry.register(SettingsScope("x", "X", (SettingSpec("a", 1, "A"),)))
    registry.register(SettingsScope("x", "X", (SettingSpec("a", 2, "A"),)))
    assert len(registry.scopes()) == 1
    assert registry.spec("x.a").default == 2


def test_commercial_parameters_match_metrics_defaults() -> None:
    """METRICS.md §6 defaults, now owned by the commercial module."""
    defaults = {spec.key: spec.default for spec in COMMERCIAL.specs}
    assert defaults == {
        "history_start_date": "2026-01-01",
        "new_customer_warmup_days": 60,
        "active_window_days": 90,
        "penetration_window_days": 90,
        "min_segment_size": 10,
        "conversion_window_days": 2,
        "frequency_lookback_days": 180,
        "min_peer_group_size": 8,
        "default_expected_interval_days": 14,
        "status_thresholds": {"attention": 1.25, "at_risk": 1.75, "dormant": 2.5, "lost": 4.0},
        "min_days_for_at_risk": 7,
        "opportunity_priority_thresholds": {"high": 70, "medium": 45},
        "opportunity_attribution_window_days": 30,
        "category_level_for_mix": 1,
    }
