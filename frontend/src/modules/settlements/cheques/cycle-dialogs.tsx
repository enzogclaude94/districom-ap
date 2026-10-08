"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import Link from "next/link";
import { useId, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatDate, formatMoney, todayIso } from "@/lib/format";
import { cashApi, cashQueryKey, type Account } from "@/modules/settlements/cash/api";
import { deliveryApi, deliveryQueryKey } from "@/modules/settlements/delivery/api";
import { FormError, PendingAttempt, SelectField, TextField } from "@/modules/settlements/delivery/form";
import { fromCents, toCents } from "@/modules/settlements/delivery/money";
import { pendingHref } from "@/modules/settlements/pending/labels";

import {
  DOCUMENT_PATHS,
  type Cheque,
  type Credit,
  type CycleDocumentType,
  type Deposit,
  type Rejection,
  type Return,
} from "./api";
import { chequeErrors, useChequeAction } from "./use-cheque-action";
import { KIND_LABELS, chequeTitle } from "./labels";

const WHERE = "el cheque y la Cartera de cheques";

function DateField({
  label,
  value,
  onChange,
  min,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  min?: string;
}) {
  const id = useId();
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} type="date" min={min} max={todayIso()} value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

function dateProblem(value: string, min: string | null, what: string) {
  if (!value) return "Indicá la fecha.";
  if (value > todayIso()) return "La fecha no puede ser posterior a hoy.";
  if (min && value < min) return `La fecha no puede ser anterior ${what} (${formatDate(min)}).`;
  return null;
}

function latest(dates: (string | null)[]) {
  return dates.reduce<string | null>((max, d) => (d && (!max || d > max) ? d : max), null);
}

function useBanks() {
  const accounts = useQuery({ queryKey: [...cashQueryKey, "accounts"], queryFn: cashApi.accounts });
  return { ...accounts, banks: (accounts.data ?? []).filter((a: Account) => a.kind === "BANK" && a.active) };
}

function ChequeLines({ cheques }: { cheques: Cheque[] }) {
  const total = cheques.reduce((sum, c) => sum + toCents(c.amount), 0);
  return (
    <div className="rounded-md border border-border">
      <ul className="divide-y divide-border text-sm">
        {cheques.map((cheque) => (
          <li key={cheque.id} className="flex items-center justify-between gap-2 px-3 py-1.5">
            <span>
              {KIND_LABELS[cheque.kind]} {chequeTitle(cheque)}
              <span className="block text-xs text-muted-foreground">Entregó: {cheque.delivered_by_name}</span>
            </span>
            <span className="tabular-nums">{formatMoney(cheque.amount)}</span>
          </li>
        ))}
      </ul>
      <p className="flex justify-between border-t border-border bg-muted px-3 py-1.5 text-sm font-semibold">
        <span>
          Total ({cheques.length} {cheques.length === 1 ? "cheque" : "cheques"})
        </span>
        <span className="tabular-nums" data-testid="cycle-total">
          {formatMoney(fromCents(total))}
        </span>
      </p>
    </div>
  );
}

/**
 * Two steps for every write of the cycle: the form, then a summary that repeats what will move, and only then
 * the request. Nothing is deposited, credited, rejected or returned automatically.
 */
