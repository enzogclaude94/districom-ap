"""Safe defaults for commercial parameters (METRICS.md §6).

`app_settings` only stores overrides, so every key always resolves to a value.
"""

from typing import Any

DEFAULTS: dict[str, Any] = {
    "currency": "ARS",
    "business_timezone": "America/Argentina/Buenos_Aires",
    "week_start": "MONDAY",
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
