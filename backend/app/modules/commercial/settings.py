"""Inteligencia Comercial parameters (METRICS.md §6). Full keys are `commercial.<key>`."""

from app.platform.settings.registry import SettingSpec, SettingsScope

SETTINGS = SettingsScope(
    key="commercial",
    label="Inteligencia Comercial",
    specs=(
        SettingSpec("history_start_date", "2026-01-01", "Inicio de historia confiable"),
        SettingSpec("new_customer_warmup_days", 60, "Días de calentamiento para clientes nuevos"),
        SettingSpec("active_window_days", 90, "Ventana de cliente activo (días)"),
        SettingSpec("penetration_window_days", 90, "Ventana de penetración (días)"),
        SettingSpec("min_segment_size", 10, "Tamaño mínimo de segmento"),
        SettingSpec("conversion_window_days", 2, "Ventana visita → factura (días)"),
        SettingSpec("frequency_lookback_days", 180, "Historia para frecuencia (días)"),
        SettingSpec("min_peer_group_size", 8, "Tamaño mínimo de grupo de pares"),
        SettingSpec("default_expected_interval_days", 14, "Intervalo de compra por defecto (días)"),
        SettingSpec(
            "status_thresholds",
            {"attention": 1.25, "at_risk": 1.75, "dormant": 2.5, "lost": 4.0},
            "Umbrales de estado de cliente (overdue ratio)",
        ),
        SettingSpec("min_days_for_at_risk", 7, "Días mínimos para “en riesgo”"),
        SettingSpec(
            "opportunity_priority_thresholds", {"high": 70, "medium": 45}, "Umbrales de prioridad de oportunidades"
        ),
        SettingSpec("opportunity_attribution_window_days", 30, "Ventana de atribución de oportunidades (días)"),
        SettingSpec("category_level_for_mix", 1, "Nivel de categoría para mix"),
    ),
)
