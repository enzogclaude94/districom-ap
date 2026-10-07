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
            "cheque_presentation_days",
            30,
            "Plazo de presentación de cheques (días)",
            "Días para presentar un cheque al banco desde que es pagadero: desde su emisión si es común, desde "
            "su fecha de pago si es diferido. Pasado el plazo, el cheque está vencido.",
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
