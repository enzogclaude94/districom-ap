"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeftRight, CheckCircle2, Landmark, Plus, ShieldCheck, Wallet } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { cashApi, cashQueryKey, type Account, type Operation } from "@/modules/settlements/cash/api";
import { cn } from "@/lib/cn";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";

import { ACCOUNT_KIND_LABELS, OPERATION_LABELS } from "./labels";
import { SourceLink } from "./source-link";
import { OperationFormDialog, type CashAction } from "./operation-forms";
import { OwnerAccountDialog } from "./owner-account-dialog";
import { listPending } from "./pending-operations";

const PAGE_SIZE = 20;

type Dialog = CashAction | "owner-account" | null;

type Notice = { text: string; operationId?: number } | null;

/** Partner accounts carry contributions with the ledger sign (value leaves the partner): shown as "aportado". */
function contributed(balance: string) {
  return balance.startsWith("-") ? balance.slice(1) : balance === "0.00" ? balance : `-${balance}`;
}

function AccountCard({ account, selected, onSelect }: { account: Account; selected: boolean; onSelect: () => void }) {
  const owner = account.kind === "OWNER_EQUITY";
  return (
    <button
      type="button"
      aria-pressed={selected}
      aria-label={`Ver movimientos de ${account.name}`}
      onClick={onSelect}
      className={cn(
        "rounded-lg border bg-surface p-4 text-left shadow-card transition-colors hover:border-primary/50 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
        selected ? "border-primary ring-1 ring-primary" : "border-border",
        !account.active && "opacity-70",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="font-medium">{account.name}</p>
          <p className="text-xs text-muted-foreground">{ACCOUNT_KIND_LABELS[account.kind]}</p>
        </div>
        {!account.active && <Badge>Dada de baja</Badge>}
      </div>
      <p className="mt-3 text-xs text-muted-foreground">{owner ? "Aportado neto" : "Saldo actual"}</p>
      <p
        className={cn(
          "font-display text-2xl font-semibold tracking-tight tabular-nums",
          !owner && account.balance.startsWith("-") && "text-danger",
        )}
        data-testid={`balance-${account.id}`}
      >
        {formatMoney(owner ? contributed(account.balance) : account.balance)}
      </p>
    </button>
  );
}

function Movements({ account }: { account: Account }) {
  const [page, setPage] = useState(0);
  const filters = { limit: PAGE_SIZE, offset: page * PAGE_SIZE };
  const query = useQuery({
    queryKey: [...cashQueryKey, "movements", account.id, filters],
    queryFn: () => cashApi.movements(account.id, filters),
    placeholderData: keepPreviousData,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Movimientos de {account.name}</CardTitle>
        <CardDescription>
          Del más reciente al más antiguo. Los importes con signo indican si entró o salió dinero.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {query.isPending ? (
          <div className="space-y-2" aria-label="Cargando movimientos">
            {Array.from({ length: 4 }, (_, i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        ) : query.data.total === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">Esta cuenta todavía no tiene movimientos.</p>
        ) : (
          <>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Fecha</TableHead>
                  <TableHead>Operación</TableHead>
                  <TableHead>Descripción</TableHead>
                  <TableHead className="text-right">Importe</TableHead>
                  <TableHead>Usuario</TableHead>
                  <TableHead>Registrado</TableHead>
                  <TableHead>Origen</TableHead>
                  <TableHead>
                    <span className="sr-only">Detalle</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {query.data.items.map((movement) => (
                  <TableRow key={movement.entry_id}>
                    <TableCell className="whitespace-nowrap tabular-nums">
                      {formatDate(movement.business_date)}
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="whitespace-nowrap">{OPERATION_LABELS[movement.operation_type]}</span>
                        {movement.reversed_by_operation_id !== null && <Badge tone="warning">Revertida</Badge>}
                        {movement.reverses_operation_id !== null && (
                          <Badge>Revierte la n.º {movement.reverses_operation_id}</Badge>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="max-w-64 truncate" title={movement.description ?? undefined}>
                      {movement.description ?? "—"}
                    </TableCell>
                    <TableCell className="text-right whitespace-nowrap tabular-nums">
                      {formatMoney(movement.amount, { signed: true })}
                    </TableCell>
                    <TableCell className="whitespace-nowrap">{movement.created_by_name}</TableCell>
                    <TableCell className="whitespace-nowrap text-muted-foreground tabular-nums">
                      {formatDateTime(movement.created_at)}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-muted-foreground">
                      <SourceLink sourceType={movement.source_type} sourceId={movement.source_id} />
                    </TableCell>
                    <TableCell>
                      <Link
                        href={`/rendiciones/cajas/operaciones/${movement.operation_id}`}
                        className="whitespace-nowrap text-primary hover:underline"
                        aria-label={`Ver detalle de la operación n.º ${movement.operation_id}`}
                      >
                        Ver detalle
                      </Link>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <div className="mt-3 flex items-center justify-between gap-2 text-sm text-muted-foreground">
              <span className="tabular-nums">
                {page * PAGE_SIZE + 1}–{page * PAGE_SIZE + query.data.items.length} de {query.data.total}
              </span>
              <div className="flex gap-2">
                <Button variant="secondary" size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>
                  Anterior
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={(page + 1) * PAGE_SIZE >= query.data.total}
                  onClick={() => setPage(page + 1)}
                >
                  Siguiente
                </Button>
              </div>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function AdminActions({ open }: { open: (dialog: Dialog) => void }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="size-4 text-primary-deep" aria-hidden />
          Operaciones administrativas
        </CardTitle>
        <CardDescription>Solo administradores. Quedan auditadas con tu usuario y se pueden revertir.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" size="sm" onClick={() => open("opening-balance")}>
            Cargar saldo inicial
          </Button>
          <Button variant="secondary" size="sm" onClick={() => open("owner-contribution")}>
            Registrar aporte de socio
          </Button>
          <Button variant="secondary" size="sm" onClick={() => open("owner-withdrawal")}>
            Registrar devolución a socio
          </Button>
          <Button variant="secondary" size="sm" onClick={() => open("owner-account")}>
            <Plus aria-hidden />
            Crear cuenta de aporte
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-3 border-t border-border pt-4">
          <Button
            variant="ghost"
            size="sm"
            className="border border-dashed border-border"
            onClick={() => open("adjustment")}
          >
            Ajuste administrativo
          </Button>
          <p className="text-xs text-muted-foreground">
            Solo para corregir una diferencia comprobada, con motivo. Para deshacer una carga errónea, revertí la
            operación desde su detalle.
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

/** `/rendiciones/cajas`: balances of cash boxes, bank and partner accounts, their movements and the R1 actions. */
export function CashBoard() {
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const accounts = useQuery({ queryKey: [...cashQueryKey, "accounts"], queryFn: cashApi.accounts });
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const role = moduleRole(me.data, "settlements");
  const canOperate = roleAtLeast(role, "operator");
  const isAdmin = roleAtLeast(role, "admin");

  const all = accounts.data ?? [];
  // Technical counterparts (Saldo inicial, Ajustes) are not available money: they only show in operation details.
  const money = all.filter((account) => account.kind === "CASH_BOX" || account.kind === "BANK");
  const partners = all.filter((account) => account.kind === "OWNER_EQUITY");
  const visible = [...money, ...partners];
  const selected = visible.find((account) => account.id === selectedId) ?? visible[0];

  // Attempts with an unknown outcome stay visible until they are retried or explicitly abandoned.
  const unconfirmed = me.data && dialog === null ? listPending(me.data.id) : [];

  const done = (operation: Operation) => {
    const amount = operation.entries.find((entry) => entry.amount.startsWith("-"))?.amount.slice(1);
    setNotice({
      text: `Operación registrada: ${OPERATION_LABELS[operation.type]}${amount ? ` por ${formatMoney(amount)}` : ""}.`,
      operationId: operation.id,
    });
  };

  return (
    <>
      <PageHeader
        title="Cajas"
        description="Dónde está el dinero hoy: cajas, banco y aportes de socios, con cada movimiento que lo explica."
        actions={
          canOperate && (
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => setDialog("cash-transfer")}>
                <ArrowLeftRight aria-hidden />
                Mover entre cajas
              </Button>
              <Button variant="secondary" onClick={() => setDialog("bank-deposit")}>
                <Landmark aria-hidden />
                Depositar en banco
              </Button>
              <Button variant="secondary" onClick={() => setDialog("bank-withdrawal")}>
                <Wallet aria-hidden />
                Extraer del banco
              </Button>
            </div>
          )
        }
      />

      {unconfirmed.length > 0 && (
        <section
          aria-label="Operaciones sin confirmar"
          className="space-y-2 rounded-md border border-warning/40 bg-warning-soft px-4 py-3 text-sm"
        >
          <p className="flex items-center gap-2 font-medium">
            <AlertTriangle className="size-4 text-warning" aria-hidden />
            {unconfirmed.length === 1
              ? "Hay una operación sin confirmar"
              : `Hay ${unconfirmed.length} operaciones sin confirmar`}
          </p>
          <ul className="space-y-1">
            {unconfirmed.map((entry) => {
              const reversed = entry.slot.startsWith("reverse:") ? Number(entry.slot.slice(8)) : null;
              return (
                <li key={entry.slot} className="flex flex-wrap items-center gap-2">
                  <span>{entry.summary}</span>
                  {reversed !== null ? (
                    <Link href={`/rendiciones/cajas/operaciones/${reversed}`} className="text-primary hover:underline">
                      Revisar
                    </Link>
                  ) : (
                    <button
                      type="button"
                      className="text-primary hover:underline"
                      onClick={() => setDialog(entry.slot as CashAction)}
                    >
                      Revisar
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {notice && (
        <div
          role="status"
          className="flex flex-wrap items-center gap-2 rounded-md border border-primary/30 bg-primary-soft px-4 py-3 text-sm"
        >
          <CheckCircle2 className="size-4 text-primary" aria-hidden />
          <span className="font-medium">{notice.text}</span>
          {notice.operationId !== undefined && (
            <Link
              href={`/rendiciones/cajas/operaciones/${notice.operationId}`}
              className="text-primary hover:underline"
            >
              Ver detalle
            </Link>
          )}
          <button
            type="button"
            className="ml-auto text-muted-foreground hover:text-foreground"
            onClick={() => setNotice(null)}
          >
            Cerrar
          </button>
        </div>
      )}

      {accounts.isPending ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-label="Cargando cuentas">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-28 w-full" />
          ))}
        </div>
      ) : accounts.isError ? (
        <ErrorState error={accounts.error} onRetry={() => void accounts.refetch()} />
      ) : (
        <>
          <section aria-labelledby="money-title" className="space-y-3">
            <h2 id="money-title" className="text-sm font-semibold text-muted-foreground">
              Cajas y banco
            </h2>
            {money.length === 0 ? (
              <EmptyState title="No hay cajas" description="No se encontraron cajas ni cuentas bancarias." />
            ) : (
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {money.map((account) => (
                  <AccountCard
                    key={account.id}
                    account={account}
                    selected={account.id === selected?.id}
                    onSelect={() => setSelectedId(account.id)}
                  />
                ))}
              </div>
            )}
          </section>

          <section aria-labelledby="partners-title" className="space-y-3">
            <h2 id="partners-title" className="text-sm font-semibold text-muted-foreground">
              Aportes de socios
            </h2>
            {partners.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Todavía no hay cuentas de aporte.
                {isAdmin && " Creá una desde Operaciones administrativas."}
              </p>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {partners.map((account) => (
                  <AccountCard
                    key={account.id}
                    account={account}
                    selected={account.id === selected?.id}
                    onSelect={() => setSelectedId(account.id)}
                  />
                ))}
              </div>
            )}
          </section>

          {isAdmin && <AdminActions open={setDialog} />}

          {selected && <Movements key={selected.id} account={selected} />}
        </>
      )}

      {dialog === "owner-account" ? (
        <OwnerAccountDialog
          accounts={all}
          onClose={() => setDialog(null)}
          onDone={(account) => setNotice({ text: `Cuenta de aporte «${account.name}» creada.` })}
        />
      ) : (
        dialog && <OperationFormDialog action={dialog} accounts={all} onClose={() => setDialog(null)} onDone={done} />
      )}
    </>
  );
}
