"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useId, useState, type FormEvent, type ReactNode } from "react";

import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { areaRole, authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";
import { parseAmount } from "@/modules/settlements/cash/amount";
import { selectClass, textareaClass } from "@/modules/settlements/delivery/form";

import {
  EMPLOYEE_FINANCE_AREA,
  employeeFinanceApi,
  employeeFinanceQueryKey,
  type Payout,
  type Payroll,
  type PayrollClosing,
  type PayrollLeg,
  type PayrollLine,
  type PayrollLineKind,
  type PayrollTotals,
} from "./api";
import {
  LINE_KIND_LABELS,
  ORIGIN_LABELS,
  PAYMENT_STATUS_LABELS,
  PAYMENT_STATUS_TONES,
  PAYOUT_METHOD_LABELS,
  PAYROLL_KIND_LABELS,
  PAYROLL_NOTICE,
  PAYROLL_STATUS_LABELS,
  PAYROLL_STATUS_TONES,
  employeeFinanceHref,
  employeeLoanHref,
  payoutHref,
  payoutTitle,
  payrollTitle,
  payrollsHref,
} from "./labels";
import { Problem } from "./movement-dialogs";
import { PayoutDialog, ReversePayoutDialog } from "./payout-dialogs";
import { periodLabel } from "./payroll-list";

const errorText = (error: unknown) =>
  error instanceof ApiError ? error.message : "No se pudo guardar. Volvé a intentar.";

const abs = (value: string) => value.replace(/^-/, "");

function usePayrollWrite(payrollId: number, onDone?: () => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      method,
      path,
      body,
    }: {
      method: "POST" | "PUT" | "PATCH" | "DELETE";
      path: string;
      body?: object;
    }) => employeeFinanceApi.writePayroll(method, path, body as Record<string, unknown> | undefined),
    onSuccess: (payroll) => {
      queryClient.setQueryData([...employeeFinanceQueryKey, "payroll", payrollId], payroll);
      void queryClient.invalidateQueries({ queryKey: employeeFinanceQueryKey });
      onDone?.();
    },
  });
}

