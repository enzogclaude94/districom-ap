"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { CheckCircle2, Landmark, Wallet } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useId, useState } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { cn } from "@/lib/cn";
import { formatDate, formatMoney, todayIso } from "@/lib/format";
import { selectClass } from "@/modules/settlements/delivery/form";

import {
  chequeApi,
  chequeHref,
  chequeQueryKey,
  documentHref,
  type Cheque,
  type ChequeFilters,
  type ChequeKind,
  type ChequeOrigin,
  type ChequeState,
} from "./api";
import { CreditDialog, DepositDialog } from "./cycle-dialogs";
import {
  KIND_LABELS,
  ORIGIN_LABELS,
  PAYMENT_TYPE_LABELS,
  STATE_LABELS,
  STATE_TONES,
  depositBlocker,
  expiringText,
} from "./labels";

const PAGE_SIZE = 20;

type Payable = "" | "yes" | "no";
type Selecting = "DEPOSIT" | "CREDIT";
type Notice = { text: string; href: string } | null;

function oneOf<T extends string>(value: string | null, options: Record<T, string>): T | undefined {
  return value && value in options ? (value as T) : undefined;
}

/** Expiry of a cheque in the portfolio, or nothing once it left it. */
export function ExpiryMark({ cheque }: { cheque: Cheque }) {
  if (cheque.presentation_expired) {
    return (
      <Badge tone="danger" data-testid={`expired-${cheque.id}`}>
        Vencido
      </Badge>
    );
  }
  const soon = expiringText(cheque);
  return soon ? (
    <span className="text-xs text-warning" data-testid={`expiring-${cheque.id}`}>
      {soon}
    </span>
  ) : null;
}

/** Why a row cannot be selected for what is being done, or null. */
function blocker(cheque: Cheque, selecting: Selecting | null, today: string): string | null {
  if (selecting === "CREDIT") return cheque.state === "DEPOSITED" ? null : "Solo se acreditan cheques depositados.";
  if (cheque.state === "DEPOSITED") return selecting === "DEPOSIT" ? "Ya está depositado." : null;
  return depositBlocker(cheque, today);
}

