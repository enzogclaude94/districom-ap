"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { CheckCircle2, HandCoins } from "lucide-react";
import Link from "next/link";
import { useId, useState } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { thirdPartiesApi, thirdPartiesQueryKey } from "@/lib/api/third-parties";
import { formatMoney } from "@/lib/format";
import { cashApi, cashQueryKey } from "@/modules/settlements/cash/api";
import { selectClass } from "@/modules/settlements/delivery/form";
import { Pager } from "@/modules/settlements/fuel/fuel-loads-view";

import {
  paymentApi,
  paymentHref,
  paymentQueryKey,
  type PaymentConcept,
  type PaymentFilters,
  type PaymentState,
} from "./api";
import { PaymentDialog } from "./payment-dialog";
import { PaymentCards, PaymentTable } from "./payment-rows";
import { CONCEPT_LABELS, STATE_LABELS } from "./labels";

const PAGE_SIZE = 20;

/**
 * `/rendiciones/pagos` (R6-1): payments made to third parties, with filters and the sum of those in effect.
 * It is what was paid, never a balance owed: DistriCom records no debts with third parties.
 */
export function PaymentList({
  initialThirdParty,
  initialCheque,
}: {
  initialThirdParty?: number;
  initialCheque?: number;
}) {
  const ids = { thirdParty: useId(), concept: useId(), account: useId(), state: useId(), from: useId(), to: useId() };
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const role = moduleRole(me.data, "settlements");
  const canOperate = roleAtLeast(role, "operator");
  const isAdmin = roleAtLeast(role, "admin");
  const accounts = useQuery({ queryKey: [...cashQueryKey, "accounts"], queryFn: cashApi.accounts });
  const thirdParties = useQuery({
    queryKey: [...thirdPartiesQueryKey, "list", "all", ""],
    queryFn: () => thirdPartiesApi.list(true),
  });
  const [thirdPartyId, setThirdPartyId] = useState<number | undefined>(initialThirdParty);
  const [concept, setConcept] = useState<PaymentConcept | "">("");
  const [accountId, setAccountId] = useState<number | undefined>();
  const [state, setState] = useState<PaymentState | "">("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(0);
  // «Entregar» on a cheque lands here with it preloaded in «Registrar pago» (R6-2).
  const [creating, setCreating] = useState(initialCheque !== undefined);
  const [notice, setNotice] = useState<{ id: number; text: string } | null>(null);

  const filters: PaymentFilters = {
    third_party_id: thirdPartyId,
    concept: concept || undefined,
    account_id: accountId,
    state: state || undefined,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  };
  const list = useQuery({
    queryKey: [...paymentQueryKey, "list", filters],
    queryFn: () => paymentApi.list(filters),
    placeholderData: keepPreviousData,
  });
  const moneyAccounts = (accounts.data ?? []).filter((a) => a.kind === "CASH_BOX" || a.kind === "BANK");
  const filtered =
    <T,>(set: (value: T) => void) =>
    (value: T) => {
      set(value);
      setPage(0);
    };

  return (
    <>
      <PageHeader
        title="Pagos"
        description="Pagos hechos a proveedores y otros terceros, en efectivo, por transferencia o con cheques de la cartera. Es lo pagado, no deudas."
        actions={
          canOperate &&
          accounts.data && (
            <Button onClick={() => setCreating(true)}>
              <HandCoins aria-hidden />
              Registrar pago
            </Button>
          )
        }
      />

      {notice && (
        <p role="status" className="flex flex-wrap items-center gap-2 text-sm text-primary">
          <CheckCircle2 className="size-4" aria-hidden />
          {notice.text}
          <Link href={paymentHref(notice.id)} className="underline">
            Ver el pago
          </Link>
        </p>
      )}

      <Card>
        <CardContent className="grid gap-3 pt-4 sm:grid-cols-2 lg:grid-cols-3">
          <div className="space-y-1.5">
            <Label htmlFor={ids.thirdParty}>Tercero</Label>
            <select
              id={ids.thirdParty}
              className={selectClass}
              value={thirdPartyId ?? ""}
              onChange={(e) => filtered(setThirdPartyId)(e.target.value ? Number(e.target.value) : undefined)}
            >
              <option value="">Todos</option>
              {(thirdParties.data ?? []).map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                  {t.active ? "" : " (desactivado)"}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.concept}>Concepto</Label>
            <select
              id={ids.concept}
              className={selectClass}
              value={concept}
              onChange={(e) => filtered(setConcept)(e.target.value as PaymentConcept | "")}
            >
              <option value="">Todos</option>
              {(Object.keys(CONCEPT_LABELS) as PaymentConcept[]).map((value) => (
                <option key={value} value={value}>
                  {CONCEPT_LABELS[value]}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.account}>Caja o banco</Label>
            <select
              id={ids.account}
              className={selectClass}
              value={accountId ?? ""}
              onChange={(e) => filtered(setAccountId)(e.target.value ? Number(e.target.value) : undefined)}
            >
              <option value="">Todas</option>
              {moneyAccounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.state}>Estado</Label>
            <select
              id={ids.state}
              className={selectClass}
              value={state}
              onChange={(e) => filtered(setState)(e.target.value as PaymentState | "")}
            >
              <option value="">Todos</option>
              {(Object.keys(STATE_LABELS) as PaymentState[]).map((value) => (
                <option key={value} value={value}>
                  {STATE_LABELS[value]}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.from}>Desde</Label>
            <Input
              id={ids.from}
              type="date"
              className="tabular-nums"
              value={dateFrom}
              onChange={(e) => filtered(setDateFrom)(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.to}>Hasta</Label>
            <Input
              id={ids.to}
              type="date"
              className="tabular-nums"
              value={dateTo}
              onChange={(e) => filtered(setDateTo)(e.target.value)}
            />
          </div>
        </CardContent>
      </Card>

      {list.isPending ? (
        <div className="space-y-2" aria-label="Cargando pagos">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : list.data.total === 0 ? (
        <EmptyState
          icon={HandCoins}
          title="No hay pagos"
          description={
            canOperate
              ? "Todavía no hay pagos con estos filtros. Se registran con «Registrar pago»."
              : "Todavía no hay pagos con estos filtros."
          }
        />
      ) : (
        <Card>
          <CardContent className="space-y-3 pt-4">
            <p className="text-sm text-muted-foreground" data-testid="payments-summary">
              {list.data.total} {list.data.total === 1 ? "pago" : "pagos"} · vigentes por{" "}
              <span className="font-medium text-foreground tabular-nums">
                {formatMoney(list.data.amount_in_effect)}
              </span>
            </p>
            <PaymentTable rows={list.data.items} />
            <PaymentCards rows={list.data.items} />
            <Pager
              page={page}
              size={PAGE_SIZE}
              shown={list.data.items.length}
              total={list.data.total}
              onPage={setPage}
            />
          </CardContent>
        </Card>
      )}

      {creating && canOperate && accounts.data && (
        <PaymentDialog
          accounts={accounts.data}
          isAdmin={isAdmin}
          initialThirdParty={thirdPartyId}
          initialCheque={initialCheque}
          onClose={() => setCreating(false)}
          onDone={(payment) => {
            setPage(0);
            setNotice({
              id: payment.id,
              text: `Pago n.º ${payment.id} registrado por ${formatMoney(payment.amount)}.`,
            });
          }}
        />
      )}
    </>
  );
}
