"use client";

import { useQuery } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
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
import { PendingAttempt, TextField, selectClass } from "@/modules/settlements/delivery/form";
import { useWriteAction } from "@/modules/settlements/delivery/use-delivery-action";

import {
  employeeFinanceApi,
  employeeFinanceQueryKey,
  type Payout,
  type PayoutMethod,
  type PayoutTerms,
  type Payroll,
} from "./api";
import { PAYOUT_METHOD_LABELS, payoutTitle } from "./labels";
import { editableAmount, moreThan, sumAmounts } from "./loan-schedule";
import { MovementLegs, Problem, movementErrorView } from "./movement-dialogs";
import { periodLabel } from "./payroll-list";

const METHOD_KINDS: Record<PayoutMethod, "CASH_BOX" | "BANK"> = { CASH: "CASH_BOX", TRANSFER: "BANK" };

/** What the user reads for each error of a payout: the backend writes business errors for the user. */
export function payoutErrorView(error: unknown, accountName?: (id: number) => string | undefined): DeliveryErrorView {
  if (error instanceof ApiError && error.code === "idempotency_conflict") {
    return {
      message:
        "Este pago ya se había registrado con otros datos. Revisá la liquidación; si querés registrar otro, empezá de nuevo.",
      conflict: true,
    };
  }
  return movementErrorView(error, accountName);
}

interface Means {
  method: PayoutMethod;
  accountId: number | null;
  amount: string; // as typed
}

/** «Pago» of a CLOSED payroll (R8-5): business date from the period's end up to today, one or more means (cash from
 * a cash box, transfer from a bank), an amount each. The default is the whole pending, editable downwards, never above
 * it. Step 2 is the backend's preview: «Liquidación», «Ya pagado», «Este pago», «Quedará pendiente» and the legs. */
