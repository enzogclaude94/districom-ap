"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { AlertTriangle, Download, Info, Printer } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useId, useState } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/cn";
import { formatDate, formatDateTime, formatMoney, todayIso } from "@/lib/format";
import {
  expectedOriginText,
  FINANCIAL_STATE_LABELS,
  FINANCIAL_STATE_TONES,
} from "@/modules/settlements/delivery/labels";

import {
  dailyCloseApi,
  dailyCloseCsvHref,
  dailyCloseQueryKey,
  type DailyClose,
  type DayAccount,
  type DayCategory,
  type DayCheques,
  type DayKindTotals,
  type DaySettlements,
} from "./api";

export const KIND_LABELS = { CASH_BOX: "Caja", BANK: "Banco" } as const;
export const TOTAL_LABELS = { CASH_BOX: "Total cajas", BANK: "Total bancos" } as const;
export const SETTLEMENT_TYPE_LABELS = { DELIVERY: "Reparto", OUTLET: "Boca" } as const;
export const PHYSICAL_COUNT_NOTE = "Arqueo físico: No registrado.";
export const LIMITATION_NOTE =
  "Saldos contables calculados del ledger por fecha de negocio. No hay arqueo de cajas, extracto ni conciliación bancaria en Rendiciones.";
export const INVARIANT_ERROR =
  "Error de invariante del ledger: el saldo inicial más el movimiento neto no coincide con el saldo final.";

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * `/rendiciones/cierre-del-dia` (R9-2): per cash box and bank, the opening balance, the day's operations grouped by
 * type, the net and the closing balance of one business date; the settlements and the cheques of that day. The backend
 * computes every figure; the screen only shows them. Printable with the browser (no PDF).
 */
export function DailyCloseView() {
  const params = useSearchParams();
  const dateId = useId();
  const [day, setDay] = useState(() => {
    const requested = params.get("fecha");
    return requested && ISO_DATE.test(requested) ? requested : todayIso();
  });
  const view = useQuery({
    queryKey: [...dailyCloseQueryKey, day],
    queryFn: () => dailyCloseApi.get(day),
    enabled: ISO_DATE.test(day),
    placeholderData: keepPreviousData,
  });

  return (
    <>
      <PageHeader
        title="Cierre del día"
        description="Cajas y bancos por fecha de negocio. Vista calculada: no se firma ni se congela, se recalcula cada vez."
        actions={
          <div className="flex w-full flex-wrap items-end gap-2 sm:w-auto print:hidden">
            <div className="space-y-1">
              <Label htmlFor={dateId}>Fecha de negocio</Label>
              <Input id={dateId} type="date" value={day} onChange={(e) => setDay(e.target.value)} className="w-44" />
            </div>
            <a href={dailyCloseCsvHref(day)} download className={buttonVariants({ variant: "secondary" })}>
              <Download aria-hidden /> Descargar CSV
            </a>
            <Button variant="secondary" onClick={() => window.print()} disabled={!view.data}>
              <Printer aria-hidden /> Imprimir
            </Button>
          </div>
        }
      />
      {view.isPending ? (
        <Skeleton className="h-64 w-full" aria-label="Cargando cierre del día" />
      ) : view.isError ? (
        <ErrorState error={view.error} onRetry={() => void view.refetch()} />
      ) : (
        <DailyCloseReport data={view.data} />
      )}
    </>
  );
}