/** `/rendiciones/cheques`: every cheque with where it is, filtered and paginated by the backend. */
export function ChequeList() {
  const params = useSearchParams();
  const ids = {
    state: useId(),
    kind: useId(),
    bank: useId(),
    origin: useId(),
    payable: useId(),
  };
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const canOperate = roleAtLeast(moduleRole(me.data, "settlements"), "operator");
  const [state, setState] = useState<ChequeState | undefined>(() => oneOf(params.get("state"), STATE_LABELS));
  const [kind, setKind] = useState<ChequeKind | undefined>();
  const [bank, setBank] = useState<number | undefined>();
  const [origin, setOrigin] = useState<ChequeOrigin | undefined>();
  const [payable, setPayable] = useState<Payable>("");
  const [expired, setExpired] = useState(() => params.get("vencidos") === "1");
  const [expiring, setExpiring] = useState(() => params.get("por_vencer") === "1");
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<Map<number, Cheque>>(new Map());
  const [dialog, setDialog] = useState<Selecting | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const today = todayIso();

  const filters: ChequeFilters = {
    state,
    kind,
    issuer_bank_id: bank,
    origin_type: origin,
    payable: payable === "" ? undefined : payable === "yes",
    expired: expired || undefined,
    expiring_soon: expiring || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  };
  const list = useQuery({
    queryKey: [...chequeQueryKey, "list", filters],
    queryFn: () => chequeApi.list(filters),
    placeholderData: keepPreviousData,
  });
  const banks = useQuery({ queryKey: [...chequeQueryKey, "banks"], queryFn: chequeApi.banks, staleTime: 300_000 });
  const filtered = Boolean(state || kind || bank || origin || payable || expired || expiring);
  const filter =
    <T,>(set: (value: T) => void) =>
    (value: T) => {
      set(value);
      setPage(0);
    };

  const chosen = [...selected.values()];
  const selecting: Selecting | null =
    chosen.length === 0 ? null : chosen[0].state === "DEPOSITED" ? "CREDIT" : "DEPOSIT";
  const toggle = (cheque: Cheque) => {
    const next = new Map(selected);
    if (next.has(cheque.id)) next.delete(cheque.id);
    else next.set(cheque.id, cheque);
    setSelected(next);
  };
  const done = (text: string, href: string) => {
    setSelected(new Map());
    setNotice({ text, href });
  };

  return (
    <>
      <PageHeader
        title="Cartera de cheques"
        description="Cada cheque con dónde está su valor: en cartera, depositado, acreditado, rechazado o devuelto. Los cheques no son dinero disponible."
      />

      {notice && (
        <div
          role="status"
          className="flex flex-wrap items-center gap-2 rounded-md border border-primary/30 bg-primary-soft px-4 py-3 text-sm"
        >
          <CheckCircle2 className="size-4 text-primary" aria-hidden />
          <span className="font-medium">{notice.text}</span>
          <Link href={notice.href} className="text-primary hover:underline">
            Ver documento
          </Link>
          <button
            type="button"
            className="ml-auto text-muted-foreground hover:text-foreground"
            onClick={() => setNotice(null)}
          >
            Cerrar
          </button>
        </div>
      )}

      <Card>
        <CardContent className="grid gap-3 pt-5 sm:grid-cols-2 lg:grid-cols-3">
          <div className="space-y-1.5">
            <Label htmlFor={ids.state}>Estado</Label>
            <select
              id={ids.state}
              className={selectClass}
              value={state ?? ""}
              onChange={(e) => filter(setState)((e.target.value || undefined) as ChequeState | undefined)}
            >
              <option value="">Todos</option>
              {(Object.keys(STATE_LABELS) as ChequeState[]).map((value) => (
                <option key={value} value={value}>
                  {STATE_LABELS[value]}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.kind}>Tipo</Label>
            <select
              id={ids.kind}
              className={selectClass}
              value={kind ?? ""}
              onChange={(e) => filter(setKind)((e.target.value || undefined) as ChequeKind | undefined)}
            >
              <option value="">Todos</option>
              {(Object.keys(KIND_LABELS) as ChequeKind[]).map((value) => (
                <option key={value} value={value}>
                  {KIND_LABELS[value]}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.bank}>Banco emisor</Label>
            <select
              id={ids.bank}
              className={selectClass}
              value={bank ?? ""}
              onChange={(e) => filter(setBank)(e.target.value ? Number(e.target.value) : undefined)}
            >
              <option value="">Todos</option>
              {(banks.data ?? []).map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.origin}>Origen</Label>
            <select
              id={ids.origin}
              className={selectClass}
              value={origin ?? ""}
              onChange={(e) => filter(setOrigin)((e.target.value || undefined) as ChequeOrigin | undefined)}
            >
              <option value="">Todos</option>
              {(Object.keys(ORIGIN_LABELS) as ChequeOrigin[]).map((value) => (
                <option key={value} value={value}>
                  {ORIGIN_LABELS[value]}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.payable}>Pagadero</Label>
            <select
              id={ids.payable}
              className={selectClass}
              value={payable}
              onChange={(e) => filter(setPayable)(e.target.value as Payable)}
            >
              <option value="">Todos</option>
              <option value="yes">Ya pagaderos</option>
              <option value="no">Todavía no pagaderos</option>
            </select>
          </div>
          <div className="flex flex-wrap items-end gap-4 pb-1.5">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={expired} onChange={(e) => filter(setExpired)(e.target.checked)} />
              Vencidos
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={expiring} onChange={(e) => filter(setExpiring)(e.target.checked)} />
              Vencen pronto
            </label>
          </div>
        </CardContent>
      </Card>

      {canOperate && chosen.length > 0 && (
        <section
          aria-label="Cheques seleccionados"
          className="sticky top-2 z-10 flex flex-wrap items-center gap-3 rounded-md border border-primary/30 bg-surface px-4 py-3 text-sm shadow-card"
        >
          <span>
            {chosen.length} {chosen.length === 1 ? "cheque seleccionado" : "cheques seleccionados"}
          </span>
          {selecting === "DEPOSIT" ? (
            <Button size="sm" onClick={() => setDialog("DEPOSIT")}>
              <Landmark aria-hidden />
              Crear boleta de depósito
            </Button>
          ) : (
            <Button size="sm" onClick={() => setDialog("CREDIT")}>
              <Wallet aria-hidden />
              Registrar acreditación
            </Button>
          )}
          <Button variant="ghost" size="sm" onClick={() => setSelected(new Map())}>
            Quitar selección
          </Button>
        </section>
      )}

      {list.isPending ? (
        <div className="space-y-2" aria-label="Cargando cheques">
          {Array.from({ length: 5 }, (_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : list.data.total === 0 ? (
        <EmptyState
          title={filtered ? "No hay cheques con esos filtros" : "Todavía no hay cheques"}
          description={
            filtered
              ? "Probá con otros filtros."
              : "Los cheques entran a la cartera al cerrar una rendición, al cobrar un pendiente con cheque o por un alta administrativa."
          }
        />
      ) : (
        <Card>
          <CardContent className="pt-4">
            {/* Phones: one card per cheque. */}
            <ul className="space-y-2 sm:hidden" aria-label="Cheques">
              {list.data.items.map((cheque) => {
                const why = canOperate ? blocker(cheque, selecting, today) : null;
                const selectable = canOperate && (cheque.state === "IN_PORTFOLIO" || cheque.state === "DEPOSITED");
                return (
                  <li key={cheque.id} className="rounded-md border border-border p-3 text-sm">
                    <div className="flex items-start justify-between gap-2">
                      <Link href={chequeHref(cheque.id)} className="font-medium text-primary hover:underline">
                        N.º {cheque.cheque_number}
                      </Link>
                      <span className="font-semibold tabular-nums">{formatMoney(cheque.amount)}</span>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {KIND_LABELS[cheque.kind]} · {PAYMENT_TYPE_LABELS[cheque.payment_type]} · {cheque.bank_name}
                    </p>
                    <p className="text-xs">Entregó: {cheque.delivered_by_name}</p>
                    <div className="mt-1 flex flex-wrap items-center gap-2">
                      <Badge tone={STATE_TONES[cheque.state]}>{cheque.state_label}</Badge>
                      <ExpiryMark cheque={cheque} />
                    </div>
                    {cheque.location_label && <p className="text-xs text-muted-foreground">{cheque.location_label}</p>}
                    {selectable && (
                      <label className="mt-2 flex items-center gap-2 text-xs">
                        <input
                          type="checkbox"
                          disabled={why !== null}
                          checked={selected.has(cheque.id)}
                          onChange={() => toggle(cheque)}
                          aria-label={`Seleccionar el cheque n.º ${cheque.cheque_number}`}
                        />
                        {why ?? "Seleccionar"}
                      </label>
                    )}
                  </li>
                );
              })}
            </ul>

            <div className="hidden sm:block">
              <Table>
                <TableHeader>
                  <TableRow>
                    {canOperate && (
                      <TableHead>
                        <span className="sr-only">Seleccionar</span>
                      </TableHead>
                    )}
                    <TableHead>Cheque</TableHead>
                    <TableHead className="text-right">Importe</TableHead>
                    <TableHead>Librador · Entregó</TableHead>
                    <TableHead>Origen</TableHead>
                    <TableHead>Emisión</TableHead>
                    <TableHead>Pagadero desde</TableHead>
                    <TableHead>Presentar hasta</TableHead>
                    <TableHead>Estado</TableHead>
                    <TableHead>Ubicación</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {list.data.items.map((cheque) => {
                    const why = canOperate ? blocker(cheque, selecting, today) : null;
                    const selectable = canOperate && (cheque.state === "IN_PORTFOLIO" || cheque.state === "DEPOSITED");
                    return (
                      <TableRow
                        key={cheque.id}
                        data-testid={`cheque-${cheque.id}`}
                        className={cn(selected.has(cheque.id) && "bg-primary-soft")}
                      >
                        {canOperate && (
                          <TableCell>
                            {selectable && (
                              <input
                                type="checkbox"
                                disabled={why !== null}
                                title={why ?? undefined}
                                checked={selected.has(cheque.id)}
                                onChange={() => toggle(cheque)}
                                aria-label={`Seleccionar el cheque n.º ${cheque.cheque_number}`}
                              />
                            )}
                          </TableCell>
                        )}
                        <TableCell>
                          <Link href={chequeHref(cheque.id)} className="font-medium text-primary hover:underline">
                            N.º {cheque.cheque_number}
                          </Link>
                          <span className="block text-xs text-muted-foreground">
                            {KIND_LABELS[cheque.kind]} · {PAYMENT_TYPE_LABELS[cheque.payment_type]} · {cheque.bank_name}
                          </span>
                          {why && selectable && <span className="block text-xs text-muted-foreground">{why}</span>}
                        </TableCell>
                        <TableCell className="text-right whitespace-nowrap tabular-nums">
                          {formatMoney(cheque.amount)}
                        </TableCell>
                        <TableCell>
                          {cheque.drawer_name}
                          <span className="block text-xs text-muted-foreground">{cheque.delivered_by_name}</span>
                        </TableCell>
                        <TableCell className="whitespace-nowrap">{ORIGIN_LABELS[cheque.origin_type]}</TableCell>
                        <TableCell className="whitespace-nowrap tabular-nums">
                          {formatDate(cheque.issue_date)}
                        </TableCell>
                        <TableCell className="whitespace-nowrap tabular-nums">
                          {formatDate(cheque.payable_from)}
                        </TableCell>
                        <TableCell className="whitespace-nowrap tabular-nums">
                          {formatDate(cheque.presentation_deadline)}
                          <span className="block">
                            <ExpiryMark cheque={cheque} />
                          </span>
                        </TableCell>
                        <TableCell>
                          <Badge tone={STATE_TONES[cheque.state]}>{cheque.state_label}</Badge>
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground">{cheque.location_label ?? "—"}</TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>

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

      {dialog === "DEPOSIT" && (
        <DepositDialog
          cheques={chosen}
          onClose={() => setDialog(null)}
          onDone={(deposit) =>
            done(
              `Boleta de depósito registrada: ${deposit.items.length} ${deposit.items.length === 1 ? "cheque" : "cheques"} por ${formatMoney(deposit.total)} en ${deposit.bank_account_name}.`,
              documentHref("DEPOSIT", deposit.id),
            )
          }
        />
      )}
      {dialog === "CREDIT" && (
        <CreditDialog
          cheques={chosen}
          onClose={() => setDialog(null)}
          onDone={(credit) =>
            done(
              `Acreditación registrada: ${formatMoney(credit.total)} en ${credit.bank_account_name}.`,
              documentHref("CREDIT", credit.id),
            )
          }
        />
      )}
    </>
  );
}
