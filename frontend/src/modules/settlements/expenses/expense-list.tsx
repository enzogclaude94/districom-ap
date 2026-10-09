"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowLeft, Receipt } from "lucide-react";
import Link from "next/link";
import { useId, useState } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { formatDate, formatMoney } from "@/lib/format";
import { cashApi, cashQueryKey } from "@/modules/settlements/cash/api";
import { deliveryApi, deliveryQueryKey } from "@/modules/settlements/delivery/api";
import { selectClass } from "@/modules/settlements/delivery/form";

import {
  expenseApi,
  expenseHref,
  expenseQueryKey,
  type ExpenseFilters,
  type ExpenseOrigin,
  type ExpenseState,
} from "./api";
import { DirectExpenseDialog, payingAccounts } from "./direct-expense-dialog";
import { fuelSummary, ORIGIN_LABELS, STATE_LABELS, STATE_TONES } from "./labels";

const PAGE_SIZE = 20;

/** `/rendiciones/cajas/gastos`: direct expenses (by default) and, on request, those of the settlements. */
export function ExpenseList() {
  const ids = { origin: useId(), category: useId(), account: useId(), state: useId(), from: useId(), to: useId() };
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const canOperate = roleAtLeast(moduleRole(me.data, "settlements"), "operator");
  const accounts = useQuery({ queryKey: [...cashQueryKey, "accounts"], queryFn: cashApi.accounts });
  const categories = useQuery({
    queryKey: [...deliveryQueryKey, "categories", "all"],
    queryFn: deliveryApi.allCategories,
  });
  const [origin, setOrigin] = useState<ExpenseOrigin | "">("DIRECT");
  const [categoryId, setCategoryId] = useState<number | undefined>();
  const [accountId, setAccountId] = useState<number | undefined>();
  const [state, setState] = useState<ExpenseState | "">("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(0);
  const [creating, setCreating] = useState(false);

  const filters: ExpenseFilters = {
    origin: origin || undefined,
    category_id: categoryId,
    account_id: accountId,
    state: state || undefined,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  };
  const list = useQuery({
    queryKey: [...expenseQueryKey, "list", filters],
    queryFn: () => expenseApi.list(filters),
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
      <Link href="/rendiciones/cajas" className="inline-flex items-center gap-1 text-sm text-primary hover:underline">
        <ArrowLeft className="size-4" aria-hidden />
        Cajas
      </Link>
      <PageHeader
        title="Gastos directos"
        description="Gastos pagados desde una caja o el banco, fuera de una rendición. Cada uno es un movimiento de Cajas."
        actions={
          canOperate &&
          accounts.data && (
            <Button onClick={() => setCreating(true)} disabled={payingAccounts(accounts.data).length === 0}>
              <Receipt aria-hidden />
              Registrar gasto
            </Button>
          )
        }
      />

      <Card>
        <CardContent className="grid gap-3 pt-4 sm:grid-cols-2 lg:grid-cols-3">
          <div className="space-y-1.5">
            <Label htmlFor={ids.origin}>Origen</Label>
            <select
              id={ids.origin}
              className={selectClass}
              value={origin}
              onChange={(e) => filtered(setOrigin)(e.target.value as ExpenseOrigin | "")}
            >
              <option value="DIRECT">{ORIGIN_LABELS.DIRECT}</option>
              <option value="SETTLEMENT">{ORIGIN_LABELS.SETTLEMENT}</option>
              <option value="">Todos</option>
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.category}>Categoría</Label>
            <select
              id={ids.category}
              className={selectClass}
              value={categoryId ?? ""}
              onChange={(e) => filtered(setCategoryId)(e.target.value ? Number(e.target.value) : undefined)}
            >
              <option value="">Todas</option>
              {(categories.data ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                  {c.active ? "" : " (desactivada)"}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.account}>Cuenta pagadora</Label>
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
              onChange={(e) => filtered(setState)(e.target.value as ExpenseState | "")}
            >
              <option value="">Todos</option>
              {(Object.keys(STATE_LABELS) as ExpenseState[]).map((value) => (
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
        <div className="space-y-2" aria-label="Cargando gastos">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : list.data.total === 0 ? (
        <EmptyState
          title="No hay gastos"
          description={
            origin === "DIRECT"
              ? "Todavía no hay gastos directos con estos filtros. Se registran desde Cajas con «Registrar gasto»."
              : "No hay gastos con estos filtros."
          }
        />
      ) : (
        <Card>
          <CardContent className="pt-4">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Fecha</TableHead>
                  <TableHead>Categoría</TableHead>
                  <TableHead>Descripción</TableHead>
                  <TableHead className="text-right">Importe</TableHead>
                  <TableHead>Cuenta pagadora</TableHead>
                  <TableHead>Estado</TableHead>
                  <TableHead>Usuario</TableHead>
                  <TableHead>
                    <span className="sr-only">Detalle</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.data.items.map((expense) => {
                  const fuel = fuelSummary(expense);
                  return (
                    <TableRow key={expense.id} data-testid={`expense-row-${expense.id}`}>
                      <TableCell className="whitespace-nowrap tabular-nums">
                        {formatDate(expense.business_date)}
                      </TableCell>
                      <TableCell className="whitespace-nowrap">{expense.category_name}</TableCell>
                      <TableCell className="max-w-72">
                        <span className="block truncate" title={expense.description ?? undefined}>
                          {expense.description ?? "—"}
                        </span>
                        {fuel && <span className="block text-xs text-muted-foreground">{fuel}</span>}
                      </TableCell>
                      <TableCell className="text-right whitespace-nowrap tabular-nums">
                        {formatMoney(expense.amount)}
                      </TableCell>
                      <TableCell className="whitespace-nowrap">
                        {expense.origin === "DIRECT" ? (
                          expense.paid_from_account_name
                        ) : (
                          <Link
                            href={`/rendiciones/repartos/${expense.settlement_id}`}
                            className="text-primary hover:underline"
                          >
                            Rendición
                          </Link>
                        )}
                      </TableCell>
                      <TableCell>
                        <Badge tone={STATE_TONES[expense.state]}>{STATE_LABELS[expense.state]}</Badge>
                      </TableCell>
                      <TableCell className="whitespace-nowrap">{expense.created_by_name}</TableCell>
                      <TableCell>
                        {expense.origin === "DIRECT" && (
                          <Link
                            href={expenseHref(expense.id)}
                            className="whitespace-nowrap text-primary hover:underline"
                            aria-label={`Ver detalle del gasto n.º ${expense.id}`}
                          >
                            Ver detalle
                          </Link>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
            <div className="mt-3 flex items-center justify-between gap-2 text-sm text-muted-foreground">
              <span className="tabular-nums">
                {page * PAGE_SIZE + 1}–{page * PAGE_SIZE + list.data.items.length} de {list.data.total}
              </span>
              <div className="flex gap-2">
                <Button variant="secondary" size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>
                  Anterior
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={(page + 1) * PAGE_SIZE >= list.data.total}
                  onClick={() => setPage(page + 1)}
                >
                  Siguiente
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {creating && accounts.data && (
        <DirectExpenseDialog accounts={accounts.data} onClose={() => setCreating(false)} onDone={() => setPage(0)} />
      )}
    </>
  );
}
