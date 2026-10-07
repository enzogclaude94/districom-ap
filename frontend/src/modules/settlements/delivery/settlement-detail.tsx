"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, CheckCircle2, Info, Lock, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useState, type ReactNode } from "react";

import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { cn } from "@/lib/cn";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";
import { cashApi, cashQueryKey } from "@/modules/settlements/cash/api";
import { SettlementPendings } from "@/modules/settlements/pending/settlement-pendings";

import {
  deliveryApi,
  deliveryQueryKey,
  settlementQueryKey,
  type Addition,
  type Expense,
  type Person,
  type Settlement,
  type Transfer,
  type TransferStatus,
} from "./api";
import { CloseDialog } from "./close-dialog";
import { CountDialog } from "./count-dialog";
import { HistoryTab } from "./history-tab";
import {
  FINANCIAL_STATE_LABELS,
  FINANCIAL_STATE_TONES,
  TRANSFER_STATUS_LABELS,
  TRANSFER_STATUS_TONES,
  differenceText,
  settlementTitle,
} from "./labels";
import {
  AdditionDialog,
  EditSettlementDialog,
  ExpenseDialog,
  TransferDecisionDialog,
  TransferDialog,
  VoidDialog,
} from "./load-dialogs";
import { ReopenDialog } from "./reopen-dialog";

export const BEING_CORRECTED_BANNER =
  "Esta rendición está siendo corregida. El cierre anterior continúa vigente en Cajas hasta que confirmes el nuevo cierre.";

type Open =
  | { kind: "count" | "addition" | "transfer" | "expense" | "edit" | "close" | "reopen" }
  | { kind: "void-addition"; addition: Addition }
  | { kind: "void-expense"; expense: Expense }
  | { kind: "decide"; transfer: Transfer; to: TransferStatus | "CORRECT" }
  | null;

function Stat({ label, value, tone, note }: { label: string; value: ReactNode; tone?: string; note?: ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-surface p-3 shadow-card">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={cn("font-display text-xl font-semibold tracking-tight tabular-nums", tone)}>{value}</p>
      {note && <div className="mt-1 text-xs">{note}</div>}
    </div>
  );
}

function Board({ settlement }: { settlement: Settlement }) {
  const s = settlement.summary;
  const declared = s.declared_transfers_count;
  return (
    <section aria-label="Tablero de la rendición" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Stat label="Esperado" value={formatMoney(s.expected)} />
      <Stat label="Efectivo" value={formatMoney(s.cash_total)} />
      <Stat
        label="Transferencias verificadas"
        value={formatMoney(s.transfers_verified)}
        note={
          declared > 0 && (
            <span className="text-warning">
              {declared === 1 ? "1 declarada" : `${declared} declaradas`} por {formatMoney(s.transfers_declared)}:
              bloquea el cierre
            </span>
          )
        }
      />
      <Stat label="Cheques" value={formatMoney(s.cheques)} />
      <Stat label="Gastos" value={formatMoney(s.expenses)} />
      <Stat label="Pendiente" value={formatMoney(s.pending)} />
      <Stat label="Justificado" value={formatMoney(s.justified)} />
      <Stat
        label="Diferencia"
        value={<span data-testid="board-difference">{differenceText(s.difference)}</span>}
        tone={
          s.difference_kind === "SHORTAGE"
            ? "text-danger"
            : s.difference_kind === "SURPLUS"
              ? "text-warning"
              : "text-success"
        }
      />
    </section>
  );
}

