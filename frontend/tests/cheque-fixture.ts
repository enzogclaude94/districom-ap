import { todayIso } from "@/lib/format";
import type { Cheque } from "@/modules/settlements/cheques/api";

/** A cheque of the portfolio as the API returns it, shared by the cheque and payment tests. */
export function shift(days: number) {
  const date = new Date(`${todayIso()}T12:00:00`);
  date.setDate(date.getDate() + days);
  return date.toISOString().slice(0, 10);
}

export function cheque(changes: Partial<Cheque> = {}): Cheque {
  return {
    id: 21,
    kind: "PHYSICAL",
    payment_type: "COMMON",
    amount: "100000.00",
    issuer_bank_id: 7,
    bank_bcra_code: 7,
    bank_name: "Banco de Galicia",
    branch_code: "001",
    account_ref: null,
    cheque_number: "00012345",
    echeq_external_id: null,
    identity_complete: true,
    issue_date: shift(-5),
    payment_date: null,
    payable_from: shift(-5),
    presentation_deadline: shift(25),
    presentation_expired: false,
    expires_in_days: 25,
    expiring_soon: false,
    location_label: "En Cartera de cheques",
    deposit_id: null,
    deposit_business_date: null,
    deposit_bank_account_id: null,
    deposit_bank_account_name: null,
    credit_id: null,
    credit_business_date: null,
    delivery_payment_id: null,
    delivery_third_party_id: null,
    delivery_third_party_name: null,
    delivery_business_date: null,
    drawer_name: "Don José SRL",
    drawer_tax_id: null,
    delivered_by_name: "Almacén Don José",
    origin_type: "SETTLEMENT",
    settlement_id: 7,
    origin_pending_id: null,
    received_business_date: shift(-5),
    received_by_employee_id: 4,
    received_by_name: "Pedro Gómez",
    physical_receipt_confirmed_by_name: "Oscar Operador",
    physical_receipt_confirmed_at: "2026-10-04T12:00:00Z",
    verification_status: null,
    verification_changed_by_name: null,
    verification_changed_at: null,
    verification_reason: null,
    notes: null,
    state: "IN_PORTFOLIO",
    state_label: "En cartera",
    published: true,
    in_effect: true,
    load_change: null,
    load_change_detail: null,
    created_by_name: "Oscar Operador",
    created_at: "2026-10-04T12:00:00Z",
    voided: false,
    voided_by_name: null,
    voided_at: null,
    void_reason: null,
    ...changes,
  };
}
