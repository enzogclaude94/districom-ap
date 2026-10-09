"use client";

import { useQuery } from "@tanstack/react-query";
import { useId } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { todayIso } from "@/lib/format";
import { parseAmount } from "@/modules/settlements/cash/amount";
import { AmountField, TextField, selectClass } from "@/modules/settlements/delivery/form";

import { chequeApi, chequeQueryKey, type Cheque, type ChequeKind, type PaymentType } from "./api";
import { KIND_LABELS, PAYMENT_TYPE_LABELS } from "./labels";

/** What the user types for one cheque, as strings, before it becomes a request body. */
export interface InstrumentDraft {
  kind: ChequeKind;
  paymentType: PaymentType;
  amount: string;
  bankId: number | null;
  branch: string;
  account: string;
  number: string;
  echeqId: string;
  issueDate: string;
  paymentDate: string;
  drawerName: string;
  drawerTaxId: string;
  deliveredBy: string;
  receivedInHand: boolean;
  notes: string;
  confirmNotDuplicate: boolean;
  duplicateReason: string;
}

export function emptyDraft(deliveredBy = ""): InstrumentDraft {
  return {
    kind: "PHYSICAL",
    paymentType: "COMMON",
    amount: "",
    bankId: null,
    branch: "",
    account: "",
    number: "",
    echeqId: "",
    issueDate: todayIso(),
    paymentDate: "",
    drawerName: "",
    drawerTaxId: "",
    deliveredBy,
    receivedInHand: false,
    notes: "",
    confirmNotDuplicate: false,
    duplicateReason: "",
  };
}

export function draftOf(cheque: Cheque): InstrumentDraft {
  return {
    kind: cheque.kind,
    paymentType: cheque.payment_type,
    amount: cheque.amount.replace(".", ","),
    bankId: cheque.issuer_bank_id,
    branch: cheque.branch_code ?? "",
    account: cheque.account_ref ?? "",
    number: cheque.cheque_number,
    echeqId: cheque.echeq_external_id ?? "",
    issueDate: cheque.issue_date,
    paymentDate: cheque.payment_date ?? "",
    drawerName: cheque.drawer_name,
    drawerTaxId: cheque.drawer_tax_id ?? "",
    deliveredBy: cheque.delivered_by_name,
    receivedInHand: cheque.physical_receipt_confirmed_at !== null,
    notes: cheque.notes ?? "",
    confirmNotDuplicate: false,
    duplicateReason: "",
  };
}

/** The first thing the user still has to fill in, or null. The backend validates everything again. */
export function draftProblem(draft: InstrumentDraft): string | null {
  if (!parseAmount(draft.amount)) return "Ingresá el importe del cheque.";
  if (draft.bankId === null) return "Elegí el banco emisor.";
  if (!draft.number.trim()) return "Ingresá el número del cheque.";
  if (!draft.issueDate) return "Indicá la fecha de emisión.";
  if (draft.paymentType === "DEFERRED" && !draft.paymentDate) return "Un cheque diferido necesita su fecha de pago.";
  if (draft.paymentType === "DEFERRED" && draft.paymentDate <= draft.issueDate)
    return "La fecha de pago de un cheque diferido es posterior a su emisión.";
  if (!draft.drawerName.trim()) return "Indicá el titular / librador.";
  if (draft.kind === "ECHEQ" && !draft.drawerTaxId.trim()) return "Un ECHEQ necesita el CUIT del librador.";
  if (!draft.deliveredBy.trim()) return "Indicá quién entregó el cheque.";
  if (draft.kind === "PHYSICAL" && !draft.receivedInHand)
    return "Confirmá «Recibí el cheque físicamente»: un cheque físico se registra solo si está en mano.";
  if (draft.confirmNotDuplicate && !draft.duplicateReason.trim()) return "Indicá por qué no es el mismo cheque.";
  return null;
}

const orNull = (value: string) => value.trim() || null;

/** The instrument fields of the request body (`InstrumentIn`). */
export function draftBody(draft: InstrumentDraft): Record<string, unknown> {
  return {
    kind: draft.kind,
    payment_type: draft.paymentType,
    amount: parseAmount(draft.amount),
    issuer_bank_id: draft.bankId,
    branch_code: draft.kind === "PHYSICAL" ? orNull(draft.branch) : null,
    account_ref: orNull(draft.account),
    cheque_number: draft.number.trim(),
    echeq_external_id: draft.kind === "ECHEQ" ? orNull(draft.echeqId) : null,
    issue_date: draft.issueDate,
    payment_date: draft.paymentType === "DEFERRED" ? draft.paymentDate : null,
    drawer_name: draft.drawerName.trim(),
    drawer_tax_id: orNull(draft.drawerTaxId),
    delivered_by_name: draft.deliveredBy.trim(),
    physical_receipt_confirmed: draft.kind === "PHYSICAL" && draft.receivedInHand,
    notes: orNull(draft.notes),
    confirm_not_duplicate: draft.confirmNotDuplicate,
    duplicate_reason: draft.confirmNotDuplicate ? orNull(draft.duplicateReason) : null,
  };
}

function DateInput({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  const id = useId();
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} type="date" value={value} onChange={(event) => onChange(event.target.value)} />
    </div>
  );
}

function Choice<T extends string>({
  legend,
  name,
  value,
  options,
  onChange,
}: {
  legend: string;
  name: string;
  value: T;
  options: Record<T, string>;
  onChange: (value: T) => void;
}) {
  return (
    <div className="space-y-1.5" role="radiogroup" aria-label={legend}>
      <p className="text-sm font-medium">{legend}</p>
      <div className="flex flex-wrap gap-4">
        {(Object.keys(options) as T[]).map((option) => (
          <label key={option} className="flex items-center gap-2 text-sm">
            <input type="radio" name={name} checked={value === option} onChange={() => onChange(option)} />
            {options[option]}
          </label>
        ))}
      </div>
    </div>
  );
}