export function DailyCloseReport({ data }: { data: DailyClose }) {
  const broken = data.accounts.filter((a) => !a.reconciled);
  return (
    <div className="space-y-6" data-testid="daily-close">
      <p className="hidden text-sm print:block">
        Cierre del día {formatDate(data.business_date)} · calculado el {formatDateTime(data.calculated_at)}
      </p>
      {!data.reconciled && (
        <div
          role="alert"
          className="flex gap-3 rounded-md border border-danger/40 bg-danger-soft px-4 py-3 text-sm text-danger"
          data-testid="invariant-error"
        >
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          <p>
            {INVARIANT_ERROR} Cuentas: {broken.map((a) => a.name).join(", ")}.
          </p>
        </div>
      )}
      <div
        className="flex gap-3 rounded-md border border-border bg-muted/40 px-4 py-3 text-sm"
        data-testid="limitation"
      >
        <Info className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
        <p>
          <span className="font-medium">{PHYSICAL_COUNT_NOTE}</span> {LIMITATION_NOTE}
          {data.employee_movements_reserved &&
            " Los movimientos de empleados se muestran reservados y suman en los totales."}
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        {data.totals.map((t) => (
          <TotalsCard key={t.kind} totals={t} />
        ))}
      </div>

      <section aria-labelledby="accounts-heading" className="space-y-3">
        <h2 id="accounts-heading" className="text-lg font-semibold">
          Cajas y bancos
        </h2>
        {data.accounts.length === 0 ? (
          <EmptyState title="No hay cajas ni bancos" description="Todavía no hay cuentas de dinero en Rendiciones." />
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            {data.accounts.map((a) => (
              <AccountDay key={a.account_id} account={a} />
            ))}
          </div>
        )}
      </section>

      <SettlementsSection settlements={data.settlements} />
      <ChequesSection cheques={data.cheques} />
    </div>
  );
}

