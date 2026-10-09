"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useId, useState, type FormEvent, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api/client";
import { formatDate, formatMoney, todayIso } from "@/lib/format";
import { parseAmount } from "@/modules/settlements/cash/amount";
import { cashApi, cashQueryKey, type Account } from "@/modules/settlements/cash/api";
import { chequeHref } from "@/modules/settlements/cheques/api";
import { chequeErrorView } from "@/modules/settlements/cheques/errors";
import {
  InstrumentFields,
  draftBody,
  draftProblem,
  emptyDraft,
  type InstrumentDraft,
} from "@/modules/settlements/cheques/instrument-fields";
import { deliveryApi, deliveryQueryKey, type Person } from "@/modules/settlements/delivery/api";
import {
  ActionDialog,
  AmountField,
  FormError,
  PendingAttempt,
  SelectField,
  TextField,
  selectClass,
} from "@/modules/settlements/delivery/form";
import { fromCents, toCents } from "@/modules/settlements/delivery/money";

import {
  pendingApi,
  pendingDetailKey,
  pendingPath,
  type DeclaredEcheq,
  type PendingDetail,
  type Receipt,
  type ReceiptMethod,
  type RecordedMethod,
  type Resolution,
} from "./api";
import { pendingErrorView } from "./errors";
import { METHOD_LABELS } from "./labels";
import { usePendingAction } from "./use-pending-action";

const WHERE = "el pendiente";
const errorView = (error: unknown) => pendingErrorView(error);
const reversalErrorView = (error: unknown) => pendingErrorView(error, true);

const DESTINATION_KIND: Record<ReceiptMethod, Account["kind"]> = { CASH: "CASH_BOX", TRANSFER: "BANK" };

function DateField({
  label,
  value,
  onChange,
  min,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  min: string;
}) {
  const id = useId();
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} type="date" min={min} max={todayIso()} value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

function dateProblem(value: string, min: string) {
  if (!value) return "Indicá la fecha de negocio.";
  if (value > todayIso()) return "La fecha de negocio no puede ser posterior a hoy.";
  if (value < min) return `La fecha no puede ser anterior al origen del pendiente (${formatDate(min)}).`;
  return null;
}

/**
 * Record a receipt (operator and admin), from the pending's detail or as a quick action from the list.
 *
 * The pending is always read again when the dialog opens, so the balance shown is the current one, and
 * nothing is credited without an explicit confirmation step that repeats customer, balance, amount,
 * method, destination and business date. The backend still guards concurrency
 * (`pending_amount_exceeds_balance`): on that answer the pending is read again.
 */