/**
 * The data of one cheque, physical or ECHEQ, common or deferred. «Titular / librador» (who signs it) is
 * kept apart from who delivered it (who answers if it bounces). A physical cheque is registered only once
 * someone confirms having it in hand; an ECHEQ is declared and verified later.
 */
export function InstrumentFields({
  draft,
  onChange,
  possibleDuplicate = false,
  lockKind = false,
}: {
  draft: InstrumentDraft;
  onChange: (draft: InstrumentDraft) => void;
  possibleDuplicate?: boolean;
  /** In a correction the kind of instrument cannot change. */
  lockKind?: boolean;
}) {
  const bankId = useId();
  const banks = useQuery({ queryKey: [...chequeQueryKey, "banks"], queryFn: chequeApi.banks, staleTime: 300_000 });
  const set = <K extends keyof InstrumentDraft>(key: K, value: InstrumentDraft[K]) =>
    onChange({ ...draft, [key]: value });
  const echeq = draft.kind === "ECHEQ";
  const active = (banks.data ?? []).filter((b) => b.active || b.id === draft.bankId);

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        {lockKind ? (
          <p className="text-sm">
            Tipo: <strong>{KIND_LABELS[draft.kind]}</strong>
          </p>
        ) : (
          <Choice
            legend="Tipo"
            name="cheque-kind"
            value={draft.kind}
            options={KIND_LABELS}
            onChange={(v) => set("kind", v)}
          />
        )}
        <Choice
          legend="Tipo de pago"
          name="cheque-payment-type"
          value={draft.paymentType}
          options={PAYMENT_TYPE_LABELS}
          onChange={(v) => set("paymentType", v)}
        />
      </div>
      <AmountField label="Importe del cheque" value={draft.amount} onChange={(v) => set("amount", v)} />
      <div className="space-y-1.5">
        <Label htmlFor={bankId}>Banco emisor</Label>
        <select
          id={bankId}
          className={selectClass}
          value={draft.bankId ?? ""}
          onChange={(event) => set("bankId", event.target.value ? Number(event.target.value) : null)}
        >
          <option value="">{banks.isPending ? "Cargando bancos…" : "Elegí el banco…"}</option>
          {active.map((bank) => (
            <option key={bank.id} value={bank.id}>
              {bank.name} ({bank.bcra_code})
            </option>
          ))}
        </select>
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        {!echeq && (
          <TextField label="Sucursal" value={draft.branch} onChange={(v) => set("branch", v)} maxLength={40} />
        )}
        <TextField
          label={echeq ? "CBU" : "Cuenta / CBU"}
          value={draft.account}
          onChange={(v) => set("account", v)}
          maxLength={60}
        />
        <TextField label="Número" value={draft.number} onChange={(v) => set("number", v)} maxLength={40} />
      </div>
      {echeq && (
        <TextField
          label="Identificador ECHEQ (si el banco lo muestra)"
          value={draft.echeqId}
          onChange={(v) => set("echeqId", v)}
          maxLength={80}
        />
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <DateInput label="Fecha de emisión" value={draft.issueDate} onChange={(v) => set("issueDate", v)} />
        {draft.paymentType === "DEFERRED" && (
          <DateInput label="Fecha de pago" value={draft.paymentDate} onChange={(v) => set("paymentDate", v)} />
        )}
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Titular / librador"
          value={draft.drawerName}
          onChange={(v) => set("drawerName", v)}
          maxLength={200}
        />
        <TextField
          label={echeq ? "CUIT del librador" : "CUIT del librador (opcional)"}
          value={draft.drawerTaxId}
          onChange={(v) => set("drawerTaxId", v)}
          maxLength={20}
        />
      </div>
      <TextField
        label="Quién lo entregó"
        value={draft.deliveredBy}
        onChange={(v) => set("deliveredBy", v)}
        maxLength={200}
        hint="A quién se le reclama si el banco lo rechaza. Puede ser distinto del librador."
      />
      {echeq ? (
        <p className="rounded-md border border-warning/40 bg-warning-soft px-3 py-2 text-sm">
          El ECHEQ queda <strong>declarado</strong>, pendiente de verificación. No justifica nada hasta verificarlo.
        </p>
      ) : (
        <label className="flex items-start gap-2 rounded-md border border-border px-3 py-2 text-sm">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={draft.receivedInHand}
            onChange={(event) => set("receivedInHand", event.target.checked)}
          />
          <span>
            <strong>Recibí el cheque físicamente</strong>
            <span className="block text-xs text-muted-foreground">Queda registrado quién lo confirmó y cuándo.</span>
          </span>
        </label>
      )}
      <TextField label="Notas (opcional)" value={draft.notes} onChange={(v) => set("notes", v)} multiline />
      {possibleDuplicate && (
        <div className="space-y-2 rounded-md border border-warning/40 bg-warning-soft px-3 py-2 text-sm">
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={draft.confirmNotDuplicate}
              onChange={(event) => set("confirmNotDuplicate", event.target.checked)}
            />
            <span>Es otro cheque, no el que ya está registrado con el mismo banco y número.</span>
          </label>
          {draft.confirmNotDuplicate && (
            <TextField
              label="¿Por qué es otro cheque?"
              value={draft.duplicateReason}
              onChange={(v) => set("duplicateReason", v)}
              multiline
            />
          )}
        </div>
      )}
    </div>
  );
}
