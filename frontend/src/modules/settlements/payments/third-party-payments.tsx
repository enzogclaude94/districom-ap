"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { authApi, currentUserQueryKey, moduleRole } from "@/lib/api/auth";
import { formatDate, formatMoney } from "@/lib/format";
import { chequeHref } from "@/modules/settlements/cheques/api";
import { Pager } from "@/modules/settlements/fuel/fuel-loads-view";

import {
  paymentApi,
  paymentHref,
  paymentQueryKey,
  type DeliveredCheque,
  type PaymentFilters,
  type ThirdPartyPaymentSummary,
} from "./api";
import { BREAKDOWN_CONCEPT_LABELS, BREAKDOWN_METHOD_LABELS, NOT_A_DEBT } from "./labels";
import { PaymentCards, PaymentTable } from "./payment-rows";

const PAGE_SIZE = 10;
const CHEQUE_PAGE_SIZE = 10;

/**
 * "Pagos" of a third party's ficha (R6-1, consolidated in R6-3): totals of the payments in effect, by concept
 * and by means, the cheques delivered to it and every payment. Only for users with access to Rendiciones.
 * Everything comes computed from the backend; it is what was paid, never a balance owed.
 */
export function ThirdPartyPayments({ thirdPartyId }: { thirdPartyId: number }) {
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const allowed = moduleRole(me.data, "settlements") !== null;
  const [page, setPage] = useState(0);
  const filters: PaymentFilters = { third_party_id: thirdPartyId, limit: PAGE_SIZE, offset: page * PAGE_SIZE };
  const summary = useQuery({
    queryKey: [...paymentQueryKey, "third-party", thirdPartyId, "summary"],
    queryFn: () => paymentApi.thirdPartySummary(thirdPartyId),
    enabled: allowed,
  });
  const list = useQuery({
    queryKey: [...paymentQueryKey, "list", filters],
    queryFn: () => paymentApi.list(filters),
    enabled: allowed,
    placeholderData: keepPreviousData,
  });
  if (!allowed) return null;

  return (
    <Card data-testid="third-party-payments">
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 space-y-1">
          <CardTitle>Pagos</CardTitle>
          <CardDescription data-testid="third-party-not-a-debt">{NOT_A_DEBT}</CardDescription>
        </div>
        <Link href={`/rendiciones/pagos?tercero=${thirdPartyId}`} className="text-sm text-primary hover:underline">
          Ver en Pagos
        </Link>
      </CardHeader>
      <CardContent className="space-y-6">
        {summary.isPending ? (
          <Skeleton className="h-24 w-full" aria-label="Cargando totales" />
        ) : summary.isError ? (
          <ErrorState error={summary.error} onRetry={() => void summary.refetch()} />
        ) : (
          <Totals summary={summary.data} />
        )}

        <DeliveredCheques thirdPartyId={thirdPartyId} />

        <section className="space-y-3" aria-labelledby="third-party-payment-list">
          <h3 id="third-party-payment-list" className="text-sm font-medium">
            Pagos registrados
          </h3>
          {list.isPending ? (
            <Skeleton className="h-24 w-full" aria-label="Cargando pagos" />
          ) : list.isError ? (
            <ErrorState error={list.error} onRetry={() => void list.refetch()} />
          ) : list.data.total === 0 ? (
            <EmptyState
              title="No hay pagos a este tercero"
              description="Aparecen acá al registrarlos en Rendiciones › Pagos."
            />
          ) : (
            <>
              <PaymentTable rows={list.data.items} withThirdParty={false} />
              <PaymentCards rows={list.data.items} withThirdParty={false} />
              <Pager
                page={page}
                size={PAGE_SIZE}
                shown={list.data.items.length}
                total={list.data.total}
                onPage={setPage}
              />
            </>
          )}
        </section>
      </CardContent>
    </Card>
  );
}

function Totals({ summary }: { summary: ThirdPartyPaymentSummary }) {
  return (
    <div className="space-y-4" data-testid="third-party-totals">
      <dl className="grid gap-3 sm:grid-cols-3">
        <Figure label="Pagos vigentes" value={String(summary.payments_in_effect)} testId="total-count" />
        <Figure label="Total pagado (vigente)" value={formatMoney(summary.amount_in_effect)} testId="total-amount" />
        <Figure
          label="Pagos revertidos"
          value={String(summary.payments_reversed)}
          hint="Quedan en la historia y no suman."
          testId="total-reversed"
        />
      </dl>
      <div className="grid gap-4 md:grid-cols-2">
        <Breakdown
          title="Por concepto"
          testId="by-concept"
          rows={summary.by_concept.map((row) => ({
            key: row.concept,
            label: BREAKDOWN_CONCEPT_LABELS[row.concept],
            count: row.count,
            amount: row.amount,
          }))}
          unit={["pago", "pagos"]}
        />
        <Breakdown
          title="Por medio"
          testId="by-method"
          rows={summary.by_method.map((row) => ({
            key: row.method,
            label: BREAKDOWN_METHOD_LABELS[row.method],
            count: row.count,
            amount: row.amount,
          }))}
          unit={["ítem", "ítems"]}
          note="En un pago mixto, cada medio suma solo su parte."
        />
      </div>
    </div>
  );
}

function Figure({ label, value, hint, testId }: { label: string; value: string; hint?: string; testId: string }) {
  return (
    <div className="rounded-md border p-3">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-lg font-medium tabular-nums" data-testid={testId}>
        {value}
      </dd>
      {hint && <dd className="text-xs text-muted-foreground">{hint}</dd>}
    </div>
  );
}

