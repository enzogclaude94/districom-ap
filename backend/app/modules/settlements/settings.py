"""Rendiciones parameters. Full keys are `settlements.<key>`."""

from app.modules.settlements.delivery.denominations import DEFAULT_DENOMINATIONS
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
        SettingSpec(
            "cash_denominations",
            DEFAULT_DENOMINATIONS,
            "Denominaciones de efectivo",
            "Billetes que se cuentan en una rendición, con su caja sugerida. No se usan monedas ni billetes de "
            "menos de $100. Se editan desde Rendiciones: una denominación se desactiva, nunca se borra.",
        ),
    ),
)
