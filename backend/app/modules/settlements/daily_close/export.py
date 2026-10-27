"""CSV of «Cierre del día» (R9-2): the same view, flattened. `;` separated, decimal comma and a UTF-8 BOM, so a
spreadsheet in Spanish (Argentina) opens it as numbers."""

import csv
import io
from decimal import Decimal

from app.modules.settlements.daily_close import schemas as s

KIND_LABELS = {"CASH_BOX": "Caja", "BANK": "Banco"}
STATE_LABELS = {"NOT_CLOSED": "Sin cerrar", "CLOSED": "Cerrada", "BEING_CORRECTED": "En corrección"}
TYPE_LABELS = {"DELIVERY": "Reparto", "OUTLET": "Boca"}
HEADER = ["Sección", "Cuenta o rendición", "Tipo", "Estado", "Concepto", "Operaciones", "Importe"]


def text(value: str) -> str:
    """A cell that starts with a name typed by someone (an account) never starts a formula in a spreadsheet."""
    return f"'{value}" if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def amount(value: Decimal | None) -> str:
    return "" if value is None else f"{value:.2f}".replace(".", ",")


def _origin(origin: s.ExpectedOriginOut | None) -> str:
    if origin is None:
        return "Sin esperado"
    if origin.source == "STORE_SYS":
        return f"Store Sys · Ref. {origin.external_reference} rev. {origin.revision}"
    return "Manual · anterior a integración Store Sys" if origin.legacy else "Manual"


def csv_text(view: s.DailyCloseOut) -> str:
    out = io.StringIO()
    out.write("\ufeff")
    w = csv.writer(out, delimiter=";", lineterminator="\r\n")
    w.writerow(["Cierre del día", view.business_date.isoformat(), "Vista calculada: no firmada ni congelada"])
    w.writerow(["Arqueo físico", "No registrado"])
    if not view.reconciled:
        w.writerow(["ERROR DE INVARIANTE", "Saldo inicial + movimiento neto no coincide con el saldo final"])
    w.writerow([])
    w.writerow(HEADER)
    for a in view.accounts:
        name = text(a.name)
        kind = KIND_LABELS[a.kind]
        state = "Activa" if a.active else "Inactiva"
        for concept, value in (
            ("Saldo inicial contable", a.opening_balance),
            ("Ingresos del día", a.inflows),
            ("Egresos del día", a.outflows),
            ("Movimiento neto", a.net),
            ("Saldo final contable", a.closing_balance),
        ):
            w.writerow(["Saldos", name, kind, state, concept, "", amount(value)])
        for c in a.inflow_categories:
            w.writerow(["Ingresos", name, kind, state, c.label, c.operations, amount(c.amount)])
        for c in a.outflow_categories:
            w.writerow(["Egresos", name, kind, state, c.label, c.operations, amount(c.amount)])
    for t in view.totals:
        label = f"Total {'bancos' if t.kind == 'BANK' else 'cajas'}"
        for concept, value in (
            ("Saldo inicial contable", t.opening_balance),
            ("Ingresos del día", t.inflows),
            ("Egresos del día", t.outflows),
            ("Movimiento neto", t.net),
            ("Saldo final contable", t.closing_balance),
        ):
            w.writerow(["Totales", label, KIND_LABELS[t.kind], "", concept, "", amount(value)])
    for item in view.settlements.items:
        name = f"{TYPE_LABELS[item.type]} · {item.name} · Rendición {item.sequence_number}"
        state = STATE_LABELS[item.financial_state]
        w.writerow(["Rendiciones", name, TYPE_LABELS[item.type], state, "Esperado", "", amount(item.expected_amount)])
        w.writerow(
            [
                "Rendiciones",
                name,
                TYPE_LABELS[item.type],
                state,
                "Origen del esperado",
                "",
                _origin(item.expected_origin),
            ]
        )
        if item.justified is not None:
            w.writerow(["Rendiciones", name, TYPE_LABELS[item.type], state, "Justificado", "", amount(item.justified)])
            w.writerow(["Rendiciones", name, TYPE_LABELS[item.type], state, "Diferencia", "", amount(item.difference)])
        if item.expected_update_pending:
            w.writerow(["Rendiciones", name, TYPE_LABELS[item.type], state, "Store Sys pendiente", "", ""])
    ch = view.cheques
    for concept, count, value in (
        ("En cartera al final del día", ch.portfolio_count, ch.portfolio_amount),
        ("Depositados sin acreditar al final del día", ch.in_transit_count, ch.in_transit_amount),
        ("Depositados en el día", ch.deposited_count, ch.deposited_amount),
        ("Acreditados en el día", ch.credited_count, ch.credited_amount),
        ("Rechazados en el día", ch.rejected_count, ch.rejected_amount),
        ("Devueltos en el día", ch.returned_count, ch.returned_amount),
    ):
        w.writerow(["Cheques", "", "", "", concept, count, amount(value)])
    return out.getvalue()