function Breakdown({
  title,
  rows,
  unit,
  note,
  testId,
}: {
  title: string;
  rows: { key: string; label: string; count: number; amount: string }[];
  unit: [string, string];
  note?: string;
  testId: string;
}) {
  return (
    <section className="space-y-2" aria-label={title} data-testid={testId}>
      <h3 className="text-sm font-medium">{title}</h3>
      <dl className="divide-y rounded-md border text-sm">
        {rows.map((row) => (
          <div
            key={row.key}
            className="flex items-baseline justify-between gap-2 px-3 py-2"
            data-testid={`${testId}-${row.key}`}
          >
            <dt className="min-w-0">
              {row.label}{" "}
              <span className="text-xs text-muted-foreground">
                ({row.count} {row.count === 1 ? unit[0] : unit[1]})
              </span>
            </dt>
            <dd className="whitespace-nowrap tabular-nums">{formatMoney(row.amount)}</dd>
          </div>
        ))}
      </dl>
      {note && <p className="text-xs text-muted-foreground">{note}</p>}
    </section>
  );
}

function DeliveredCheques({ thirdPartyId }: { thirdPartyId: number }) {
  const [page, setPage] = useState(0);
  const cheques = useQuery({
    queryKey: [...paymentQueryKey, "third-party", thirdPartyId, "cheques", page],
    queryFn: () => paymentApi.thirdPartyCheques(thirdPartyId, CHEQUE_PAGE_SIZE, page * CHEQUE_PAGE_SIZE),
    placeholderData: keepPreviousData,
  });
  return (
    <section className="space-y-3" aria-labelledby="third-party-cheques" data-testid="third-party-cheques">
      <h3 id="third-party-cheques" className="text-sm font-medium">
        Cheques entregados
      </h3>
      {cheques.isPending ? (
        <Skeleton className="h-16 w-full" aria-label="Cargando cheques entregados" />
      ) : cheques.isError ? (
        <ErrorState error={cheques.error} onRetry={() => void cheques.refetch()} />
      ) : cheques.data.total === 0 ? (
        <p className="text-sm text-muted-foreground">No se le entregaron cheques en pagos.</p>
      ) : (
        <>
          <div className="hidden md:block">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Número</TableHead>
                  <TableHead>Banco</TableHead>
                  <TableHead className="text-right">Importe</TableHead>
                  <TableHead>Entrega</TableHead>
                  <TableHead>Pago</TableHead>
                  <TableHead>Estado</TableHead>
                  <TableHead>
                    <span className="sr-only">Enlaces</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {cheques.data.items.map((c) => (
                  <TableRow key={`${c.payment_id}-${c.cheque_id}`} data-testid={`delivered-cheque-${c.cheque_id}`}>
                    <TableCell className="tabular-nums">{c.cheque_number}</TableCell>
                    <TableCell className="max-w-48 break-words">{c.bank_name}</TableCell>
                    <TableCell className="text-right whitespace-nowrap tabular-nums">{formatMoney(c.amount)}</TableCell>
                    <TableCell className="whitespace-nowrap tabular-nums">{formatDate(c.delivered_on)}</TableCell>
                    <TableCell className="whitespace-nowrap">n.º {c.payment_id}</TableCell>
                    <TableCell>
                      <ChequeStatus cheque={c} />
                    </TableCell>
                    <TableCell>
                      <ChequeLinks cheque={c} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <ul className="space-y-2 md:hidden" aria-label="Cheques entregados">
            {cheques.data.items.map((c) => (
              <li
                key={`${c.payment_id}-${c.cheque_id}`}
                className="space-y-1 rounded-md border p-3 text-sm"
                data-testid={`delivered-cheque-card-${c.cheque_id}`}
              >
                <div className="flex items-start justify-between gap-2">
                  <p className="min-w-0 break-words">
                    N.º {c.cheque_number} · {c.bank_name}
                  </p>
                  <p className="font-medium whitespace-nowrap tabular-nums">{formatMoney(c.amount)}</p>
                </div>
                <p className="text-xs text-muted-foreground">
                  Entregado el {formatDate(c.delivered_on)} en el pago n.º {c.payment_id}
                </p>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <ChequeStatus cheque={c} />
                  <ChequeLinks cheque={c} />
                </div>
              </li>
            ))}
          </ul>
          <Pager
            page={page}
            size={CHEQUE_PAGE_SIZE}
            shown={cheques.data.items.length}
            total={cheques.data.total}
            onPage={setPage}
          />
        </>
      )}
    </section>
  );
}

/** Its state now; a cheque recovered after the payment's reversal is marked so, never as still delivered. */
function ChequeStatus({ cheque }: { cheque: DeliveredCheque }) {
  return (
    <span className="flex flex-wrap items-center gap-1">
      {cheque.recovered && <Badge tone="warning">Recuperado</Badge>}
      <Badge tone="neutral">{cheque.state_label}</Badge>
    </span>
  );
}

function ChequeLinks({ cheque }: { cheque: DeliveredCheque }) {
  return (
    <span className="flex flex-wrap gap-3 whitespace-nowrap">
      <Link
        href={chequeHref(cheque.cheque_id)}
        className="text-primary hover:underline"
        aria-label={`Ver cheque n.º ${cheque.cheque_number}`}
      >
        Ver cheque
      </Link>
      <Link
        href={paymentHref(cheque.payment_id)}
        className="text-primary hover:underline"
        aria-label={`Ver pago n.º ${cheque.payment_id}`}
      >
        Ver pago
      </Link>
    </span>
  );
}