function Section({
  title,
  description,
  action,
  children,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-2">
        <div className="space-y-1">
          <CardTitle>{title}</CardTitle>
          {description && <CardDescription>{description}</CardDescription>}
        </div>
        {action}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

const Empty = ({ children }: { children: ReactNode }) => (
  <p className="py-3 text-center text-sm text-muted-foreground">{children}</p>
);

function Voided({ by, at, reason }: { by: string | null; at: string | null; reason: string | null }) {
  return (
    <div className="space-y-0.5">
      <Badge>Anulado</Badge>
      <p className="text-xs text-muted-foreground">
        {by} · {at ? formatDateTime(at) : ""}
        {reason && <span className="block">Motivo: {reason}</span>}
      </p>
    </div>
  );
}

function Load({
  settlement,
  editable,
  isAdmin,
  open,
  employees,
}: {
  settlement: Settlement;
  editable: boolean;
  isAdmin: boolean;
  open: (dialog: Open) => void;
  employees: Person[];
}) {
  const s = settlement.summary;
  return (
    <div className="space-y-4">
      <Section
        title="Conteo de efectivo"
        description="Billetes por denominación y la caja donde quedan."
        action={
          editable && (
            <Button variant="secondary" size="sm" onClick={() => open({ kind: "count" })}>
              {settlement.count_lines.length ? "Editar conteo" : "Cargar conteo"}
            </Button>
          )
        }
      >
        {settlement.count_lines.length === 0 ? (
          <Empty>Todavía no se contó el efectivo.</Empty>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Billete</TableHead>
                <TableHead className="text-right">Cantidad</TableHead>
                <TableHead>Caja</TableHead>
                <TableHead className="text-right">Importe</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {settlement.count_lines.map((line) => (
                <TableRow key={line.denomination_code}>
                  <TableCell className="tabular-nums">{formatMoney(line.denomination_value)}</TableCell>
                  <TableCell className="text-right tabular-nums">{line.quantity}</TableCell>
                  <TableCell>{line.destination_account_name}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatMoney(line.amount)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        {s.cash_by_box.length > 0 && (
          <p className="mt-3 text-sm text-muted-foreground">
            Efectivo por caja (conteo + agregados):{" "}
            {s.cash_by_box.map((box) => `${box.account_name} ${formatMoney(box.total)}`).join(" · ")}
          </p>
        )}
      </Section>

      <Section
        title="Agregados de efectivo"
        description="Efectivo que se suma al conteo. Un agregado no se edita: se anula con motivo."
        action={
          editable && (
            <Button variant="secondary" size="sm" onClick={() => open({ kind: "addition" })}>
              Agregar efectivo
            </Button>
          )
        }
      >
        {settlement.additions.length === 0 ? (
          <Empty>Sin agregados.</Empty>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-right">Importe</TableHead>
                <TableHead>Caja</TableHead>
                <TableHead>Descripción</TableHead>
                <TableHead>Cargado</TableHead>
                <TableHead>Estado</TableHead>
                <TableHead>
                  <span className="sr-only">Acciones</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {settlement.additions.map((addition) => (
                <TableRow key={addition.id} className={cn(addition.voided && "text-muted-foreground")}>
                  <TableCell className={cn("text-right tabular-nums", addition.voided && "line-through")}>
                    {formatMoney(addition.amount)}
                  </TableCell>
                  <TableCell>{addition.destination_account_name}</TableCell>
                  <TableCell>{addition.description ?? "—"}</TableCell>
                  <TableCell className="text-xs whitespace-nowrap">
                    {addition.created_by_name}
                    <span className="block text-muted-foreground">{formatDateTime(addition.created_at)}</span>
                  </TableCell>
                  <TableCell>
                    {addition.voided ? (
                      <Voided by={addition.voided_by_name} at={addition.voided_at} reason={addition.void_reason} />
                    ) : (
                      <Badge tone="success">Vigente</Badge>
                    )}
                  </TableCell>
                  <TableCell>
                    {editable && !addition.voided && (
                      <Button variant="ghost" size="sm" onClick={() => open({ kind: "void-addition", addition })}>
                        Anular
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Section>

      <Section
        title="Transferencias"
        description="Solo las verificadas justifican la rendición. Una transferencia declarada impide cerrar."
        action={
          editable && (
            <Button variant="secondary" size="sm" onClick={() => open({ kind: "transfer" })}>
              Declarar transferencia
            </Button>
          )
        }
      >
        {settlement.transfers.length === 0 ? (
          <Empty>Sin transferencias.</Empty>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Pagó</TableHead>
                <TableHead className="text-right">Importe</TableHead>
                <TableHead>Referencia</TableHead>
                <TableHead>Cuenta</TableHead>
                <TableHead>Estado</TableHead>
                <TableHead>Decisión</TableHead>
                <TableHead>
                  <span className="sr-only">Acciones</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {settlement.transfers.map((transfer) => (
                <TableRow key={transfer.id}>
                  <TableCell>{transfer.payer_name ?? "—"}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatMoney(transfer.amount)}</TableCell>
                  <TableCell>{transfer.reference ?? "—"}</TableCell>
                  <TableCell>{transfer.bank_account_name}</TableCell>
                  <TableCell>
                    <Badge tone={TRANSFER_STATUS_TONES[transfer.status]}>
                      {TRANSFER_STATUS_LABELS[transfer.status]}
                    </Badge>
                    {transfer.status === "DECLARED" && (
                      <span className="block text-xs text-warning">Bloquea el cierre</span>
                    )}
                  </TableCell>
                  <TableCell className="text-xs whitespace-nowrap">
                    {transfer.status_changed_by_name}
                    <span className="block text-muted-foreground">{formatDateTime(transfer.status_changed_at)}</span>
                    {transfer.status_reason && (
                      <span className="block text-muted-foreground">Motivo: {transfer.status_reason}</span>
                    )}
                  </TableCell>
                  <TableCell className="whitespace-nowrap">
                    {editable && transfer.status === "DECLARED" && (
                      <>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => open({ kind: "decide", transfer, to: "VERIFIED" })}
                        >
                          Verificar
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => open({ kind: "decide", transfer, to: "REJECTED" })}
                        >
                          Rechazar
                        </Button>
                      </>
                    )}
                    {editable && isAdmin && transfer.status !== "DECLARED" && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => open({ kind: "decide", transfer, to: "CORRECT" })}
                      >
                        Corregir decisión
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Section>

      <Section
        title="Gastos"
        description="Pagados con dinero de la rendición. Un gasto anulado queda en el historial."
        action={
          editable && (
            <Button variant="secondary" size="sm" onClick={() => open({ kind: "expense" })}>
              Registrar gasto
            </Button>
          )
        }
      >
        {settlement.expenses.length === 0 ? (
          <Empty>Sin gastos.</Empty>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Categoría</TableHead>
                <TableHead className="text-right">Importe</TableHead>
                <TableHead>Descripción</TableHead>
                <TableHead>Cargado</TableHead>
                <TableHead>Estado</TableHead>
                <TableHead>
                  <span className="sr-only">Acciones</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {settlement.expenses.map((expense) => (
                <TableRow key={expense.id} className={cn(expense.voided && "text-muted-foreground")}>
                  <TableCell>{expense.category_name}</TableCell>
                  <TableCell className={cn("text-right tabular-nums", expense.voided && "line-through")}>
                    {formatMoney(expense.amount)}
                  </TableCell>
                  <TableCell>{expense.description ?? "—"}</TableCell>
                  <TableCell className="text-xs whitespace-nowrap">
                    {expense.created_by_name}
                    <span className="block text-muted-foreground">{formatDateTime(expense.created_at)}</span>
                  </TableCell>
                  <TableCell>
                    {expense.voided ? (
                      <Voided by={expense.voided_by_name} at={expense.voided_at} reason={expense.void_reason} />
                    ) : (
                      <Badge tone="success">Vigente</Badge>
                    )}
                  </TableCell>
                  <TableCell>
                    {editable && !expense.voided && (
                      <Button variant="ghost" size="sm" onClick={() => open({ kind: "void-expense", expense })}>
                        Anular
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Section>

      <SettlementPendings settlement={settlement} editable={editable} employees={employees} />
    </div>
  );
}

function CurrentDifference({ settlement }: { settlement: Settlement }) {
  const difference = settlement.current_difference;
  if (!difference) return null;
  return (
    <p className="text-sm">
      Diferencia del cierre vigente:{" "}
      {difference.allocations
        .map(
          (allocation) =>
            `${allocation.target === "COMPANY" ? "la empresa" : allocation.employee_name} ${formatMoney(allocation.amount)}`,
        )
        .join(", ")}
      .
    </p>
  );
}

/** `/rendiciones/repartos/[id]`: the fixed board, the load, the close and the history of one settlement. */
export function SettlementDetail({ settlementId }: { settlementId: number }) {
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const query = useQuery({ queryKey: settlementQueryKey(settlementId), queryFn: () => deliveryApi.get(settlementId) });
  const accounts = useQuery({ queryKey: [...cashQueryKey, "accounts"], queryFn: cashApi.accounts });
  const role = moduleRole(me.data, "settlements");
  const canOperate = roleAtLeast(role, "operator");
  const isAdmin = roleAtLeast(role, "admin");
  const denominations = useQuery({
    queryKey: [...deliveryQueryKey, "denominations"],
    queryFn: deliveryApi.denominations,
    enabled: canOperate,
  });
  const categories = useQuery({
    queryKey: [...deliveryQueryKey, "categories"],
    queryFn: deliveryApi.categories,
    enabled: canOperate,
  });
  const employees = useQuery({
    queryKey: [...deliveryQueryKey, "employees"],
    queryFn: deliveryApi.employees,
    enabled: canOperate,
  });
  const [dialog, setDialog] = useState<Open>(null);
  const [tab, setTab] = useState<"load" | "history">("load");
  const [closedNotice, setClosedNotice] = useState<Settlement | null>(null);

  if (query.isPending) {
    return (
      <div className="space-y-3" aria-label="Cargando rendición">
        <Skeleton className="h-10 w-72" />
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-60 w-full" />
      </div>
    );
  }
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;

  const settlement = query.data;
  const editable = canOperate && settlement.status === "OPEN";
  const boxes = (accounts.data ?? []).filter((account) => account.kind === "CASH_BOX" && account.active);
  const banks = (accounts.data ?? []).filter((account) => account.kind === "BANK" && account.active);
  const close = () => setDialog(null);

  return (
    <>
      <Link
        href="/rendiciones/repartos"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" aria-hidden />
        Rendiciones de reparto
      </Link>
      <PageHeader
        title={`${settlementTitle(settlement.sequence_number)} · ${settlement.employee_name}`}
        description={`Fecha ${formatDate(settlement.business_date)}${settlement.external_reference ? ` · Referencia ${settlement.external_reference}` : ""} · Versión ${settlement.version}`}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={FINANCIAL_STATE_TONES[settlement.financial_state]} data-testid="financial-state">
              {FINANCIAL_STATE_LABELS[settlement.financial_state]}
            </Badge>
            {editable && (
              <Button variant="secondary" onClick={() => setDialog({ kind: "edit" })}>
                Corregir datos
              </Button>
            )}
            {editable && (
              <Button onClick={() => setDialog({ kind: "close" })}>
                <Lock aria-hidden />
                Cerrar rendición
              </Button>
            )}
            {isAdmin && settlement.status === "CLOSED" && (
              <Button variant="secondary" onClick={() => setDialog({ kind: "reopen" })}>
                <RotateCcw aria-hidden />
                Reabrir para corregir
              </Button>
            )}
          </div>
        }
      />

      {settlement.financial_state === "BEING_CORRECTED" && (
        <div role="status" className="flex gap-2 rounded-md border border-warning/40 bg-warning-soft px-4 py-3 text-sm">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
          <div className="space-y-1">
            <p className="font-medium">{BEING_CORRECTED_BANNER}</p>
            {settlement.reopened_by_name && settlement.reopened_at && (
              <p>
                Reabierta por {settlement.reopened_by_name} el {formatDateTime(settlement.reopened_at)}. Motivo:{" "}
                {settlement.reopen_reason}
              </p>
            )}
            <CurrentDifference settlement={settlement} />
          </div>
        </div>
      )}

      {closedNotice && (
        <div
          role="status"
          className="flex flex-wrap items-center gap-2 rounded-md border border-primary/30 bg-primary-soft px-4 py-3 text-sm"
        >
          <CheckCircle2 className="size-4 text-primary" aria-hidden />
          <span className="font-medium">Rendición cerrada. El efectivo y las transferencias ya están en Cajas.</span>
          {closedNotice.close_in_effect && (
            <Link
              href={`/rendiciones/cajas/operaciones/${closedNotice.close_in_effect.operation_id}`}
              className="text-primary hover:underline"
            >
              Ver el cierre en Cajas
            </Link>
          )}
          <button
            type="button"
            className="ml-auto text-muted-foreground hover:text-foreground"
            onClick={() => setClosedNotice(null)}
          >
            Cerrar
          </button>
        </div>
      )}

      {settlement.status === "CLOSED" && !closedNotice && (
        <div className="flex gap-2 rounded-md border border-border bg-muted px-4 py-3 text-sm">
          <Info className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
          <div className="space-y-1">
            <p>
              Cerrada por {settlement.closed_by_name} el{" "}
              {settlement.closed_at ? formatDateTime(settlement.closed_at) : "—"}.{" "}
              {settlement.close_in_effect && (
                <Link
                  href={`/rendiciones/cajas/operaciones/${settlement.close_in_effect.operation_id}`}
                  className="text-primary hover:underline"
                >
                  Ver el cierre en Cajas
                </Link>
              )}
            </p>
            <CurrentDifference settlement={settlement} />
          </div>
        </div>
      )}

      {settlement.same_day_settlement_ids.length > 0 && (
        <p className="text-sm text-muted-foreground">
          {settlement.employee_name} tiene otras rendiciones el {formatDate(settlement.business_date)}:{" "}
          {settlement.same_day_settlement_ids.map((id, i) => (
            <span key={id}>
              {i > 0 && ", "}
              <Link href={`/rendiciones/repartos/${id}`} className="text-primary hover:underline">
                ver rendición
              </Link>
            </span>
          ))}
          .
        </p>
      )}

      <Board settlement={settlement} />

      {settlement.notes && <p className="text-sm text-muted-foreground">Notas: {settlement.notes}</p>}

      <div role="tablist" aria-label="Secciones de la rendición" className="flex gap-1 border-b border-border">
        {(
          [
            ["load", "Carga"],
            ["history", "Historial"],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={tab === value}
            className={cn(
              "-mb-px border-b-2 px-3 py-2 text-sm font-medium",
              tab === value ? "border-primary text-foreground" : "border-transparent text-muted-foreground",
            )}
            onClick={() => setTab(value)}
          >
            {label}
          </button>
        ))}
      </div>

      <div role="tabpanel">
        {tab === "load" ? (
          <Load
            settlement={settlement}
            editable={editable}
            isAdmin={isAdmin}
            open={setDialog}
            employees={employees.data ?? []}
          />
        ) : (
          <HistoryTab settlement={settlement} />
        )}
      </div>

      {dialog?.kind === "count" && denominations.data && (
        <CountDialog settlement={settlement} denominations={denominations.data} boxes={boxes} onClose={close} />
      )}
      {dialog?.kind === "addition" && <AdditionDialog settlement={settlement} boxes={boxes} onClose={close} />}
      {dialog?.kind === "transfer" && <TransferDialog settlement={settlement} banks={banks} onClose={close} />}
      {dialog?.kind === "expense" && (
        <ExpenseDialog settlement={settlement} categories={categories.data ?? []} onClose={close} />
      )}
      {dialog?.kind === "edit" && <EditSettlementDialog settlement={settlement} onClose={close} />}
      {dialog?.kind === "void-addition" && (
        <VoidDialog
          settlement={settlement}
          title="Anular agregado"
          what={`El agregado de ${formatMoney(dialog.addition.amount)}`}
          path={`/additions/${dialog.addition.id}/void`}
          onClose={close}
        />
      )}
      {dialog?.kind === "void-expense" && (
        <VoidDialog
          settlement={settlement}
          title="Anular gasto"
          what={`El gasto de ${formatMoney(dialog.expense.amount)} (${dialog.expense.category_name})`}
          path={`/expenses/${dialog.expense.id}/void`}
          onClose={close}
        />
      )}
      {dialog?.kind === "decide" && (
        <TransferDecisionDialog settlement={settlement} transfer={dialog.transfer} to={dialog.to} onClose={close} />
      )}
      {dialog?.kind === "close" && (
        <CloseDialog settlement={settlement} isAdmin={isAdmin} onClose={close} onClosed={setClosedNotice} />
      )}
      {dialog?.kind === "reopen" && <ReopenDialog settlement={settlement} onClose={close} />}
    </>
  );
}
