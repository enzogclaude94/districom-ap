"use client";

import { useQuery } from "@tanstack/react-query";
import { Info, Plus, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useId, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api/client";
import { formatDate, formatMoney, todayIso } from "@/lib/format";
import { parseAmount } from "@/modules/settlements/cash/amount";
import { cashApi, cashQueryKey } from "@/modules/settlements/cash/api";
import type { DeliveryErrorView } from "@/modules/settlements/delivery/errors";
import { AmountField, PendingAttempt, SelectField, TextField } from "@/modules/settlements/delivery/form";
import { useWriteAction } from "@/modules/settlements/delivery/use-delivery-action";

import {
  employeeFinanceApi,
  employeeFinanceQueryKey,
  type EmployeeLoan,
  type LoanInstallment,
  type LoanPreview,
  type LoanTerms,
} from "./api";
import { employeeLoanHref, loanTitle } from "./labels";
import { addMonths, editableAmount, sameAmount, sumAmounts } from "./loan-schedule";
import { MovementLegs, Problem, movementErrorView } from "./movement-dialogs";

const MAX_INSTALLMENTS = 120;

/** What the user reads for each error of a loan: the backend writes business errors for the user. */
export function loanErrorView(error: unknown, accountName?: (id: number) => string | undefined): DeliveryErrorView {
  if (error instanceof ApiError && error.code === "idempotency_conflict") {
    return {
      message:
        "Esta acción ya se había registrado con otros datos. Revisá el préstamo; si querés hacer otra distinta, empezá de nuevo.",
      conflict: true,
    };
  }
  return movementErrorView(error, accountName);
}

interface Row {
  due_on: string;
  amount: string; // as typed
}

/** One editable row per installment: number, due date and amount. */
function ScheduleEditor({
  rows,
  minDate,
  onChange,
  testId,
  numberOf,
}: {
  rows: Row[];
  minDate: string;
  onChange: (rows: Row[]) => void;
  testId: string;
  numberOf: (index: number) => number;
}) {
  const set = (index: number, patch: Partial<Row>) =>
    onChange(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  return (
    <ol className="divide-y divide-border rounded-md border border-border" data-testid={testId}>
      {rows.map((row, index) => (
        <li
          key={index}
          className="grid grid-cols-[2.5rem_1fr] items-center gap-x-2 gap-y-2 px-3 py-2 sm:grid-cols-[2.5rem_1fr_1fr]"
        >
          <span className="text-sm font-medium tabular-nums">{numberOf(index)}</span>
          <Input
            type="date"
            aria-label={`Vencimiento de la cuota ${numberOf(index)}`}
            min={minDate}
            className="tabular-nums"
            value={row.due_on}
            onChange={(event) => set(index, { due_on: event.target.value })}
          />
          <Input
            inputMode="decimal"
            autoComplete="off"
            aria-label={`Importe de la cuota ${numberOf(index)}`}
            className="col-start-2 tabular-nums sm:col-start-3"
            value={row.amount}
            onChange={(event) => set(index, { amount: event.target.value })}
          />
        </li>
      ))}
    </ol>
  );
}

/** «Total de cuotas» against what it must add up to, so the admin sees an edit that no longer fits. */
function Totals({ label, expected, rows, testId }: { label: string; expected: string; rows: Row[]; testId: string }) {
  const total = sumAmounts(rows.map((row) => row.amount));
  const matches = total !== null && sameAmount(total, expected);
  return (
    <dl className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2" data-testid={testId}>
      <div className="rounded-md border border-border px-3 py-2">
        <dt className="text-muted-foreground">{label}</dt>
        <dd className="font-medium tabular-nums">{formatMoney(expected)}</dd>
      </div>
      <div className={`rounded-md border px-3 py-2 ${matches ? "border-border" : "border-danger/40 bg-danger-soft"}`}>
        <dt className="text-muted-foreground">Nuevo cronograma</dt>
        <dd className={`font-medium tabular-nums ${matches ? "" : "text-danger"}`}>
          {total === null ? "Revisá los importes" : formatMoney(total)}
        </dd>
      </div>
    </dl>
  );
}

function checkRows(rows: Row[], expected: string, minDate: string, what: string): string | null {
  if (rows.some((row) => !row.due_on)) return "Indicá el vencimiento de cada cuota.";
  if (rows.some((row) => row.due_on < minDate))
    return `Ninguna cuota puede vencer antes del préstamo (${formatDate(minDate)}).`;
  if (rows.some((row) => parseAmount(row.amount) === null))
    return "Cada cuota necesita un importe mayor a cero, con hasta dos decimales.";
  const total = sumAmounts(rows.map((row) => row.amount));
  if (total === null || !sameAmount(total, expected))
    return `Las cuotas tienen que sumar exactamente ${formatMoney(expected)} (${what}).`;
  return null;
}

/**
 * «Dar préstamo» (R8-3, Enzo's point 12): an interest-free loan from a cash box or the bank, paid back in
 * installments. Step 1: the terms. Step 2: the backend's preview (the legs, the default schedule and its total),
 * where the admin may still change each installment's date and amount before confirming; once confirmed the schedule
 * only changes by deferring an installment. The request id is kept for every retry (ADR 008).
 */
export function LoanDialog({
  employeeId,
  employeeName,
  onClose,
}: {
  employeeId: number;
  employeeName: string;
  onClose: () => void;
}) {
  const router = useRouter();
  const dateId = useId();
  const countId = useId();
  const firstId = useId();
  const accounts = useQuery({ queryKey: [...cashQueryKey, "accounts"], queryFn: cashApi.accounts });
  const funding = (accounts.data ?? []).filter(
    (account) => account.active && (account.kind === "CASH_BOX" || account.kind === "BANK"),
  );
  const [businessDate, setBusinessDate] = useState(todayIso);
  const [principal, setPrincipal] = useState("");
  const [fundingId, setFundingId] = useState<number | null>(null);
  const [count, setCount] = useState("3");
  // A month after the loan by default; editable down to the loan's own date (Enzo, R8-2 approval).
  const [firstDue, setFirstDue] = useState<string | null>(null);
  const [description, setDescription] = useState("");
  const [reference, setReference] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [terms, setTerms] = useState<LoanTerms | null>(null);
  const [rows, setRows] = useState<Row[] | null>(null);

  const action = useWriteAction<EmployeeLoan>({
    slot: `employee-loan:${employeeId}`,
    write: (request) => employeeFinanceApi.writeLoan(request.path, request.body),
    invalidate: [employeeFinanceQueryKey, cashQueryKey],
    isUncertain: (error) => Boolean(loanErrorView(error).uncertain),
    onDone: (loan) => {
      onClose();
      router.push(employeeLoanHref(loan.id));
    },
  });
  const accountName = (id: number) => (accounts.data ?? []).find((a) => a.id === id)?.name;
  const parsed = parseAmount(principal);
  const installments = Number(count);
  const firstDueOn = firstDue ?? (businessDate ? addMonths(businessDate, 1) : "");
  const preview = useQuery({
    queryKey: [...employeeFinanceQueryKey, "loan-preview", employeeId, terms],
    queryFn: () => employeeFinanceApi.previewLoan(employeeId, terms!),
    enabled: terms !== null,
    retry: false,
    staleTime: 0,
  });
  const schedule: Row[] =
    rows ??
    (preview.data?.installments ?? []).map((item) => ({ due_on: item.due_on, amount: editableAmount(item.amount) }));
  const pending = action.mutation.isPending;
  const error = action.mutation.error ? loanErrorView(action.mutation.error, accountName) : null;
  const previewError = preview.error ? loanErrorView(preview.error, accountName) : null;

  const validate = () =>
    (!businessDate ? "Indicá la fecha del préstamo." : null) ??
    (businessDate > todayIso() ? "La fecha no puede ser futura." : null) ??
    (!parsed ? "Ingresá el importe del préstamo, mayor a cero y con hasta dos decimales." : null) ??
    (fundingId === null ? "Elegí la caja o el banco desde el que se entrega." : null) ??
    (!Number.isInteger(installments) || installments < 1 || installments > MAX_INSTALLMENTS
      ? `La cantidad de cuotas va de 1 a ${MAX_INSTALLMENTS}.`
      : null) ??
    (!firstDueOn ? "Indicá el vencimiento de la primera cuota." : null) ??
    (firstDueOn < businessDate ? "La primera cuota no puede vencer antes del préstamo." : null);

  const review = (event: FormEvent) => {
    event.preventDefault();
    const local = validate();
    setProblem(local);
    if (local) return;
    action.mutation.reset();
    setRows(null);
    setTerms({
      business_date: businessDate,
      principal: parsed!,
      funding_account_id: fundingId!,
      installment_count: installments,
      first_due_on: firstDueOn,
    });
  };

  const scheduleProblem = preview.data
    ? checkRows(schedule, preview.data.principal, businessDate, "el préstamo")
    : null;

  const confirm = () => {
    if (!terms || scheduleProblem) return;
    const confirmed = schedule.map((row) => ({ due_on: row.due_on, amount: parseAmount(row.amount)! }));
    action.submit((requestId) => ({
      method: "POST",
      path: `/employees/${employeeId}/loans`,
      body: {
        client_request_id: requestId,
        ...terms,
        installment_count: confirmed.length,
        first_due_on: confirmed[0].due_on,
        installments: confirmed,
        description: description.trim() || null,
        reference: reference.trim() || null,
      },
      summary: `Préstamo de ${formatMoney(terms.principal)} a ${employeeName}`,
    }));
  };

  const title = "Dar préstamo";
  const intro = `${employeeName}. Dinero entregado desde una caja o el banco, sin interés, que se devuelve en cuotas.`;

  if (action.stored) {
    return (
      <Dialog title={title} description={intro} onClose={onClose}>
        <PendingAttempt
          action={action}
          onClose={onClose}
          errorView={(e) => loanErrorView(e, accountName)}
          where="los préstamos del empleado"
        />
      </Dialog>
    );
  }

  if (terms) {
    const data: LoanPreview | undefined = preview.data;
    const shown = previewError?.message ?? error?.message ?? (data ? scheduleProblem : null);
    return (
      <Dialog
        title="Confirmá el préstamo"
        description={`${employeeName} · ${formatDate(terms.business_date)}${description.trim() ? ` · ${description.trim()}` : ""}`}
        onClose={onClose}
        className="sm:max-w-2xl"
      >
        <div className="space-y-4" data-testid="loan-confirmation">
          {preview.isPending ? (
            <p className="text-sm text-muted-foreground">Calculando el préstamo…</p>
          ) : data ? (
            <>
              <p className="font-medium">
                Se entregan {formatMoney(data.principal)} desde {data.funding_account_name}: {employeeName} deberá{" "}
                {formatMoney(data.principal)} más a la empresa, en {schedule.length}{" "}
                {schedule.length === 1 ? "cuota" : "cuotas"} sin interés.
              </p>
              <MovementLegs legs={data.legs} testId="loan-legs" />
              <div className="space-y-2">
                <h3 className="text-sm font-medium">Cronograma</h3>
                <p className="text-xs text-muted-foreground">
                  Podés cambiar fechas e importes antes de confirmar. Después, el cronograma solo cambia postergando una
                  cuota.
                </p>
                <ScheduleEditor
                  rows={schedule}
                  minDate={terms.business_date}
                  onChange={setRows}
                  testId="loan-schedule"
                  numberOf={(index) => index + 1}
                />
                <Totals label="Préstamo" expected={data.principal} rows={schedule} testId="loan-totals" />
              </div>
            </>
          ) : null}
          {shown && <Problem>{shown}</Problem>}
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" disabled={pending} onClick={() => setTerms(null)}>
              Volver a editar
            </Button>
            {error?.conflict ? (
              <Button variant="secondary" onClick={action.abandonStored}>
                Empezar de nuevo
              </Button>
            ) : (
              <Button disabled={pending || !data || scheduleProblem !== null} onClick={confirm}>
                {pending ? "Registrando…" : "Confirmar y dar préstamo"}
              </Button>
            )}
          </div>
        </div>
      </Dialog>
    );
  }

  return (
    <Dialog title={title} description={intro} onClose={onClose}>
      <form className="space-y-4" onSubmit={review} noValidate>
        <div className="flex gap-2 rounded-md border border-border bg-muted/40 px-3 py-2 text-sm">
          <Info className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
          <p>Sin intereses. El cronograma se revisa en el paso siguiente.</p>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={dateId}>Fecha del préstamo</Label>
          <Input
            id={dateId}
            type="date"
            max={todayIso()}
            className="w-44 tabular-nums"
            value={businessDate}
            onChange={(event) => setBusinessDate(event.target.value)}
          />
        </div>
        <AmountField label="Importe del préstamo" value={principal} onChange={setPrincipal} />
        <SelectField
          label="Se entrega desde"
          value={fundingId}
          onChange={setFundingId}
          placeholder={accounts.isPending ? "Cargando cuentas…" : "Elegí la caja o el banco…"}
          options={funding.map((a) => ({ id: a.id, name: `${a.name} · saldo ${formatMoney(a.balance)}` }))}
        />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor={countId}>Cantidad de cuotas</Label>
            <Input
              id={countId}
              type="number"
              inputMode="numeric"
              min={1}
              max={MAX_INSTALLMENTS}
              className="w-28 tabular-nums"
              value={count}
              onChange={(event) => setCount(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={firstId}>Vence la primera cuota</Label>
            <Input
              id={firstId}
              type="date"
              min={businessDate}
              className="w-44 tabular-nums"
              value={firstDueOn}
              onChange={(event) => setFirstDue(event.target.value)}
            />
            <p className="text-xs text-muted-foreground">Puede vencer en el mismo mes del préstamo.</p>
          </div>
        </div>
        <TextField label="Descripción (opcional)" value={description} onChange={setDescription} multiline />
        <TextField label="Referencia (opcional)" value={reference} onChange={setReference} maxLength={100} />
        {problem && <Problem>{problem}</Problem>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button type="submit">Revisar</Button>
        </div>
      </form>
    </Dialog>
  );
}

/**
 * «Postergar» a scheduled installment (area admin, Enzo's point 13): a reason and one or more new installments
 * adding up to exactly its amount. The installment stays as history, deferred; nothing moves in the ledger.
 */
export function DeferInstallmentDialog({
  loan,
  installment,
  onClose,
}: {
  loan: EmployeeLoan;
  installment: LoanInstallment;
  onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  const [rows, setRows] = useState<Row[]>(() => [
    { due_on: addMonths(installment.due_on, 1), amount: editableAmount(installment.amount) },
  ]);
  const [problem, setProblem] = useState<string | null>(null);
  const action = useWriteAction<EmployeeLoan>({
    slot: `employee-loan-defer:${installment.id}`,
    write: (request) => employeeFinanceApi.writeLoan(request.path, request.body),
    invalidate: [employeeFinanceQueryKey],
    isUncertain: (error) => Boolean(loanErrorView(error).uncertain),
    onDone: onClose,
  });
  const error = action.mutation.error ? loanErrorView(action.mutation.error) : null;
  const pending = action.mutation.isPending;
  const nextNumber = Math.max(...loan.installments.map((i) => i.number)) + 1;
  const title = `Postergar la cuota ${installment.number}`;
  const intro = `${formatMoney(installment.amount)} · vence el ${formatDate(installment.due_on)}. La cuota queda en la historia como postergada y la reemplazan las nuevas.`;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const local =
      (reason.trim() ? null : "Indicá el motivo de la postergación.") ??
      checkRows(rows, installment.amount, loan.business_date, `la cuota ${installment.number}`);
    setProblem(local);
    if (local) return;
    action.submit((requestId) => ({
      method: "POST",
      path: `/loan-installments/${installment.id}/defer`,
      body: {
        client_request_id: requestId,
        reason: reason.trim(),
        replacements: rows.map((row) => ({ due_on: row.due_on, amount: parseAmount(row.amount) })),
      },
      summary: `Postergación de la cuota ${installment.number} del ${loanTitle(loan.id).toLowerCase()}`,
    }));
  };
  if (action.stored) {
    return (
      <Dialog title={title} description={intro} onClose={onClose}>
        <PendingAttempt action={action} onClose={onClose} errorView={(e) => loanErrorView(e)} where="el préstamo" />
      </Dialog>
    );
  }
  return (
    <Dialog title={title} description={intro} onClose={onClose} className="sm:max-w-2xl">
      <form className="space-y-4" onSubmit={submit} noValidate>
        <TextField label="Motivo" value={reason} onChange={setReason} multiline />
        <div className="space-y-2">
          <h3 className="text-sm font-medium">Nuevas cuotas</h3>
          <ScheduleEditor
            rows={rows}
            minDate={loan.business_date}
            onChange={setRows}
            testId="replacement-rows"
            numberOf={(index) => nextNumber + index}
          />
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              variant="secondary"
              onClick={() =>
                setRows([
                  ...rows,
                  { due_on: addMonths(rows[rows.length - 1]?.due_on ?? installment.due_on, 1), amount: "" },
                ])
              }
            >
              <Plus className="size-4" aria-hidden />
              Agregar cuota
            </Button>
            {rows.length > 1 && (
              <Button type="button" size="sm" variant="ghost" onClick={() => setRows(rows.slice(0, -1))}>
                <Trash2 className="size-4" aria-hidden />
                Quitar la última
              </Button>
            )}
          </div>
          <Totals label="Importe reemplazado" expected={installment.amount} rows={rows} testId="deferral-totals" />
        </div>
        {(problem ?? error) && <Problem>{problem ?? error!.message}</Problem>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          {error?.conflict ? (
            <Button type="button" variant="secondary" onClick={action.abandonStored}>
              Empezar de nuevo
            </Button>
          ) : (
            <Button type="submit" disabled={pending}>
              {pending ? "Postergando…" : "Confirmar postergación"}
            </Button>
          )}
        </div>
      </form>
    </Dialog>
  );
}

/** Reverses the whole loan on its original date (area admin + operator in Rendiciones), once, with a reason. */
export function ReverseLoanDialog({ loan, onClose }: { loan: EmployeeLoan; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const action = useWriteAction<EmployeeLoan>({
    slot: `employee-loan-reverse:${loan.id}`,
    write: (request) => employeeFinanceApi.writeLoan(request.path, request.body),
    invalidate: [employeeFinanceQueryKey, cashQueryKey],
    isUncertain: (error) => Boolean(loanErrorView(error).uncertain),
    onDone: onClose,
  });
  const error = action.mutation.error ? loanErrorView(action.mutation.error) : null;
  const pending = action.mutation.isPending;
  const title = `Revertir el ${loanTitle(loan.id).toLowerCase()}`;
  const intro = `Se registra el reverso exacto con fecha ${formatDate(loan.business_date)}: el dinero vuelve a ${loan.funding_account_name}. Las cuotas programadas se cancelan; las postergadas quedan como historia.`;
  const reversed = loan.legs.map((leg) => ({
    ...leg,
    amount: leg.amount.startsWith("-") ? leg.amount.slice(1) : `-${leg.amount}`,
  }));
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const local = reason.trim() ? null : "Indicá el motivo del reverso.";
    setProblem(local);
    if (local) return;
    action.submit((requestId) => ({
      method: "POST",
      path: `/loans/${loan.id}/reverse`,
      body: { client_request_id: requestId, reason: reason.trim() },
      summary: `Reverso del ${loanTitle(loan.id).toLowerCase()}`,
    }));
  };
  if (action.stored) {
    return (
      <Dialog title={title} description={intro} onClose={onClose}>
        <PendingAttempt action={action} onClose={onClose} errorView={(e) => loanErrorView(e)} where="el préstamo" />
      </Dialog>
    );
  }
  return (
    <Dialog title={title} description={intro} onClose={onClose}>
      <form className="space-y-4" onSubmit={submit} noValidate>
        <MovementLegs legs={reversed} testId="loan-reversal-legs" />
        <TextField label="Motivo del reverso" value={reason} onChange={setReason} multiline />
        {(problem ?? error) && <Problem>{problem ?? error!.message}</Problem>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button type="submit" variant="danger" disabled={pending}>
            {pending ? "Revirtiendo…" : "Confirmar reverso"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
