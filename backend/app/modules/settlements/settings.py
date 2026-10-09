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
            "Un pendiente vigente con saldo alerta cuando pasaron más de estas horas desde que se registró.",
        ),
        SettingSpec(
            "cheque_expiring_soon_days",
            7,
            "Cheques por vencer (días)",
            "Un cheque en cartera se marca «Vence en N días» cuando faltan estos días o menos para su último día "
            "de presentación. Solo cambia el aviso: los plazos del cheque son reglas fijas y no se configuran.",
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