function TwoStepDialog<T>({
  title,
  description,
  action,
  onClose,
  form,
  summary,
  validate,
  confirmLabel,
  send,
}: {
  title: string;
  description: ReactNode;
  action: ReturnType<typeof useChequeAction<T>>;
  onClose: () => void;
  form: ReactNode;
  summary: ReactNode;
  validate: () => string | null;
  confirmLabel: string;
  send: () => void;
}) {
  const [problem, setProblem] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const pending = action.mutation.isPending;
  const error = action.mutation.error ? chequeErrors(action.mutation.error) : null;
  return (
    <Dialog title={title} description={description} onClose={onClose}>
      {action.stored ? (
        <PendingAttempt action={action} onClose={onClose} errorView={chequeErrors} where={WHERE} />
      ) : reviewing ? (
        <div className="space-y-4">
          <div role="group" aria-label="Revisá antes de confirmar" className="space-y-2">
            <p className="text-sm font-medium">Revisá antes de confirmar</p>
            {summary}
          </div>
          <FormError message={error?.message} />
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" disabled={pending} onClick={() => setReviewing(false)}>
              Volver
            </Button>
            <Button disabled={pending} onClick={send}>
              {pending ? "Registrando…" : confirmLabel}
            </Button>
          </div>
        </div>
      ) : (
        <form
          className="space-y-4"
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            const local = validate();
            setProblem(local);
            if (!local) {
              action.mutation.reset();
              setReviewing(true);
            }
          }}
        >
          {form}
          <FormError message={problem ?? error?.message} />
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" onClick={onClose}>
              Cancelar
            </Button>
            <Button type="submit">Revisar</Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}

function Facts({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-md border border-border px-3 py-2 text-sm">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** «Crear boleta de depósito»: the chosen cheques of the portfolio go to «Cheques depositados». */
export function DepositDialog({
  cheques,
  onClose,
  onDone,
}: {
  cheques: Cheque[];
  onClose: () => void;
  onDone: (deposit: Deposit) => void;
}) {
  const { banks, isPending } = useBanks();
  const [bank, setBank] = useState<number | null>(null);
  const [businessDate, setBusinessDate] = useState(todayIso);
  const [slip, setSlip] = useState("");
  const [notes, setNotes] = useState("");
  const action = useChequeAction<Deposit>(`deposit:${cheques.map((c) => c.id).join(",")}`, (deposit) => {
    onDone(deposit);
    onClose();
  });
  const chosen = banks.find((b) => b.id === bank);
  const total = fromCents(cheques.reduce((sum, c) => sum + toCents(c.amount), 0));
  const min = latest(cheques.map((c) => c.received_business_date));
  return (
    <TwoStepDialog
      title="Crear boleta de depósito"
      description="Los cheques salen de Cartera y pasan a Cheques depositados hasta que el banco los acredite. El saldo del banco no cambia todavía."
      action={action}
      onClose={onClose}
      validate={() =>
        bank === null
          ? "Elegí el banco destino."
          : (dateProblem(businessDate, min, "a la entrada a cartera") ??
            cheques
              .map((c) =>
                c.payable_from > businessDate
                  ? `El cheque n.º ${c.cheque_number} recién es pagadero desde el ${formatDate(c.payable_from)}.`
                  : null,
              )
              .find(Boolean) ??
            cheques
              .map((c) =>
                businessDate > c.presentation_deadline
                  ? `El cheque n.º ${c.cheque_number} venció el ${formatDate(c.presentation_deadline)}: no se puede depositar.`
                  : null,
              )
              .find(Boolean) ??
            null)
      }
      form={
        <>
          <ChequeLines cheques={cheques} />
          <SelectField
            label="Banco destino"
            value={bank}
            onChange={setBank}
            placeholder={isPending ? "Cargando…" : "Elegí el banco…"}
            options={banks}
          />
          <DateField
            label="Fecha del depósito"
            value={businessDate}
            onChange={setBusinessDate}
            min={min ?? undefined}
          />
          <TextField label="Número de boleta (opcional)" value={slip} onChange={setSlip} maxLength={40} />
          <TextField label="Notas (opcional)" value={notes} onChange={setNotes} multiline />
        </>
      }
      summary={
        <>
          <ChequeLines cheques={cheques} />
          <Facts
            rows={[
              ["Banco destino", chosen?.name],
              ["Fecha del depósito", formatDate(businessDate)],
              ["Número de boleta", slip.trim() || "—"],
              ["Cartera de cheques", `−${formatMoney(total)}`],
              ["Cheques depositados", `+${formatMoney(total)}`],
              ["Saldo del banco", "Sin cambio hasta la acreditación"],
            ]}
          />
        </>
      }
      confirmLabel="Confirmar depósito"
      send={() =>
        action.submit((requestId) => ({
          method: "POST",
          path: `/${DOCUMENT_PATHS.DEPOSIT}`,
          body: {
            client_request_id: requestId,
            bank_account_id: bank,
            cheque_ids: cheques.map((c) => c.id),
            business_date: businessDate,
            slip_number: slip.trim() || null,
            notes: notes.trim() || null,
          },
          summary: `Depósito de ${cheques.length} ${cheques.length === 1 ? "cheque" : "cheques"} por ${formatMoney(total)} en ${chosen?.name ?? "el banco"}`,
        }))
      }
    />
  );
}

/** The bank credited deposited cheques: «Cheques depositados» ↓, the bank of the deposit ↑. */
export function CreditDialog({
  cheques,
  onClose,
  onDone,
}: {
  cheques: Cheque[];
  onClose: () => void;
  onDone: (credit: Credit) => void;
}) {
  const [businessDate, setBusinessDate] = useState(todayIso);
  const [notes, setNotes] = useState("");
  const action = useChequeAction<Credit>(`credit:${cheques.map((c) => c.id).join(",")}`, (credit) => {
    onDone(credit);
    onClose();
  });
  const banks = new Set(cheques.map((c) => c.deposit_bank_account_id));
  const bankId = cheques[0]?.deposit_bank_account_id ?? null;
  const bankName = cheques[0]?.deposit_bank_account_name ?? "";
  const total = fromCents(cheques.reduce((sum, c) => sum + toCents(c.amount), 0));
  const min = latest(cheques.map((c) => c.deposit_business_date));
  return (
    <TwoStepDialog
      title="Registrar acreditación"
      description="El banco acreditó los cheques: salen de Cheques depositados y el importe entra al saldo del banco."
      action={action}
      onClose={onClose}
      validate={() =>
        banks.size !== 1
          ? "Elegí cheques depositados en un mismo banco: cada uno se acredita en el banco donde se depositó."
          : dateProblem(businessDate, min, "al depósito")
      }
      form={
        <>
          <ul className="divide-y divide-border rounded-md border border-border text-sm">
            {cheques.map((cheque) => (
              <li key={cheque.id} className="flex items-center justify-between gap-2 px-3 py-1.5">
                <span>
                  {chequeTitle(cheque)}
                  <span className="block text-xs text-muted-foreground">
                    Depositado el {formatDate(cheque.deposit_business_date)} en {cheque.deposit_bank_account_name}
                  </span>
                </span>
                <span className="tabular-nums">{formatMoney(cheque.amount)}</span>
              </li>
            ))}
          </ul>
          <DateField
            label="Fecha de acreditación"
            value={businessDate}
            onChange={setBusinessDate}
            min={min ?? undefined}
          />
          <TextField label="Notas (opcional)" value={notes} onChange={setNotes} multiline />
        </>
      }
      summary={
        <Facts
          rows={[
            ["Cheques", String(cheques.length)],
            ["Banco", bankName],
            ["Fecha de acreditación", formatDate(businessDate)],
            ["Cheques depositados", `−${formatMoney(total)}`],
            [bankName || "Banco", `+${formatMoney(total)}`],
          ]}
        />
      }
      confirmLabel="Confirmar acreditación"
      send={() =>
        action.submit((requestId) => ({
          method: "POST",
          path: `/${DOCUMENT_PATHS.CREDIT}`,
          body: {
            client_request_id: requestId,
            bank_account_id: bankId,
            cheque_ids: cheques.map((c) => c.id),
            business_date: businessDate,
            notes: notes.trim() || null,
          },
          summary: `Acreditación de ${cheques.length} ${cheques.length === 1 ? "cheque" : "cheques"} por ${formatMoney(total)} en ${bankName}`,
        }))
      }
    />
  );
}

/** Who answers for the new pending: chosen, or by default the driver of the settlement. */
function ResponsibleField({
  cheque,
  value,
  onChange,
}: {
  cheque: Cheque;
  value: number | null;
  onChange: (value: number | null) => void;
}) {
  const employees = useQuery({ queryKey: [...deliveryQueryKey, "employees"], queryFn: deliveryApi.employees });
  const fallback =
    cheque.origin_type === "SETTLEMENT"
      ? `Por defecto: el repartidor de la rendición${cheque.received_by_name ? ` (${cheque.received_by_name})` : ""}`
      : cheque.origin_type === "PENDING_RECEIPT"
        ? "Por defecto: el responsable del pendiente cobrado con este cheque"
        : null;
  return (
    <SelectField
      label="Responsable del nuevo pendiente"
      value={value}
      onChange={onChange}
      placeholder={fallback ?? "Elegí un empleado…"}
      options={(employees.data ?? []).map((e) => ({ id: e.id, name: e.full_name }))}
    />
  );
}

const needsResponsible = (cheque: Cheque, value: number | null) =>
  value === null && cheque.origin_type !== "SETTLEMENT" && cheque.origin_type !== "PENDING_RECEIPT";

/** The bank rejected a deposited (or credited) cheque: a new pending for who delivered it. */
export function RejectionDialog({
  cheque,
  onClose,
  onDone,
}: {
  cheque: Cheque;
  onClose: () => void;
  onDone: (rejection: Rejection) => void;
}) {
  const [reason, setReason] = useState("");
  const [businessDate, setBusinessDate] = useState(todayIso);
  const [responsible, setResponsible] = useState<number | null>(null);
  const action = useChequeAction<Rejection>(`rejection:${cheque.id}`, onDone);
  const credited = cheque.state === "CREDITED";
  const min = credited ? cheque.credit_business_date : cheque.deposit_business_date;
  const effect = (
    <div className="space-y-2 text-sm">
      <p className="rounded-md border border-border bg-muted px-3 py-2">
        El rechazo volverá a reconocer una deuda por <strong>{formatMoney(cheque.amount)}</strong> a nombre de{" "}
        <strong>{cheque.delivered_by_name}</strong>.
      </p>
      {credited && (
        <p
          className="flex gap-2 rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-danger"
          data-testid="rejection-overdraft-warning"
        >
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          <span>
            El banco será debitado por {formatMoney(cheque.amount)}. Esto puede dejar la cuenta en descubierto.
          </span>
        </p>
      )}
    </div>
  );
  return (
    <TwoStepDialog
      title="Registrar rechazo"
      description={
        credited
          ? `Después de acreditado: el cheque ya estaba en ${cheque.deposit_bank_account_name ?? "el banco"}.`
          : "En tránsito: el cheque estaba depositado y todavía no acreditado."
      }
      action={action}
      onClose={onClose}
      validate={() =>
        !reason.trim()
          ? "Indicá el motivo bancario."
          : needsResponsible(cheque, responsible)
            ? "Elegí el responsable del nuevo pendiente."
            : dateProblem(businessDate, min, credited ? "a la acreditación" : "al depósito")
      }
      form={
        <>
          <p className="text-sm">
            {chequeTitle(cheque)} · <strong>{formatMoney(cheque.amount)}</strong>
            <span className="block text-xs text-muted-foreground">
              {credited ? "Rechazo después de acreditado" : "Rechazo en tránsito"} · Entregó: {cheque.delivered_by_name}
            </span>
          </p>
          <DateField label="Fecha del rechazo" value={businessDate} onChange={setBusinessDate} min={min ?? undefined} />
          <TextField label="Motivo bancario" value={reason} onChange={setReason} multiline />
          <ResponsibleField cheque={cheque} value={responsible} onChange={setResponsible} />
          {effect}
        </>
      }
      summary={
        <>
          <Facts
            rows={[
              ["Etapa", credited ? "Después de acreditado" : "En tránsito"],
              ["Fecha", formatDate(businessDate)],
              ["Motivo", reason.trim()],
              [
                credited ? (cheque.deposit_bank_account_name ?? "Banco") : "Cheques depositados",
                `−${formatMoney(cheque.amount)}`,
              ],
              ["Pendientes de cobro", `+${formatMoney(cheque.amount)}`],
            ]}
          />
          {effect}
        </>
      }
      confirmLabel="Confirmar rechazo"
      send={() =>
        action.submit((requestId) => ({
          method: "POST",
          path: `/cheques/${cheque.id}/rejection`,
          body: {
            client_request_id: requestId,
            reason: reason.trim(),
            business_date: businessDate,
            responsible_employee_id: responsible,
          },
          summary: `Rechazo del cheque n.º ${cheque.cheque_number} por ${formatMoney(cheque.amount)}`,
        }))
      }
    />
  );
}

/** Admin: give a cheque of the portfolio back to who delivered it; its debt is recognized again. */
export function ReturnDialog({
  cheque,
  onClose,
  onDone,
}: {
  cheque: Cheque;
  onClose: () => void;
  onDone: (given: Return) => void;
}) {
  const [reason, setReason] = useState("");
  const [businessDate, setBusinessDate] = useState(todayIso);
  const [responsible, setResponsible] = useState<number | null>(null);
  const action = useChequeAction<Return>(`return:${cheque.id}`, onDone);
  const effect = (
    <p className="rounded-md border border-border bg-muted px-3 py-2 text-sm">
      El cheque saldrá de Cartera y volverá a reconocerse una deuda por <strong>{formatMoney(cheque.amount)}</strong> a
      nombre de <strong>{cheque.delivered_by_name}</strong>.
    </p>
  );
  return (
    <TwoStepDialog
      title="Devolver cheque"
      description="Solo administradores. El cheque vuelve a quien lo entregó y la deuda queda como un pendiente nuevo."
      action={action}
      onClose={onClose}
      validate={() =>
        !reason.trim()
          ? "Indicá el motivo."
          : needsResponsible(cheque, responsible)
            ? "Elegí el responsable del nuevo pendiente."
            : dateProblem(businessDate, cheque.received_business_date, "a la entrada a cartera")
      }
      form={
        <>
          <p className="text-sm">
            {chequeTitle(cheque)} · <strong>{formatMoney(cheque.amount)}</strong>
            {cheque.presentation_expired && <span className="block text-xs text-danger">Vencido</span>}
          </p>
          <DateField
            label="Fecha de la devolución"
            value={businessDate}
            onChange={setBusinessDate}
            min={cheque.received_business_date}
          />
          <TextField label="Motivo" value={reason} onChange={setReason} multiline />
          <ResponsibleField cheque={cheque} value={responsible} onChange={setResponsible} />
          {effect}
        </>
      }
      summary={
        <>
          <Facts
            rows={[
              ["Fecha", formatDate(businessDate)],
              ["Motivo", reason.trim()],
              ["Cartera de cheques", `−${formatMoney(cheque.amount)}`],
              ["Pendientes de cobro", `+${formatMoney(cheque.amount)}`],
            ]}
          />
          {effect}
        </>
      }
      confirmLabel="Confirmar devolución"
      send={() =>
        action.submit((requestId) => ({
          method: "POST",
          path: `/cheques/${cheque.id}/return`,
          body: {
            client_request_id: requestId,
            reason: reason.trim(),
            business_date: businessDate,
            responsible_employee_id: responsible,
          },
          summary: `Devolución del cheque n.º ${cheque.cheque_number} por ${formatMoney(cheque.amount)}`,
        }))
      }
    />
  );
}

const REVERSAL_TITLES: Record<CycleDocumentType, string> = {
  DEPOSIT: "Revertir depósito",
  CREDIT: "Revertir acreditación",
  REJECTION: "Revertir rechazo",
  RETURN: "Revertir devolución",
};

/** Admin: reverse a document of the cycle, with a reason, on its original business date. */
export function ReverseDocumentDialog({
  type,
  id,
  businessDate,
  what,
  onClose,
  onDone,
}: {
  type: CycleDocumentType;
  id: number;
  businessDate: string;
  what: string;
  onClose: () => void;
  onDone?: () => void;
}) {
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const action = useChequeAction<unknown>(`reverse:${type}:${id}`, () => {
    onDone?.();
    onClose();
  });
  const pending = action.mutation.isPending;
  const error = action.mutation.error ? chequeErrors(action.mutation.error) : null;
  return (
    <Dialog
      title={REVERSAL_TITLES[type]}
      description={
        <span className="flex gap-2">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
          <span>
            La corrección se registrará en la fecha financiera original ({formatDate(businessDate)}). El movimiento no
            se eliminará.
          </span>
        </span>
      }
      onClose={onClose}
    >
      {action.stored ? (
        <PendingAttempt action={action} onClose={onClose} errorView={chequeErrors} where={WHERE} />
      ) : (
        <form
          className="space-y-4"
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            const local = reason.trim() ? null : "Indicá el motivo.";
            setProblem(local);
            if (!local)
              action.submit((requestId) => ({
                method: "POST",
                path: `/${DOCUMENT_PATHS[type]}/${id}/reverse`,
                body: { client_request_id: requestId, reason: reason.trim() },
                summary: `${REVERSAL_TITLES[type]}: ${what}`,
              }));
          }}
        >
          <fieldset disabled={pending} className="space-y-4">
            <p className="text-sm">Vas a revertir {what}.</p>
            <TextField label="Motivo" value={reason} onChange={setReason} multiline />
          </fieldset>
          <FormError message={problem ?? error?.message} />
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" onClick={onClose}>
              Cancelar
            </Button>
            <Button type="submit" disabled={pending}>
              {pending ? "Revirtiendo…" : "Revertir"}
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}

/** After a rejection or a return: where the new debt is. */
export function NewPendingLink({ pendingId }: { pendingId: number }) {
  return (
    <Link href={pendingHref(pendingId)} className="text-primary hover:underline" data-testid="new-pending-link">
      Ver nuevo pendiente
    </Link>
  );
}