function Figures({ totals }: { totals: Omit<DayKindTotals, "kind"> }) {
  const rows: [string, string, boolean?][] = [
    ["Saldo inicial contable", totals.opening_balance],
    ["Ingresos del día", totals.inflows],
    ["Egresos del día", totals.outflows],
    ["Movimiento neto", totals.net],
    ["Saldo final contable", totals.closing_balance, true],
  ];
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-[1fr_auto]">
      {rows.map(([label, value, strong]) => (
        <div key={label} className="contents">
          <dt className={cn("text-muted-foreground", strong && "font-medium text-foreground")}>{label}</dt>
          <dd className={cn("text-right tabular-nums", strong && "font-semibold")}>{formatMoney(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function TotalsCard({ totals }: { totals: DayKindTotals }) {
  return (
    <Card data-testid={`totals-${totals.kind}`}>
      <CardHeader>
        <CardTitle>{TOTAL_LABELS[totals.kind]}</CardTitle>
      </CardHeader>
      <CardContent>
        <Figures totals={totals} />
      </CardContent>
    </Card>
  );
}

function Categories({ title, categories }: { title: string; categories: DayCategory[] }) {
  return (
    <div className="space-y-1">
      <h4 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{title}</h4>
      {categories.length === 0 ? (
        <p className="text-sm text-muted-foreground">Sin movimientos.</p>
      ) : (
        <ul className="space-y-1">
          {categories.map((c) => (
            <li
              key={c.key}
              className={cn("flex items-baseline justify-between gap-3 text-sm", c.reverses && "pl-3")}
              data-testid={`category-${c.key}`}
            >
              <span className="min-w-0">
                {c.label}{" "}
                <span className="text-xs text-muted-foreground">
                  ({c.operations} {c.operations === 1 ? "operación" : "operaciones"})
                </span>
                {c.reverses && (
                  <Badge tone="warning" className="ml-1">
                    Reverso
                  </Badge>
                )}
              </span>
              <span className="shrink-0 tabular-nums">{formatMoney(c.amount)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function AccountDay({ account }: { account: DayAccount }) {
  return (
    <Card data-testid={`account-${account.account_id}`} className="break-inside-avoid">
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle>{account.name}</CardTitle>
          <Badge>{KIND_LABELS[account.kind]}</Badge>
          {!account.active && <Badge>Hoy inactiva</Badge>}
          {!account.reconciled && <Badge tone="danger">Error de invariante</Badge>}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <Figures totals={account} />
        <div className="grid gap-4 sm:grid-cols-2">
          <Categories title="Ingresos" categories={account.inflow_categories} />
          <Categories title="Egresos" categories={account.outflow_categories} />
        </div>
      </CardContent>
    </Card>
  );
}

function SettlementsSection({ settlements }: { settlements: DaySettlements }) {
  const counts: [string, number][] = [
    ["Total", settlements.total],
    ["Sin cerrar", settlements.open],
    ["Cerradas", settlements.closed],
    ["En corrección", settlements.being_corrected],
    ["Sin esperado", settlements.without_expected],
    ["Store Sys pendiente", settlements.store_sys_pending],
  ];
  return (
    <section aria-labelledby="settlements-heading" className="space-y-3">
      <h2 id="settlements-heading" className="text-lg font-semibold">
        Rendiciones del día
      </h2>
      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6" data-testid="settlement-counts">
        {counts.map(([label, value]) => (
          <div key={label} className="rounded-md border border-border bg-surface px-3 py-2">
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="font-display text-xl font-semibold tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      {settlements.items.length === 0 ? (
        <EmptyState title="No hay rendiciones" description="Ninguna rendición tiene esta fecha de negocio." />
      ) : (
        <Card>
          <Table stacked>
            <TableHeader>
              <TableRow>
                <TableHead>Rendición</TableHead>
                <TableHead>Estado</TableHead>
                <TableHead className="text-right">Esperado</TableHead>
                <TableHead>Origen del esperado</TableHead>
                <TableHead className="text-right">Justificado</TableHead>
                <TableHead className="text-right">Diferencia</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {settlements.items.map((s) => (
                <TableRow key={s.id} data-testid={`settlement-${s.id}`}>
                  <TableCell data-label="Rendición">
                    {SETTLEMENT_TYPE_LABELS[s.type]} · {s.name} · Rendición {s.sequence_number}
                  </TableCell>
                  <TableCell data-label="Estado">
                    <span className="flex flex-wrap justify-end gap-1 md:justify-start">
                      <Badge tone={FINANCIAL_STATE_TONES[s.financial_state]}>
                        {FINANCIAL_STATE_LABELS[s.financial_state]}
                      </Badge>
                      {s.close_kind === "ADMINISTRATIVE_ZERO" && <Badge>Cierre en cero</Badge>}
                      {s.expected_update_pending && <Badge tone="warning">Store Sys pendiente</Badge>}
                    </span>
                  </TableCell>
                  <TableCell data-label="Esperado" className="text-right tabular-nums">
                    {s.expected_amount === null ? "Sin esperado" : formatMoney(s.expected_amount)}
                  </TableCell>
                  <TableCell data-label="Origen del esperado">
                    {s.expected_origin ? expectedOriginText(s.expected_origin) : "—"}
                  </TableCell>
                  <TableCell data-label="Justificado" className="text-right tabular-nums">
                    {s.justified === null ? "—" : formatMoney(s.justified)}
                  </TableCell>
                  <TableCell data-label="Diferencia" className="text-right tabular-nums">
                    {s.difference === null ? "—" : formatMoney(s.difference, { signed: true })}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
      {settlements.being_corrected > 0 && (
        <p className="text-sm text-muted-foreground">
          Una rendición en corrección muestra el cierre vigente en Cajas, nunca su borrador.
        </p>
      )}
    </section>
  );
}

function ChequesSection({ cheques }: { cheques: DayCheques }) {
  const rows: [string, number, string][] = [
    ["En cartera", cheques.portfolio_count, cheques.portfolio_amount],
    ["Depositados sin acreditar", cheques.in_transit_count, cheques.in_transit_amount],
    ["Depositados en el día", cheques.deposited_count, cheques.deposited_amount],
    ["Acreditados en el día", cheques.credited_count, cheques.credited_amount],
    ["Rechazados en el día", cheques.rejected_count, cheques.rejected_amount],
    ["Devueltos en el día", cheques.returned_count, cheques.returned_amount],
  ];
  return (
    <section aria-labelledby="cheques-heading" className="space-y-3">
      <Card>
        <CardHeader>
          <CardTitle id="cheques-heading">Cartera de cheques al final del día</CardTitle>
          <CardDescription>
            Cartera y depositados según el ledger hasta la fecha; el resto, por los documentos del día vigentes hoy.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2 lg:grid-cols-3" data-testid="cheques-day">
            {rows.map(([label, count, amount]) => (
              <div key={label} className="flex items-baseline justify-between gap-3 text-sm">
                <dt className="text-muted-foreground">
                  {label} <span className="text-xs">({count})</span>
                </dt>
                <dd className="tabular-nums">{formatMoney(amount)}</dd>
              </div>
            ))}
          </dl>
        </CardContent>
      </Card>
    </section>
  );
}