function Facts({ items, testId }: { items: [string, ReactNode][]; testId?: string }) {
  return (
    <dl className="divide-y divide-border rounded-md border border-border text-sm" data-testid={testId}>
      {items.map(([label, value]) => (
        <div key={label} className="flex flex-wrap justify-between gap-x-4 gap-y-0.5 px-3 py-2">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="min-w-0 font-medium break-words tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Credits, deductions, credits applied and net: the backend's numbers, never recomputed here. */
function TotalsView({ totals, testId }: { totals: PayrollTotals; testId: string }) {
  const items: [string, ReactNode][] = [
    ["Haberes", formatMoney(totals.credits)],
    ["Descuentos", formatMoney(totals.deductions)],
    ["Créditos aplicados", formatMoney(totals.credits_applied)],
    [
      "Neto",
      <span key="net" className="text-base">
        {formatMoney(totals.net)}
      </span>,
    ],
  ];
  if (totals.deduction_pct !== null)
    items.push(["Descuentos / haberes", `${totals.deduction_pct.replace(".", ",")} %`]);
  return <Facts items={items} testId={testId} />;
}

function ReasonDialog({
  title,
  description,
  action,
  pending,
  error,
  onSubmit,
  onClose,
}: {
  title: string;
  description: string;
  action: string;
  pending: boolean;
  error: unknown;
  onSubmit: (reason: string) => void;
  onClose: () => void;
}) {
  const id = useId();
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!reason.trim()) return setProblem("Indicá el motivo.");
    setProblem(null);
    if (!pending) onSubmit(reason.trim());
  };
  return (
    <Dialog title={title} description={description} onClose={onClose}>
      <form className="space-y-4" onSubmit={submit} noValidate>
        <div className="space-y-1.5">
          <Label htmlFor={id}>Motivo</Label>
          <Input id={id} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
        </div>
        {(problem !== null || Boolean(error)) && <Problem>{problem ?? errorText(error)}</Problem>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button type="submit" disabled={pending}>
            {pending ? "Guardando…" : action}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

/** Add or change a credit (haber). A salary typed by hand and «Otro haber» need a note. */
function LineDialog({ payroll, line, onClose }: { payroll: Payroll; line: PayrollLine | null; onClose: () => void }) {
  const ids = { kind: useId(), amount: useId(), description: useId() };
  const [kind, setKind] = useState<PayrollLineKind>(line?.kind ?? "SALARY");
  const [amount, setAmount] = useState(line ? line.amount.replace(".", ",") : "");
  const [description, setDescription] = useState(line?.description ?? "");
  const [problem, setProblem] = useState<string | null>(null);
  const write = usePayrollWrite(payroll.id, onClose);
  const needsNote = kind === "SALARY" || kind === "OTHER_CREDIT";
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const parsed = parseAmount(amount);
    const local =
      parsed === null
        ? "Ingresá un importe válido."
        : needsNote && !description.trim()
          ? kind === "SALARY"
            ? "Un sueldo cargado a mano necesita una nota (período parcial, cambio de condición…)."
            : "Indicá la descripción del haber."
          : null;
    setProblem(local);
    if (local || write.isPending) return;
    const body = { amount: parsed, description: description.trim() || null };
    write.mutate(
      line
        ? { method: "PATCH", path: `/payroll-lines/${line.id}`, body }
        : { method: "POST", path: `/payrolls/${payroll.id}/lines`, body: { ...body, kind } },
    );
  };
  return (
    <Dialog title={line ? "Editar haber" : "Agregar haber"} description={PAYROLL_NOTICE} onClose={onClose}>
      <form className="space-y-4" onSubmit={submit} noValidate>
        {!line && (
          <div className="space-y-1.5">
            <Label htmlFor={ids.kind}>Concepto</Label>
            <select
              id={ids.kind}
              className={selectClass}
              value={kind}
              onChange={(event) => setKind(event.target.value as PayrollLineKind)}
            >
              {(Object.keys(LINE_KIND_LABELS) as PayrollLineKind[]).map((key) => (
                <option key={key} value={key}>
                  {LINE_KIND_LABELS[key]}
                </option>
              ))}
            </select>
          </div>
        )}
        <div className="space-y-1.5">
          <Label htmlFor={ids.amount}>Importe</Label>
          <Input
            id={ids.amount}
            inputMode="decimal"
            autoComplete="off"
            placeholder="0,00"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={ids.description}>{needsNote ? "Nota" : "Nota (opcional)"}</Label>
          <textarea
            id={ids.description}
            className={textareaClass}
            maxLength={300}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          {kind === "INCENTIVE" && (
            <p className="text-xs text-muted-foreground">Carga manual: no se lee de Inteligencia Comercial.</p>
          )}
        </div>
        {(problem || write.error) && <Problem>{problem ?? errorText(write.error)}</Problem>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button type="submit" disabled={write.isPending}>
            {write.isPending ? "Guardando…" : "Guardar haber"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function CloseDialog({ payroll, onClose }: { payroll: Payroll; onClose: () => void }) {
  const write = usePayrollWrite(payroll.id, onClose);
  const preview = payroll.preview!;
  const accrual =
    preview.accrual === "FINANCIAL"
      ? `Se devengan ${formatMoney(preview.credits)}: «Sueldos y bonos internos» +${formatMoney(preview.credits)} · cuenta interna −${formatMoney(preview.credits)}.`
      : "Sin haberes: el cierre no publica asiento (sin devengamiento); solo registra las aplicaciones.";
  return (
    <Dialog
      title="Cerrar liquidación"
      description={`${payroll.employee_name} · ${periodLabel(payroll)}${payroll.in_correction ? " · reemplaza el cierre vigente" : ""}`}
      onClose={onClose}
    >
      <div className="space-y-4">
        <TotalsView totals={preview} testId="payroll-close-totals" />
        <p className="text-sm">{accrual}</p>
        <p className="text-sm text-muted-foreground">
          Las aplicaciones no mueven dinero: imputan movimientos que ya están en la cuenta interna. El pago es aparte.
        </p>
        {write.error && <Problem>{errorText(write.error)}</Problem>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button
            disabled={write.isPending}
            onClick={() =>
              write.mutate({
                method: "POST",
                path: `/payrolls/${payroll.id}/close`,
                body: { version: payroll.version, preview_token: preview.preview_token, confirm: true },
              })
            }
          >
            {write.isPending ? "Cerrando…" : "Confirmar cierre"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

/** One leg of the internal account: include it, or apply part of it (never more than what is available). */
function LegRow({ leg, payroll, editable }: { leg: PayrollLeg; payroll: Payroll; editable: boolean }) {
  const [amount, setAmount] = useState(abs(leg.amount).replace(".", ","));
  const [problem, setProblem] = useState<string | null>(null);
  const write = usePayrollWrite(payroll.id);
  const choose = (included: boolean, partial?: string) =>
    write.mutate({
      method: "PUT",
      path: `/payrolls/${payroll.id}/choices`,
      body: { ledger_entry_id: leg.entry_id, included, ...(partial ? { amount: partial } : {}) },
    });
  const apply = () => {
    const parsed = parseAmount(amount);
    if (parsed === null) return setProblem("Importe inválido.");
    setProblem(null);
    choose(true, parsed);
  };
  const label = ORIGIN_LABELS[leg.origin];
  return (
    <TableRow data-testid={`payroll-leg-${leg.entry_id}`}>
      <TableCell data-label="Incluir">
        <input
          type="checkbox"
          className="size-4"
          aria-label={`Incluir ${label} del ${formatDate(leg.business_date)}`}
          checked={leg.included}
          disabled={!editable || write.isPending}
          onChange={(event) => choose(event.target.checked)}
        />
      </TableCell>
      <TableCell data-label="Origen">
        {label}
        {leg.description && <span className="block text-xs text-muted-foreground">{leg.description}</span>}
        {leg.reversed && <span className="block text-xs text-muted-foreground">Revertido después</span>}
      </TableCell>
      <TableCell data-label="Fecha" className="whitespace-nowrap tabular-nums">
        {formatDate(leg.business_date)}
      </TableCell>
      <TableCell data-label="Original" className="text-right tabular-nums">
        {formatMoney(leg.original_amount, { signed: true })}
      </TableCell>
      <TableCell data-label="Ya aplicado" className="text-right tabular-nums">
        {formatMoney(abs(leg.applied_elsewhere))}
      </TableCell>
      <TableCell data-label="Disponible" className="text-right tabular-nums">
        {formatMoney(abs(leg.available))}
      </TableCell>
      <TableCell data-label="A aplicar" className="text-right">
        {leg.included && editable ? (
          <div className="flex items-center justify-end gap-1">
            <Input
              aria-label={`Importe a aplicar de ${label}`}
              inputMode="decimal"
              className="h-8 w-28 text-right tabular-nums"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
            />
            <Button size="sm" variant="ghost" disabled={write.isPending} onClick={apply}>
              Aplicar
            </Button>
          </div>
        ) : (
          <span className="tabular-nums">{leg.included ? formatMoney(abs(leg.amount)) : "—"}</span>
        )}
        {leg.partial && <span className="block text-xs text-muted-foreground">Parcial</span>}
        {(problem || write.error) && (
          <span role="alert" className="block text-xs text-danger">
            {problem ?? errorText(write.error)}
          </span>
        )}
      </TableCell>
    </TableRow>
  );
}

/** Original and reversal of one document, both included, net to zero: shown as one correction (Enzo's precision A). */
function neutralizedGroups(legs: PayrollLeg[]) {
  const groups = new Map<number, PayrollLeg[]>();
  for (const leg of legs) groups.set(leg.group_operation_id, [...(groups.get(leg.group_operation_id) ?? []), leg]);
  return [...groups.values()].filter(
    (group) =>
      group.length > 1 &&
      group.every((leg) => leg.included) &&
      group.reduce((sum, leg) => sum + Math.round(Number(leg.amount) * 100), 0) === 0,
  );
}

function ClosingView({ closing, current }: { closing: PayrollClosing; current: boolean }) {
  return (
    <li
      className="space-y-2 rounded-md border border-border px-3 py-2 text-sm"
      data-testid={`closing-v${closing.version}`}
    >
      <p className="font-medium">
        Versión {closing.version} · {closing.kind === "FINANCIAL" ? "con devengamiento" : "sin devengamiento"}
        {current ? " · vigente" : " · reemplazada"}
      </p>
      <p className="text-muted-foreground">
        Cerrada por {closing.closed_by_name} el {formatDateTime(closing.closed_at)}
        {closing.superseded_at && ` · reemplazada el ${formatDateTime(closing.superseded_at)}`}
      </p>
      <p className="tabular-nums">
        Haberes {formatMoney(closing.credits)} · Descuentos {formatMoney(closing.deductions)} · Créditos{" "}
        {formatMoney(closing.credits_applied)} · Neto {formatMoney(closing.net)}
      </p>
      {closing.applications.length > 0 && (
        <ul className="list-inside list-disc text-muted-foreground">
          {closing.applications.map((a) => (
            <li key={`${a.entry_id}-${a.installment_id ?? 0}`}>
              {a.installment_number !== null
                ? `Cuota ${a.installment_number} del préstamo n.º ${a.loan_id}`
                : `${ORIGIN_LABELS[a.origin]} del ${formatDate(a.business_date)}`}
              : {formatMoney(a.amount, { signed: true })}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

/** One payout as history: never deleted; a reversed one keeps who, when and why (Enzo's R8-5 point 15). */
export function PayoutHistoryItem({ payout, onReverse }: { payout: Payout; onReverse?: (payout: Payout) => void }) {
  return (
    <li className="space-y-1 rounded-md border border-border px-3 py-2 text-sm" data-testid={`payout-${payout.id}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Link href={payoutHref(payout.id)} className="font-medium hover:underline">
          {payoutTitle(payout.id)}
        </Link>
        <span className="flex items-center gap-2">
          <span className={payout.in_effect ? "font-medium tabular-nums" : "tabular-nums line-through"}>
            {formatMoney(payout.amount)}
          </span>
          <Badge tone={payout.in_effect ? "success" : "neutral"}>{payout.in_effect ? "Vigente" : "Revertido"}</Badge>
        </span>
      </div>
      <p className="text-muted-foreground">
        {formatDate(payout.business_date)} ·{" "}
        {payout.items
          .map((i) => `${PAYOUT_METHOD_LABELS[i.method]} ${i.account_name} ${formatMoney(i.amount)}`)
          .join(" · ")}
      </p>
      <p className="text-muted-foreground">
        Registrado por {payout.created_by_name} el {formatDateTime(payout.created_at)} · operación n.º{" "}
        {payout.operation_id}
        {payout.reference && ` · ref. ${payout.reference}`}
      </p>
      {!payout.in_effect && (
        <p className="break-words">
          Revertido por {payout.reversed_by_name} el {formatDateTime(payout.reversed_at!)}: {payout.reversal_reason}
        </p>
      )}
      {payout.in_effect && onReverse && (
        <Button size="sm" variant="ghost" onClick={() => onReverse(payout)}>
          Revertir pago
        </Button>
      )}
    </li>
  );
}

/** «Pago» of a CLOSED payroll: net, paid and pending, all from the backend (R8-5). */
function PaymentCard({ row, canPay, canReverse }: { row: Payroll; canPay: boolean; canReverse: boolean }) {
  const [paying, setPaying] = useState(false);
  const [reversing, setReversing] = useState<Payout | null>(null);
  const payment = row.payment;
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle>Pago</CardTitle>
          {payment && (
            <Badge tone={PAYMENT_STATUS_TONES[payment.status]} data-testid="payment-status">
              {PAYMENT_STATUS_LABELS[payment.status]}
            </Badge>
          )}
        </div>
        {payment && canPay && Number(payment.pending) > 0 && (
          <Button onClick={() => setPaying(true)}>Registrar pago</Button>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        {payment ? (
          <Facts
            testId="payroll-payment"
            items={[
              ["Neto", formatMoney(payment.net)],
              ["Pagado", formatMoney(payment.paid)],
              [
                "Pendiente de pago",
                <span key="pending" className="text-base">
                  {formatMoney(payment.pending)}
                </span>,
              ],
            ]}
          />
        ) : (
          <p className="text-sm text-muted-foreground">Se paga cuando la liquidación está cerrada.</p>
        )}
        {payment && Number(payment.paid) > 0 && (
          <p className="text-sm text-muted-foreground">
            Con pagos vigentes la liquidación no se reabre: revertí los pagos o corregí en una liquidación posterior.
          </p>
        )}
        {row.payouts.length > 0 && (
          <ol className="space-y-2" data-testid="payout-history">
            {row.payouts.map((p) => (
              <PayoutHistoryItem key={p.id} payout={p} onReverse={canReverse ? setReversing : undefined} />
            ))}
          </ol>
        )}
      </CardContent>
      {paying && <PayoutDialog payroll={row} onClose={() => setPaying(false)} />}
      {reversing && <ReversePayoutDialog payout={reversing} onClose={() => setReversing(null)} />}
    </Card>
  );
}

/** `/rendiciones/empleados/liquidaciones/[id]`: the draft, its preview, the close and the history (R8-4). */
export function PayrollView({ payrollId }: { payrollId: number }) {
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const role = areaRole(me.data, EMPLOYEE_FINANCE_AREA);
  const areaAdmin = roleAtLeast(role, "admin");
  const areaOperator = roleAtLeast(role, "operator");
  // A payout moves a cash box or the bank: it also needs operator in Rendiciones (Enzo's R8-5 permissions).
  const cashOperator = roleAtLeast(moduleRole(me.data, "settlements"), "operator");
  const [lineDialog, setLineDialog] = useState<PayrollLine | "new" | null>(null);
  const [dialog, setDialog] = useState<"close" | "reopen" | "cancel" | null>(null);
  const payroll = useQuery({
    queryKey: [...employeeFinanceQueryKey, "payroll", payrollId],
    queryFn: () => employeeFinanceApi.payroll(payrollId),
  });
  const write = usePayrollWrite(payrollId, () => setDialog(null));
  if (payroll.isPending) return <Skeleton className="h-40 w-full" />;
  if (payroll.isError) return <ErrorState error={payroll.error} onRetry={() => void payroll.refetch()} />;
  const row = payroll.data;
  const open = row.status === "OPEN";
  const editable = open && areaOperator;
  const preview = row.preview;
  const neutralized = neutralizedGroups(row.legs);
  const facts: [string, ReactNode][] = [
    ["Empleado", row.employee_name],
    ["Período", periodLabel(row)],
    ["Tipo", PAYROLL_KIND_LABELS[row.kind]],
    ["Versión", String(row.version)],
  ];
  if (row.payment_date) facts.push(["Fecha de pago prevista", formatDate(row.payment_date)]);
  facts.push(["Creada", `${row.created_by_name} · ${formatDateTime(row.created_at)}`]);
  if (row.cancelled_at) facts.push(["Anulada", `${row.cancelled_by_name} · ${row.cancel_reason}`]);
  const actions = (
    <div className="flex flex-wrap gap-2">
      {open && areaAdmin && row.closing === null && (
        <Button variant="ghost" onClick={() => setDialog("cancel")}>
          Anular
        </Button>
      )}
      {row.status === "CLOSED" && areaAdmin && (
        <Button variant="secondary" onClick={() => setDialog("reopen")}>
          Reabrir
        </Button>
      )}
    </div>
  );
  return (
    <>
      <Link
        href={payrollsHref}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" aria-hidden />
        Liquidaciones internas
      </Link>
      <PageHeader title={payrollTitle(row.id)} description={PAYROLL_NOTICE} actions={actions} />

      <Card>
        <CardHeader className="flex flex-row flex-wrap items-center gap-2">
          <CardTitle>Período</CardTitle>
          <Badge tone={PAYROLL_STATUS_TONES[row.status]} data-testid="payroll-status">
            {PAYROLL_STATUS_LABELS[row.status]}
          </Badge>
          {row.in_correction && <Badge tone="warning">En corrección: el cierre anterior sigue vigente</Badge>}
        </CardHeader>
        <CardContent className="space-y-2">
          <Facts items={facts} testId="payroll-facts" />
          <Link href={employeeFinanceHref(row.employee_id)} className="text-sm hover:underline">
            Ver cuenta interna de {row.employee_name}
          </Link>
        </CardContent>
      </Card>

      {open && (
        <>
          <Card>
            <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
              <div className="space-y-1">
                <CardTitle>Haberes</CardTitle>
                <CardDescription data-testid="salary-hint">{row.salary_hint.message}</CardDescription>
              </div>
              {areaAdmin && (
                <Button variant="secondary" onClick={() => setLineDialog("new")}>
                  Agregar haber
                </Button>
              )}
            </CardHeader>
            <CardContent>
              {row.lines.length === 0 ? (
                <p className="text-sm text-muted-foreground">Sin haberes.</p>
              ) : (
                <ul
                  className="divide-y divide-border rounded-md border border-border text-sm"
                  data-testid="payroll-lines"
                >
                  {row.lines.map((line) => (
                    <li key={line.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                      <span>
                        <span className="font-medium">{LINE_KIND_LABELS[line.kind]}</span>
                        {line.source === "TERM" && (
                          <span className="text-muted-foreground"> · de la condición salarial</span>
                        )}
                        {line.description && (
                          <span className="block text-xs text-muted-foreground">{line.description}</span>
                        )}
                      </span>
                      <span className="flex items-center gap-2">
                        <span className="tabular-nums">{formatMoney(line.amount)}</span>
                        {areaAdmin && (
                          <>
                            <Button size="sm" variant="ghost" onClick={() => setLineDialog(line)}>
                              Editar
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              disabled={write.isPending}
                              onClick={() => write.mutate({ method: "DELETE", path: `/payroll-lines/${line.id}` })}
                            >
                              Quitar
                            </Button>
                          </>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Movimientos a aplicar</CardTitle>
              <CardDescription>
                {row.kind === "REGULAR"
                  ? "Lo pendiente de la cuenta interna hasta el fin del período, propuesto completo. Podés excluir o aplicar una parte."
                  : "Una liquidación extraordinaria no propone nada: incluí lo que corresponda."}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {neutralized.map((group) => (
                <p
                  key={group[0].group_operation_id}
                  className="text-sm text-muted-foreground"
                  data-testid="neutralized"
                >
                  {group[0].origin.startsWith("SETTLEMENT") ? "Corrección de rendición" : "Corrección"} · efecto neto{" "}
                  {formatMoney("0")} ({group.length} aplicaciones)
                </p>
              ))}
              {row.legs.length === 0 ? (
                <p className="text-sm text-muted-foreground">Nada pendiente de aplicar.</p>
              ) : (
                <Table stacked aria-label="Movimientos a aplicar">
                  <TableHeader>
                    <TableRow>
                      <TableHead>Incluir</TableHead>
                      <TableHead>Origen</TableHead>
                      <TableHead>Fecha</TableHead>
                      <TableHead className="text-right">Original</TableHead>
                      <TableHead className="text-right">Ya aplicado</TableHead>
                      <TableHead className="text-right">Disponible</TableHead>
                      <TableHead className="text-right">A aplicar</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {row.legs.map((leg) => (
                      <LegRow
                        key={`${leg.entry_id}-${leg.amount}-${leg.included}`}
                        leg={leg}
                        payroll={row}
                        editable={editable}
                      />
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>

          {row.kind === "REGULAR" && (
            <Card>
              <CardHeader>
                <CardTitle>Cuotas</CardTitle>
                <CardDescription>
                  Cuotas vencidas o del período, propuestas completas. Una cuota no se reduce: se incluye entera, se
                  excluye o se posterga desde su préstamo.
                </CardDescription>
              </CardHeader>
              <CardContent>
                {row.installments.length === 0 ? (
                  <p className="text-sm text-muted-foreground">Sin cuotas a aplicar.</p>
                ) : (
                  <ul
                    className="divide-y divide-border rounded-md border border-border text-sm"
                    data-testid="payroll-installments"
                  >
                    {row.installments.map((i) => (
                      <li
                        key={i.installment_id}
                        className="flex flex-wrap items-center justify-between gap-2 px-3 py-2"
                      >
                        <label className="flex items-center gap-2">
                          <input
                            type="checkbox"
                            className="size-4"
                            checked={i.included}
                            disabled={!editable || write.isPending}
                            onChange={(event) =>
                              write.mutate({
                                method: "PUT",
                                path: `/payrolls/${row.id}/choices`,
                                body: { installment_id: i.installment_id, included: event.target.checked },
                              })
                            }
                          />
                          <span>
                            Cuota {i.number} ·{" "}
                            <Link href={employeeLoanHref(i.loan_id)} className="hover:underline">
                              préstamo n.º {i.loan_id}
                            </Link>{" "}
                            · vence {formatDate(i.due_on)}
                          </span>
                          {i.overdue && <Badge tone="warning">Vencida</Badge>}
                        </label>
                        <span className="tabular-nums">{formatMoney(i.amount)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
          )}

          {preview && (
            <Card>
              <CardHeader>
                <CardTitle>Vista previa</CardTitle>
                <CardDescription>
                  {preview.accrual === "FINANCIAL"
                    ? "Al cerrar se devengan los haberes en la cuenta interna."
                    : "Sin haberes: el cierre no publica asiento."}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <TotalsView totals={preview} testId="payroll-preview" />
                {preview.blockers.map((b) => (
                  <Problem key={b.code}>{b.message}</Problem>
                ))}
                {write.error && <Problem>{errorText(write.error)}</Problem>}
                {areaAdmin && (
                  <div className="flex justify-end">
                    <Button disabled={preview.blockers.length > 0} onClick={() => setDialog("close")}>
                      Cerrar liquidación
                    </Button>
                  </div>
                )}
              </CardContent>
            </Card>
          )}
        </>
      )}

      {(row.status === "CLOSED" || row.payouts.length > 0) && (
        <PaymentCard row={row} canPay={areaOperator && cashOperator} canReverse={areaAdmin && cashOperator} />
      )}

      {row.closing && (
        <Card>
          <CardHeader>
            <CardTitle>Cierre vigente</CardTitle>
          </CardHeader>
          <CardContent>
            <TotalsView totals={row.closing} testId="payroll-closing" />
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Historia</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {row.closings.length === 0 && row.reopenings.length === 0 ? (
            <p className="text-sm text-muted-foreground">Todavía no se cerró.</p>
          ) : (
            <ol className="space-y-3" data-testid="payroll-history">
              {row.closings.map((c) => (
                <ClosingView key={c.id} closing={c} current={c.id === row.closing?.id} />
              ))}
              {row.reopenings.map((r) => (
                <li key={r.from_version} className="rounded-md border border-border px-3 py-2 text-sm">
                  Reabierta (versión {r.from_version} → {r.to_version}) por {r.reopened_by_name} el{" "}
                  {formatDateTime(r.reopened_at)}: {r.reason}
                </li>
              ))}
            </ol>
          )}
        </CardContent>
      </Card>

      {lineDialog && (
        <LineDialog payroll={row} line={lineDialog === "new" ? null : lineDialog} onClose={() => setLineDialog(null)} />
      )}
      {dialog === "close" && <CloseDialog payroll={row} onClose={() => setDialog(null)} />}
      {dialog === "reopen" && (
        <ReasonDialog
          title="Reabrir liquidación"
          description="El cierre vigente sigue en efecto hasta el nuevo cierre, que lo reemplaza."
          action="Reabrir"
          pending={write.isPending}
          error={write.error}
          onSubmit={(reason) =>
            write.mutate({ method: "POST", path: `/payrolls/${row.id}/reopen`, body: { version: row.version, reason } })
          }
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "cancel" && (
        <ReasonDialog
          title="Anular liquidación"
          description="Solo una liquidación que nunca se cerró. Libera su período."
          action="Anular"
          pending={write.isPending}
          error={write.error}
          onSubmit={(reason) => write.mutate({ method: "POST", path: `/payrolls/${row.id}/cancel`, body: { reason } })}
          onClose={() => setDialog(null)}
        />
      )}
    </>
  );
}