export function ReceiptDialog({ pendingId, onClose }: { pendingId: number; onClose: () => void }) {
  const fresh = useQuery({
    queryKey: pendingDetailKey(pendingId),
    queryFn: () => pendingApi.get(pendingId),
    refetchOnMount: "always",
    staleTime: 0,
  });
  const accounts = useQuery({ queryKey: [...cashQueryKey, "accounts"], queryFn: cashApi.accounts });
  return (
    <Dialog title="Registrar cobro" description="El cliente pagó todo o parte de lo que debía." onClose={onClose}>
      {fresh.isPending || accounts.isPending ? (
        <div className="space-y-2" aria-label="Cargando el pendiente">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : fresh.isError || accounts.isError ? (
        <FormError message="No se pudo leer el pendiente. Cerrá y volvé a intentar." />
      ) : (
        <ReceiptForm pending={fresh.data} accounts={accounts.data} onClose={onClose} />
      )}
    </Dialog>
  );
}

function ReceiptForm({
  pending,
  accounts,
  onClose,
}: {
  pending: PendingDetail;
  accounts: Account[];
  onClose: () => void;
}) {
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState<RecordedMethod>("CASH");
  const [destination, setDestination] = useState<number | null>(null);
  const [businessDate, setBusinessDate] = useState(todayIso);
  const [problem, setProblem] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const action = usePendingAction(`receipt:${pending.id}`, onClose);
  const pendingRequest = action.mutation.isPending;
  const error = action.mutation.error ? pendingErrorView(action.mutation.error) : null;
  const destinations =
    method === "CHEQUE" ? [] : accounts.filter((a) => a.active && a.kind === DESTINATION_KIND[method]);
  const chosen = destinations.find((a) => a.id === destination) ?? null;
  const parsed = parseAmount(amount);
  const balance = pending.balance ?? "0.00";
  const owes = pending.state === "OPEN" || pending.state === "PARTIAL";

  if (action.stored) return <PendingAttempt action={action} onClose={onClose} errorView={errorView} where={WHERE} />;

  const methodChoice = (
    <div className="space-y-1.5" role="radiogroup" aria-label="Medio">
      <p className="text-sm font-medium">Medio</p>
      <div className="flex flex-wrap gap-4">
        {(["CASH", "TRANSFER", "CHEQUE"] as RecordedMethod[]).map((value) => (
          <label key={value} className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              name="receipt-method"
              checked={method === value}
              onChange={() => {
                setMethod(value);
                setDestination(null);
              }}
            />
            {METHOD_LABELS[value]}
          </label>
        ))}
      </div>
    </div>
  );

  // Once chosen, the cheque form stays until closed: it shows its own result after the pending is collected.
  if (method === "CHEQUE") {
    return <ChequeReceiptForm pending={pending} methodChoice={methodChoice} onClose={onClose} />;
  }

  const review = (event: FormEvent) => {
    event.preventDefault();
    const local = !parsed
      ? "Ingresá el importe."
      : toCents(parsed) > toCents(balance)
        ? `El importe supera el saldo pendiente (${formatMoney(balance)}).`
        : chosen === null
          ? "Elegí la cuenta de destino."
          : dateProblem(businessDate, pending.origin_business_date);
    setProblem(local);
    if (!local) {
      action.mutation.reset();
      setConfirming(true);
    }
  };

  const confirm = () => {
    action.submit((requestId) => ({
      method: "POST",
      path: pendingPath(pending.id, "/receipts"),
      body: {
        client_request_id: requestId,
        amount: parsed,
        method,
        destination_account_id: destination,
        business_date: businessDate,
      },
      summary: `Cobro de ${formatMoney(parsed!)} de ${pending.customer_name} (${METHOD_LABELS[method].toLowerCase()} a ${chosen?.name ?? ""})`,
    }));
  };

  const header = (
    <dl className="grid grid-cols-2 gap-2 rounded-md border border-border bg-muted px-3 py-2 text-sm">
      <div>
        <dt className="text-xs text-muted-foreground">Cliente</dt>
        <dd className="font-medium">{pending.customer_name}</dd>
      </div>
      <div>
        <dt className="text-xs text-muted-foreground">Saldo pendiente</dt>
        <dd className="font-medium tabular-nums" data-testid="receipt-balance">
          {formatMoney(balance)}
        </dd>
      </div>
    </dl>
  );

  if (!owes) {
    return (
      <div className="space-y-4">
        {header}
        <FormError message="Este pendiente no tiene saldo para cobrar." />
        <div className="flex justify-end">
          <Button variant="ghost" onClick={onClose}>
            Cerrar
          </Button>
        </div>
      </div>
    );
  }

  // The balance changed under us (another receipt, a resolution): the answer already re-read the pending,
  // so the form comes back with the current balance and the message.
  const code = action.mutation.error && (action.mutation.error as { code?: string }).code;
  const stale = code === "pending_amount_exceeds_balance" || code === "pending_not_in_effect";

  if (confirming && !stale) {
    return (
      <div className="space-y-4">
        <div className="space-y-2" role="group" aria-label="Revisá el cobro">
          <p className="text-sm font-medium">Revisá el cobro antes de confirmarlo</p>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-md border border-border px-3 py-2 text-sm">
            <dt className="text-muted-foreground">Cliente</dt>
            <dd>{pending.customer_name}</dd>
            <dt className="text-muted-foreground">Saldo pendiente actual</dt>
            <dd className="tabular-nums">{formatMoney(balance)}</dd>
            <dt className="text-muted-foreground">Importe a acreditar</dt>
            <dd className="font-semibold tabular-nums" data-testid="receipt-amount">
              {formatMoney(parsed!)}
            </dd>
            <dt className="text-muted-foreground">Medio</dt>
            <dd>{METHOD_LABELS[method]}</dd>
            <dt className="text-muted-foreground">Cuenta destino</dt>
            <dd>{chosen?.name}</dd>
            <dt className="text-muted-foreground">Fecha de negocio</dt>
            <dd className="tabular-nums">{formatDate(businessDate)}</dd>
            <dt className="text-muted-foreground">Saldo después del cobro</dt>
            <dd className="tabular-nums">{formatMoney(fromCents(toCents(balance) - toCents(parsed!)))}</dd>
          </dl>
        </div>
        <FormError message={error?.message} />
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" disabled={pendingRequest} onClick={() => setConfirming(false)}>
            Volver
          </Button>
          <Button disabled={pendingRequest} onClick={confirm}>
            {pendingRequest ? "Registrando…" : "Confirmar cobro"}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <form className="space-y-4" onSubmit={review} noValidate>
      {header}
      <fieldset className="space-y-4">
        <div className="space-y-1.5">
          <AmountField value={amount} onChange={setAmount} />
          <Button type="button" variant="ghost" size="sm" onClick={() => setAmount(balance.replace(".", ","))}>
            Cobrar saldo completo
          </Button>
        </div>
        {methodChoice}
        <SelectField
          label={method === "CASH" ? "Caja de destino" : "Banco de destino"}
          value={destination}
          onChange={setDestination}
          placeholder="Elegí…"
          options={destinations}
        />
        <DateField
          label="Fecha de negocio"
          value={businessDate}
          onChange={setBusinessDate}
          min={pending.origin_business_date}
        />
      </fieldset>
      <FormError message={problem ?? error?.message} />
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancelar
        </Button>
        <Button type="submit">Revisar cobro</Button>
      </div>
    </form>
  );
}

interface Part {
  key: number;
  target: "COMPANY" | "EMPLOYEE";
  employeeId: number | null;
  amount: string;
}

/**
 * Administrative resolution of (part of) the balance: the pending will not be collected normally and the
 * admin decides who absorbs it, the company (Incobrables) and/or one or more employees. The parts must add
 * up exactly before it can be confirmed.
 */
export function ResolveDialog({ pending, onClose }: { pending: PendingDetail; onClose: () => void }) {
  const balance = pending.balance ?? "0.00";
  const employees = useQuery({ queryKey: [...deliveryQueryKey, "employees"], queryFn: deliveryApi.employees });
  const [amount, setAmount] = useState(balance.replace(".", ","));
  const [reason, setReason] = useState("");
  const [businessDate, setBusinessDate] = useState(todayIso);
  const [parts, setParts] = useState<Part[]>([{ key: 1, target: "COMPANY", employeeId: null, amount: "" }]);
  const action = usePendingAction(`resolution:${pending.id}`, onClose);
  const parsed = parseAmount(amount);
  const toResolve = parsed ? toCents(parsed) : 0;
  const assigned = parts.reduce(
    (sum, part) => sum + (parseAmount(part.amount) ? toCents(parseAmount(part.amount)!) : 0),
    0,
  );
  const missing = toResolve - assigned;
  const exact = toResolve > 0 && missing === 0 && parts.every((p) => parseAmount(p.amount));
  const update = (key: number, change: Partial<Part>) =>
    setParts(parts.map((part) => (part.key === key ? { ...part, ...change } : part)));
  const names = new Map((employees.data ?? []).map((e: Person) => [e.id, e.full_name]));

  return (
    <ActionDialog
      title="Resolver saldo"
      description="Para cuando el pendiente no se va a cobrar normalmente y hay que decidir quién absorbe el saldo: la empresa (Incobrables) o uno o varios empleados. Un cobro se registra con «Registrar cobro», no acá."
      submitLabel="Confirmar resolución"
      action={action}
      onClose={onClose}
      errorView={errorView}
      where={WHERE}
      submitDisabled={!exact || !reason.trim()}
      validate={() =>
        !parsed
          ? "Ingresá el importe a resolver."
          : toCents(parsed) > toCents(balance)
            ? `El importe supera el saldo pendiente (${formatMoney(balance)}).`
            : !reason.trim()
              ? "Indicá el motivo."
              : parts.some((p) => p.target === "EMPLOYEE" && p.employeeId === null)
                ? "Elegí el empleado de cada parte."
                : !exact
                  ? "Las partes tienen que sumar exactamente el importe a resolver."
                  : dateProblem(businessDate, pending.origin_business_date)
      }
      build={(requestId) => ({
        method: "POST",
        path: pendingPath(pending.id, "/resolutions"),
        body: {
          client_request_id: requestId,
          amount: parsed,
          reason: reason.trim(),
          business_date: businessDate,
          allocations: parts.map((part) => ({
            target: part.target,
            employee_id: part.target === "EMPLOYEE" ? part.employeeId : null,
            amount: parseAmount(part.amount),
          })),
        },
        summary: `Resolución de ${formatMoney(parsed!)} del pendiente de ${pending.customer_name}: ${parts
          .map(
            (p) =>
              `${p.target === "COMPANY" ? "empresa" : (names.get(p.employeeId ?? 0) ?? "empleado")} ${formatMoney(parseAmount(p.amount) ?? "0")}`,
          )
          .join(", ")}`,
      })}
    >
      <p className="text-sm">
        {pending.customer_name} · Saldo pendiente <strong className="tabular-nums">{formatMoney(balance)}</strong>
      </p>
      <AmountField label="Importe a resolver" value={amount} onChange={setAmount} />
      <div className="space-y-2">
        <p className="text-sm font-medium">¿Quién absorbe el saldo?</p>
        {parts.map((part, index) => (
          <div
            key={part.key}
            className="grid gap-2 rounded-md border border-border p-2 sm:grid-cols-[1fr_1fr_auto]"
            data-testid={`part-${index}`}
          >
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground" htmlFor={`part-target-${part.key}`}>
                Parte {index + 1}
              </label>
              <select
                id={`part-target-${part.key}`}
                aria-label={`A cargo de, parte ${index + 1}`}
                className={selectClass}
                value={part.target === "COMPANY" ? "COMPANY" : String(part.employeeId ?? "")}
                onChange={(event) =>
                  event.target.value === "COMPANY"
                    ? update(part.key, { target: "COMPANY", employeeId: null })
                    : update(part.key, {
                        target: "EMPLOYEE",
                        employeeId: event.target.value ? Number(event.target.value) : null,
                      })
                }
              >
                <option value="COMPANY">Empresa (Incobrables)</option>
                <option value="">Un empleado…</option>
                {(employees.data ?? []).map((employee) => (
                  <option key={employee.id} value={employee.id}>
                    {employee.full_name}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground" htmlFor={`part-amount-${part.key}`}>
                Importe
              </label>
              <Input
                id={`part-amount-${part.key}`}
                aria-label={`Importe, parte ${index + 1}`}
                inputMode="decimal"
                className="tabular-nums"
                placeholder="0,00"
                value={part.amount}
                onChange={(event) => update(part.key, { amount: event.target.value })}
              />
            </div>
            <div className="flex items-end">
              {parts.length > 1 && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  aria-label={`Quitar parte ${index + 1}`}
                  onClick={() => setParts(parts.filter((p) => p.key !== part.key))}
                >
                  <Trash2 aria-hidden />
                </Button>
              )}
            </div>
          </div>
        ))}
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={() =>
              setParts([
                ...parts,
                { key: Math.max(...parts.map((p) => p.key)) + 1, target: "EMPLOYEE", employeeId: null, amount: "" },
              ])
            }
          >
            <Plus aria-hidden />
            Agregar parte
          </Button>
          {missing > 0 && parts.length === 1 && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => update(parts[0].key, { amount: fromCents(toResolve).replace(".", ",") })}
            >
              Asignar todo a esta parte
            </Button>
          )}
        </div>
      </div>
      <dl className="grid grid-cols-3 gap-2 rounded-md bg-muted px-3 py-2 text-sm tabular-nums" aria-live="polite">
        <div>
          <dt className="text-xs text-muted-foreground">A resolver</dt>
          <dd>{formatMoney(fromCents(toResolve))}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Asignado</dt>
          <dd data-testid="resolution-assigned">{formatMoney(fromCents(assigned))}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Falta asignar</dt>
          <dd
            data-testid="resolution-missing"
            className={missing !== 0 ? "font-semibold text-warning" : "text-success"}
          >
            {formatMoney(fromCents(missing))}
          </dd>
        </div>
      </dl>
      <TextField label="Motivo" value={reason} onChange={setReason} multiline />
      <DateField
        label="Fecha de negocio"
        value={businessDate}
        onChange={setBusinessDate}
        min={pending.origin_business_date}
      />
    </ActionDialog>
  );
}

/** Reverse a receipt or a resolution (admin, with a reason), in its original business date. */
export function ReverseDialog({
  pending,
  target,
  onClose,
}: {
  pending: PendingDetail;
  target: { kind: "receipt"; item: Receipt } | { kind: "resolution"; item: Resolution };
  onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  const isReceipt = target.kind === "receipt";
  const what = isReceipt
    ? `el cobro de ${formatMoney(target.item.amount)} (${METHOD_LABELS[target.item.method].toLowerCase()} a ${target.item.destination_name})`
    : `la resolución de ${formatMoney(target.item.amount)}`;
  const action = usePendingAction(`${target.kind}-reversal:${pending.id}:${target.item.id}`, onClose);
  return (
    <ActionDialog
      title={isReceipt ? "Revertir cobro" : "Revertir resolución"}
      description={
        <span className="flex gap-2">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
          {isReceipt && target.item.cheque_id !== null ? (
            <span>
              La corrección se registrará en la fecha financiera original ({formatDate(target.item.business_date)}). El
              movimiento no se eliminará. El cheque sale de Cartera y el saldo del pendiente vuelve a quedar abierto.
            </span>
          ) : (
            <span>
              Se corregirá la operación financiera en su fecha original ({formatDate(target.item.business_date)}). El
              movimiento original no se borra: queda registrado junto con su reverso, y el saldo del pendiente vuelve a
              quedar abierto.
            </span>
          )}
        </span>
      }
      submitLabel="Revertir"
      action={action}
      onClose={onClose}
      errorView={isReceipt && target.item.cheque_id !== null ? chequeErrorView : reversalErrorView}
      where={WHERE}
      validate={() => (!reason.trim() ? "Indicá el motivo." : null)}
      build={(requestId) => ({
        method: "POST",
        path: pendingPath(pending.id, `/${isReceipt ? "receipts" : "resolutions"}/${target.item.id}/reverse`),
        body: { client_request_id: requestId, reason: reason.trim() },
        summary: `Reverso de ${what}`,
      })}
    >
      <p className="text-sm">Vas a revertir {what}.</p>
      <TextField label="Motivo" value={reason} onChange={setReason} multiline />
    </ActionDialog>
  );
}

/** Change who answers for collecting the pending, also after the close (admin). The settlement is not reopened. */
export function ResponsibleDialog({ pending, onClose }: { pending: PendingDetail; onClose: () => void }) {
  const employees = useQuery({ queryKey: [...deliveryQueryKey, "employees"], queryFn: deliveryApi.employees });
  const [responsible, setResponsible] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const action = usePendingAction(`responsible:${pending.id}`, onClose);
  const options = (employees.data ?? [])
    .filter((employee) => employee.id !== pending.responsible_employee_id)
    .map((employee) => ({ id: employee.id, name: employee.full_name }));
  return (
    <ActionDialog
      title="Cambiar responsable"
      description="El cambio queda en la historia del pendiente. La rendición no se reabre y Cajas no cambia."
      submitLabel="Cambiar responsable"
      action={action}
      onClose={onClose}
      errorView={errorView}
      where={WHERE}
      validate={() => (responsible === null ? "Elegí el nuevo responsable." : null)}
      build={() => ({
        method: "PATCH",
        path: pendingPath(pending.id, "/responsible"),
        body: { responsible_employee_id: responsible, reason: reason.trim() || null },
        summary: `Nuevo responsable del pendiente de ${pending.customer_name}`,
      })}
    >
      <p className="text-sm">
        Responsable actual: <strong>{pending.responsible_name}</strong>
      </p>
      <SelectField
        label="Nuevo responsable"
        value={responsible}
        onChange={setResponsible}
        placeholder="Elegí un empleado…"
        options={options}
      />
      <TextField label="Motivo (opcional)" value={reason} onChange={setReason} multiline />
    </ActionDialog>
  );
}

const CHEQUE_OVER_BALANCE =
  "El importe del cheque supera el saldo pendiente. DistriCom todavía no gestiona saldos a favor del cliente.";

/**
 * Collect the pending with a cheque (R4-2). A physical cheque collects at once (Pendiente ↓, Cartera ↑); an
 * ECHEQ is only declared: the balance stays until it is verified, and the verification reads the balance
 * again. Never more than the balance: there are no credit balances.
 */
function ChequeReceiptForm({
  pending,
  methodChoice,
  onClose,
}: {
  pending: PendingDetail;
  methodChoice: ReactNode;
  onClose: () => void;
}) {
  const balance = pending.balance ?? "0.00";
  const [draft, setDraft] = useState<InstrumentDraft>(() =>
    emptyDraft(pending.customer_in_effect ?? pending.customer_name),
  );
  const [businessDate, setBusinessDate] = useState(todayIso);
  const [problem, setProblem] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [created, setCreated] = useState<{ chequeId: number | null; echeq: boolean } | null>(null);
  // The cheques this pending had when the form opened: the new one is the one not in this set.
  const [before] = useState(
    () =>
      new Set([
        ...pending.receipts.map((r) => r.cheque_id).filter((id): id is number => id !== null),
        ...pending.echeqs.map((e) => e.cheque_id),
      ]),
  );
  const action = usePendingAction(`cheque-receipt:${pending.id}`, (detail) => {
    const ids = [
      ...detail.receipts.map((r) => r.cheque_id).filter((id): id is number => id !== null),
      ...detail.echeqs.map((e) => e.cheque_id),
    ];
    setCreated({ chequeId: ids.find((id) => !before.has(id)) ?? null, echeq: draft.kind === "ECHEQ" });
  });
  const pendingRequest = action.mutation.isPending;
  const error = action.mutation.error;
  const view = error ? chequeErrorView(error) : null;
  const parsed = parseAmount(draft.amount);
  const echeq = draft.kind === "ECHEQ";

  if (created) {
    return (
      <div className="space-y-4" role="status">
        <p className="text-sm font-medium">
          {created.echeq
            ? "ECHEQ declarado. Este ECHEQ todavía no fue verificado. El saldo pendiente continúa vigente."
            : "Cobro registrado con cheque: el pendiente bajó y el cheque entró a Cartera."}
        </p>
        <div className="flex flex-wrap justify-end gap-2">
          {created.chequeId !== null && (
            <Link href={chequeHref(created.chequeId)} className="self-center text-sm text-primary hover:underline">
              Ver cheque
            </Link>
          )}
          <Button onClick={onClose}>Cerrar</Button>
        </div>
      </div>
    );
  }
  if (action.stored) return <PendingAttempt action={action} onClose={onClose} errorView={errorView} where={WHERE} />;

  const review = (event: FormEvent) => {
    event.preventDefault();
    const local =
      parsed && toCents(parsed) > toCents(balance)
        ? CHEQUE_OVER_BALANCE
        : (draftProblem(draft) ?? dateProblem(businessDate, pending.origin_business_date));
    setProblem(local);
    if (!local) {
      action.mutation.reset();
      setConfirming(true);
    }
  };

  const confirm = () =>
    action.submit((requestId) => ({
      method: "POST",
      path: pendingPath(pending.id, "/cheque-receipts"),
      body: { client_request_id: requestId, business_date: businessDate, ...draftBody(draft) },
      summary: `${echeq ? "ECHEQ declarado" : "Cobro con cheque"} n.º ${draft.number.trim()} de ${formatMoney(parsed!)} para ${pending.customer_name}`,
    }));

  const duplicate = error instanceof ApiError && error.code === "cheque_possible_duplicate";
  if (confirming && !duplicate) {
    return (
      <div className="space-y-4">
        <div className="space-y-2" role="group" aria-label="Revisá el cobro">
          <p className="text-sm font-medium">Revisá el cobro antes de confirmarlo</p>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-md border border-border px-3 py-2 text-sm">
            <dt className="text-muted-foreground">Cliente</dt>
            <dd>{pending.customer_name}</dd>
            <dt className="text-muted-foreground">Saldo pendiente actual</dt>
            <dd className="tabular-nums">{formatMoney(balance)}</dd>
            <dt className="text-muted-foreground">Cheque</dt>
            <dd>
              {echeq ? "ECHEQ" : "Físico"} n.º {draft.number.trim()}
            </dd>
            <dt className="text-muted-foreground">Importe</dt>
            <dd className="font-semibold tabular-nums">{formatMoney(parsed!)}</dd>
            <dt className="text-muted-foreground">Fecha de negocio</dt>
            <dd className="tabular-nums">{formatDate(businessDate)}</dd>
            {echeq ? (
              <>
                <dt className="text-muted-foreground">Efecto</dt>
                <dd>Queda declarado. El saldo no baja hasta verificarlo.</dd>
              </>
            ) : (
              <>
                <dt className="text-muted-foreground">Pendiente</dt>
                <dd className="tabular-nums">−{formatMoney(parsed!)}</dd>
                <dt className="text-muted-foreground">Cartera de cheques</dt>
                <dd className="tabular-nums">+{formatMoney(parsed!)}</dd>
              </>
            )}
          </dl>
        </div>
        <FormError message={view?.message} />
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" disabled={pendingRequest} onClick={() => setConfirming(false)}>
            Volver
          </Button>
          <Button disabled={pendingRequest} onClick={confirm}>
            {pendingRequest ? "Registrando…" : echeq ? "Declarar ECHEQ" : "Confirmar cobro"}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <form className="space-y-4" onSubmit={review} noValidate>
      <p className="text-sm">
        {pending.customer_name} · Saldo pendiente{" "}
        <strong className="tabular-nums" data-testid="receipt-balance">
          {formatMoney(balance)}
        </strong>
      </p>
      {methodChoice}
      <InstrumentFields draft={draft} onChange={setDraft} possibleDuplicate={duplicate} />
      {parsed && toCents(parsed) > toCents(balance) && <FormError message={CHEQUE_OVER_BALANCE} />}
      <DateField
        label="Fecha de negocio"
        value={businessDate}
        onChange={setBusinessDate}
        min={pending.origin_business_date}
      />
      <FormError message={problem ?? view?.message} />
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancelar
        </Button>
        <Button type="submit">Revisar cobro</Button>
      </div>
    </form>
  );
}

/**
 * Verify an ECHEQ received for this pending, or reject its verification. Verifying reads the balance again:
 * if it no longer covers the ECHEQ (another receipt meanwhile), the backend refuses and nothing is collected.
 */
export function EcheqDecisionDialog({
  pending,
  echeq,
  to,
  onClose,
}: {
  pending: PendingDetail;
  echeq: DeclaredEcheq;
  to: "VERIFIED" | "VERIFICATION_REJECTED";
  onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  const [businessDate, setBusinessDate] = useState(todayIso);
  const action = usePendingAction(`echeq:${pending.id}:${echeq.cheque_id}`, onClose);
  const verify = to === "VERIFIED";
  return (
    <ActionDialog
      title={verify ? "Verificar ECHEQ" : "Rechazar verificación"}
      description={
        verify
          ? "Confirmá que el ECHEQ está a nombre de la empresa. Al verificarlo se registra el cobro: el pendiente baja y el ECHEQ entra a Cartera."
          : "El ECHEQ no se pudo validar: no se registra ningún cobro y el saldo sigue pendiente."
      }
      submitLabel={verify ? "Verificar y cobrar" : "Rechazar verificación"}
      action={action}
      onClose={onClose}
      errorView={(error) => chequeErrorView(error)}
      where={WHERE}
      validate={() =>
        !verify && !reason.trim() ? "Indicá el motivo." : dateProblem(businessDate, pending.origin_business_date)
      }
      build={() => ({
        method: "POST",
        path: pendingPath(pending.id, `/cheque-receipts/${echeq.cheque_id}/verification`),
        body: { status: to, reason: reason.trim() || null, business_date: businessDate },
        summary: `${verify ? "Verificación" : "Rechazo de verificación"} del ECHEQ n.º ${echeq.cheque_number}`,
      })}
    >
      <p className="text-sm">
        ECHEQ n.º {echeq.cheque_number} · <strong className="tabular-nums">{formatMoney(echeq.amount)}</strong> · Saldo
        pendiente <strong className="tabular-nums">{formatMoney(pending.balance ?? "0.00")}</strong>
      </p>
      <TextField label={verify ? "Motivo (opcional)" : "Motivo"} value={reason} onChange={setReason} multiline />
      <DateField
        label="Fecha de negocio"
        value={businessDate}
        onChange={setBusinessDate}
        min={pending.origin_business_date}
      />
    </ActionDialog>
  );
}