export function PayoutDialog({ payroll, onClose }: { payroll: Payroll; onClose: () => void }) {
  const payment = payroll.payment!;
  const dateId = useId();
  const accounts = useQuery({ queryKey: [...cashQueryKey, "accounts"], queryFn: cashApi.accounts });
  const today = todayIso();
  const [businessDate, setBusinessDate] = useState(payroll.period_end > today ? payroll.period_end : today);
  const [means, setMeans] = useState<Means[]>([
    { method: "CASH", accountId: null, amount: editableAmount(payment.pending) },
  ]);
  const [reference, setReference] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [terms, setTerms] = useState<PayoutTerms | null>(null);

  const action = useWriteAction<Payout>({
    slot: `employee-payout:${payroll.id}`,
    write: (request) => employeeFinanceApi.writePayout(request.path, request.body),
    invalidate: [employeeFinanceQueryKey, cashQueryKey],
    isUncertain: (error) => Boolean(payoutErrorView(error).uncertain),
    onDone: onClose,
  });
  const accountName = (id: number) => (accounts.data ?? []).find((a) => a.id === id)?.name;
  const options = (method: PayoutMethod) =>
    (accounts.data ?? []).filter((a) => a.active && a.kind === METHOD_KINDS[method]);
  const total = sumAmounts(means.map((m) => m.amount));
  const preview = useQuery({
    queryKey: [...employeeFinanceQueryKey, "payout-preview", payroll.id, terms],
    queryFn: () => employeeFinanceApi.previewPayout(payroll.id, terms!),
    enabled: terms !== null,
    retry: false,
    staleTime: 0,
  });
  const pending = action.mutation.isPending;
  const error = action.mutation.error ? payoutErrorView(action.mutation.error, accountName) : null;
  const previewError = preview.error ? payoutErrorView(preview.error, accountName) : null;

  const set = (index: number, patch: Partial<Means>) =>
    setMeans(means.map((m, i) => (i === index ? { ...m, ...patch } : m)));

  const validate = () =>
    (!businessDate ? "Indicá la fecha del pago." : null) ??
    (businessDate > today ? "La fecha del pago no puede ser futura." : null) ??
    (businessDate < payroll.period_end
      ? `El pago no puede ser anterior al fin del período (${formatDate(payroll.period_end)}). La plata entregada antes es un adelanto.`
      : null) ??
    (means.some((m) => m.accountId === null) ? "Elegí la caja o el banco de cada medio." : null) ??
    (means.some((m) => parseAmount(m.amount) === null)
      ? "Cada importe tiene que ser mayor a cero y con hasta dos decimales."
      : null) ??
    (new Set(means.map((m) => m.accountId)).size !== means.length
      ? "Cada caja o banco va una sola vez: sumá los importes."
      : null) ??
    (total !== null && moreThan(total, payment.pending)
      ? `El pago (${formatMoney(total)}) supera lo pendiente (${formatMoney(payment.pending)}).`
      : null);

  const review = (event: FormEvent) => {
    event.preventDefault();
    const local = validate();
    setProblem(local);
    if (local) return;
    action.mutation.reset();
    setTerms({
      payroll_closing_id: payroll.closing!.id,
      business_date: businessDate,
      items: means.map((m) => ({ method: m.method, account_id: m.accountId!, amount: parseAmount(m.amount)! })),
      reference: reference.trim() || null,
    });
  };

  const confirm = () => {
    if (!terms) return;
    action.submit((requestId) => ({
      method: "POST",
      path: `/payrolls/${payroll.id}/payouts`,
      body: { client_request_id: requestId, ...terms },
      summary: `Pago de ${formatMoney(total ?? "0")} de la liquidación de ${payroll.employee_name}`,
    }));
  };

  const title = "Registrar pago";
  const intro = `${payroll.employee_name} · ${periodLabel(payroll)}. Pendiente de pago: ${formatMoney(payment.pending)}.`;

  if (action.stored) {
    return (
      <Dialog title={title} description={intro} onClose={onClose}>
        <PendingAttempt
          action={action}
          onClose={onClose}
          errorView={(e) => payoutErrorView(e, accountName)}
          where="los pagos de la liquidación"
        />
      </Dialog>
    );
  }

  if (terms) {
    const data = preview.data;
    const shown = previewError?.message ?? error?.message;
    const facts: [string, string][] = data
      ? [
          ["Liquidación", formatMoney(data.net)],
          ["Ya pagado", formatMoney(data.paid)],
          ["Este pago", formatMoney(data.amount)],
          ["Quedará pendiente", formatMoney(data.pending_after)],
        ]
      : [];
    return (
      <Dialog
        title="Confirmá el pago"
        description={`${payroll.employee_name} · ${formatDate(terms.business_date)}`}
        onClose={onClose}
      >
        <div className="space-y-4" data-testid="payout-confirmation">
          {preview.isPending ? (
            <p className="text-sm text-muted-foreground">Calculando el pago…</p>
          ) : data ? (
            <>
              <dl
                className="divide-y divide-border rounded-md border border-border text-sm"
                data-testid="payout-preview"
              >
                {facts.map(([label, value]) => (
                  <div key={label} className="flex flex-wrap justify-between gap-x-4 gap-y-0.5 px-3 py-2">
                    <dt className="text-muted-foreground">{label}</dt>
                    <dd className="font-medium tabular-nums">{value}</dd>
                  </div>
                ))}
              </dl>
              <h3 className="text-sm font-medium">Asiento</h3>
              <MovementLegs legs={data.legs} testId="payout-legs" />
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
              <Button disabled={pending || !data} onClick={confirm}>
                {pending ? "Registrando…" : "Confirmar pago"}
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
        <div className="space-y-1.5">
          <Label htmlFor={dateId}>Fecha del pago</Label>
          <Input
            id={dateId}
            type="date"
            min={payroll.period_end}
            max={today}
            className="w-44 tabular-nums"
            value={businessDate}
            onChange={(event) => setBusinessDate(event.target.value)}
          />
          <p className="text-xs text-muted-foreground">
            Desde el fin del período ({formatDate(payroll.period_end)}) hasta hoy.
          </p>
        </div>
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">Medios de pago</legend>
          <ol className="space-y-2" data-testid="payout-means">
            {means.map((m, index) => (
              <li
                key={index}
                className="grid grid-cols-1 gap-2 rounded-md border border-border p-2 sm:grid-cols-[9rem_1fr_9rem_auto]"
              >
                <select
                  aria-label={`Medio ${index + 1}`}
                  className={selectClass}
                  value={m.method}
                  onChange={(event) => set(index, { method: event.target.value as PayoutMethod, accountId: null })}
                >
                  {(Object.keys(PAYOUT_METHOD_LABELS) as PayoutMethod[]).map((key) => (
                    <option key={key} value={key}>
                      {PAYOUT_METHOD_LABELS[key]}
                    </option>
                  ))}
                </select>
                <select
                  aria-label={`${m.method === "CASH" ? "Caja" : "Banco"} del medio ${index + 1}`}
                  className={selectClass}
                  value={m.accountId ?? ""}
                  onChange={(event) =>
                    set(index, { accountId: event.target.value ? Number(event.target.value) : null })
                  }
                >
                  <option value="">
                    {accounts.isPending
                      ? "Cargando cuentas…"
                      : m.method === "CASH"
                        ? "Elegí la caja…"
                        : "Elegí el banco…"}
                  </option>
                  {options(m.method).map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name} · saldo {formatMoney(a.balance)}
                    </option>
                  ))}
                </select>
                <Input
                  aria-label={`Importe del medio ${index + 1}`}
                  inputMode="decimal"
                  autoComplete="off"
                  placeholder="0,00"
                  className="tabular-nums"
                  value={m.amount}
                  onChange={(event) => set(index, { amount: event.target.value })}
                />
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={`Quitar el medio ${index + 1}`}
                  disabled={means.length === 1}
                  onClick={() => setMeans(means.filter((_, i) => i !== index))}
                >
                  <Trash2 className="size-4" aria-hidden />
                </Button>
              </li>
            ))}
          </ol>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setMeans([...means, { method: "TRANSFER", accountId: null, amount: "" }])}
          >
            <Plus className="size-4" aria-hidden />
            Agregar medio
          </Button>
          <p className="text-sm tabular-nums" data-testid="payout-total">
            Total del pago: {total === null ? "—" : formatMoney(total)} · pendiente {formatMoney(payment.pending)}
          </p>
        </fieldset>
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

/** Reverses the whole payout on its date (area admin + operator in Rendiciones), once, with a reason. */
export function ReversePayoutDialog({ payout, onClose }: { payout: Payout; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const action = useWriteAction<Payout>({
    slot: `employee-payout-reverse:${payout.id}`,
    write: (request) => employeeFinanceApi.writePayout(request.path, request.body),
    invalidate: [employeeFinanceQueryKey, cashQueryKey],
    isUncertain: (error) => Boolean(payoutErrorView(error).uncertain),
    onDone: onClose,
  });
  const error = action.mutation.error ? payoutErrorView(action.mutation.error) : null;
  const pending = action.mutation.isPending;
  const title = `Revertir el ${payoutTitle(payout.id).toLowerCase()}`;
  const intro = `Se registra el reverso exacto con fecha ${formatDate(payout.business_date)}: el dinero vuelve a cada caja o banco y la liquidación vuelve a quedar pendiente por ${formatMoney(payout.amount)}.`;
  const legs = [
    ...payout.items.map((i) => ({
      account_id: i.account_id,
      account_name: i.account_name,
      account_kind: METHOD_KINDS[i.method],
      amount: i.amount,
    })),
    {
      account_id: null,
      account_name: `Cuenta interna · ${payout.employee_name}`,
      account_kind: "EMPLOYEE",
      amount: `-${payout.amount}`,
    },
  ];
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const local = reason.trim() ? null : "Indicá el motivo del reverso.";
    setProblem(local);
    if (local) return;
    action.submit((requestId) => ({
      method: "POST",
      path: `/payouts/${payout.id}/reverse`,
      body: { client_request_id: requestId, reason: reason.trim() },
      summary: `Reverso del ${payoutTitle(payout.id).toLowerCase()}`,
    }));
  };
  if (action.stored) {
    return (
      <Dialog title={title} description={intro} onClose={onClose}>
        <PendingAttempt action={action} onClose={onClose} errorView={(e) => payoutErrorView(e)} where="el pago" />
      </Dialog>
    );
  }
  return (
    <Dialog title={title} description={intro} onClose={onClose}>
      <form className="space-y-4" onSubmit={submit} noValidate>
        <MovementLegs legs={legs} testId="payout-reversal-legs" />
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
