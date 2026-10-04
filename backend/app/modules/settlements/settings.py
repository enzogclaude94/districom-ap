"""Rendiciones parameters. Full keys are `settlements.<key>`."""

from app.platform.settings.registry import SettingSpec, SettingsScope

SETTINGS = SettingsScope(
    key="settlements",
    label="Rendiciones",
    specs=(
        SettingSpec(
            "pending_alert_hours",
            24,
            "Alerta de pendientes de cobro (horas)",
            "Un pendiente sin acreditar alerta al superar este plazo y vuelve a alertar cada vez que se cumple.",
        ),
    ),
)
