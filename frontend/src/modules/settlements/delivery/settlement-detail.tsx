"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, CheckCircle2, Info, Lock, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { cn } from "@/lib/cn";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";
import { cashApi, cashQueryKey } from "@/modules/settlements/cash/api";
import { SettlementCheques } from "@/modules/settlements/cheques/settlement-cheques";
import type { Account } from "@/modules/settlements/cash/api";
import { outletSettlementApi } from "@/modules/settlements/outlet-settlements/api";
import { SettlementRemittances } from "@/modules/settlements/outlet-settlements/remittances";
import { SettlementPendings } from "@/modules/settlements/pending/settlement-pendings";

import {
  deliveryApi,
  deliveryQueryKey,
  FUEL_CODE,
  settlementHref,
  settlementQueryKey,
  type Addition,
  type Expense,
  type Person,
  type Settlement,
  type SettlementType,
  type Transfer,
  type TransferStatus,
} from "./api";
import { CloseDialog } from "./close-dialog";
import { CountDialog } from "./count-dialog";
import { formatQuantity } from "./fuel";
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
  FuelCorrectionDialog,
  TransferDecisionDialog,
  TransferDialog,
  VoidDialog,
} from "./load-dialogs";
import { ReopenDialog } from "./reopen-dialog";

export const BEING_CORRECTED_BANNER =
  "Esta rendición está siendo corregida. El cierre anterior continúa vigente en Cajas hasta que confirmes el nuevo cierre.";
/** The same, when the close in effect is an administrative zero close (R7-2): nothing of it is in Cajas. */
export const BEING_CORRECTED_ZERO_BANNER =
  "Esta rendición está siendo corregida. Su cierre en cero sigue vigente, sin movimientos en Cajas, hasta que confirmes el nuevo cierre.";
export const ZERO_CLOSE_TEXT = "Cierre administrativo cero: rendición de $0 sin movimientos, sin operación en Cajas.";

const isOutlet = (settlement: Settlement) => settlement.type === "OUTLET";

type Open =
  | { kind: "count" | "addition" | "transfer" | "expense" | "edit" | "close" | "reopen" }
  | { kind: "void-addition"; addition: Addition }
  | { kind: "void-expense"; expense: Expense }
  | { kind: "fuel"; expense: Expense }
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
  const missing = Boolean(s.expected_missing);
  return (
    <section aria-label="Tablero de la rendición" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Stat
        label={isOutlet(settlement) ? "Esperado (Store Sys)" : "Esperado"}
        value={missing ? <span data-testid="expected-missing">Sin cargar</span> : formatMoney(s.expected)}
        tone={missing ? "text-warning" : undefined}
        note={missing && <span className="text-warning">Cargalo antes de cerrar</span>}
      />
      {isOutlet(settlement) && (
        <Stat
          label="Entregas parciales"
          value={<span data-testid="board-remitted">{formatMoney(s.remitted ?? "0.00")}</span>}
        />
      )}
      <Stat label={isOutlet(settlement) ? "Efectivo final" : "Efectivo"} value={formatMoney(s.cash_total)} />
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
      {!isOutlet(settlement) && <Stat label="Pendiente" value={formatMoney(s.pending)} />}
      <Stat label="Justificado" value={formatMoney(s.justified)} />
      {s.difference === null ? (
        <Remaining summary={s} missing={missing} />
      ) : (
        <Stat
          label="Diferencia"
          value={<span data-testid="board-difference">{missing ? "—" : differenceText(s.difference)}</span>}
          tone={
            s.difference_kind === "SHORTAGE"
              ? "text-danger"
              : s.difference_kind === "SURPLUS"
                ? "text-warning"
                : "text-success"
          }
        />
      )}
    </section>
  );
}

/**
 * A boca while OPEN (R7-3): how far the justified total is from the expected amount, never a shortage or a
 * surplus. The difference exists only once the settlement is closed.
 */
function Remaining({ summary, missing }: { summary: Settlement["summary"]; missing: boolean }) {
  const above = summary.justified_above_expected;
  if (above) {
    return (
      <Stat
        label="Justificado por encima del esperado"
        value={<span data-testid="board-remaining">{formatMoney(above)}</span>}
        note={<span className="text-muted-foreground">La diferencia se calcula al cerrar.</span>}
      />
    );
  }
  return (
    <Stat
      label="Resta justificar"
      value={
        <span data-testid="board-remaining">
          {missing || summary.remaining == null ? "—" : formatMoney(summary.remaining)}
        </span>
      }
      note={<span className="text-muted-foreground">La diferencia se calcula al cerrar.</span>}
    />
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
  canOperate,
  boxes,
}: {
  settlement: Settlement;
  editable: boolean;
  isAdmin: boolean;
  open: (dialog: Open) => void;
  employees: Person[];
  canOperate: boolean;
  boxes: Account[];
}) {
  const s = settlement.summary;
  const outlet = isOutlet(settlement);
  return (
    <div className="space-y-4">
      {outlet && (
        <SettlementRemittances
          settlement={settlement}
          canOperate={canOperate}
          isAdmin={isAdmin}
          boxes={boxes}
          employees={employees}
        />
      )}
      <Section
        title={outlet ? "Conteo final de efectivo" : "Conteo de efectivo"}
        description={
          outlet
            ? "Lo que queda físicamente en la boca después de las entregas parciales: billetes por denominación y la caja donde quedan."
            : "Billetes por denominación y la caja donde quedan."
        }
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
                  <TableCell>
                    {expense.category_name}
                    {expense.fuel && (
                      <span className="block text-xs text-muted-foreground tabular-nums">
                        {expense.fuel.vehicle_plate}
                        {!expense.fuel.vehicle_active && " (desactivado)"} · {formatQuantity(expense.fuel.liters)} l × ${" "}
                        {formatQuantity(expense.fuel.price_per_liter)} · {expense.fuel.employee_name}
                        {expense.fuel.odometer_km != null &&
                          ` · ${formatQuantity(String(expense.fuel.odometer_km))} km`}
                      </span>
                    )}
                    {expense.legacy_fuel_without_detail && (
                      <Badge className="mt-1" title="Cargado antes del detalle de combustible">
                        Sin detalle (histórico)
                      </Badge>
                    )}
                  </TableCell>
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
                    {editable && !expense.voided && expense.category_code === FUEL_CODE && (
                      <Button variant="ghost" size="sm" onClick={() => open({ kind: "fuel", expense })}>
                        {expense.fuel ? "Corregir carga" : "Cargar detalle"}
                      </Button>
                    )}
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

      <SettlementCheques settlement={settlement} editable={editable} isAdmin={isAdmin} />

      {/* A boca has no pending collections (R7-2). */}
      {!isOutlet(settlement) && (
        <SettlementPendings settlement={settlement} editable={editable} employees={employees} />
      )}
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

function Responsables({ settlement }: { settlement: Settlement }) {
  const people = settlement.responsible_suggestions ?? [];
  return (
    <p className="text-sm text-muted-foreground" data-testid="responsible-suggestions">
      {people.length === 0
        ? `La boca no tiene responsables asignados el ${formatDate(settlement.business_date)}.`
        : `Responsables de la boca el ${formatDate(settlement.business_date)}: ${people.map((p) => p.full_name).join(", ")}. Son una sugerencia para asignar diferencias; nada se reparte solo.`}
    </p>
  );
}

/** A settlement link from elsewhere (a cheque, an expense, Cajas) only knows the id: a boca's opens here. */
function useOutletRedirect(settlementId: number, error: unknown, type: SettlementType) {
  const router = useRouter();
  const notFoundAsDelivery =
    type === "DELIVERY" && error instanceof ApiError && error.status === 404 && error.code === "settlement_not_found";
  const outlet = useQuery({
    queryKey: ["settlements", "outlet-settlements", "detail", settlementId],
    queryFn: () => outletSettlementApi.get(settlementId),
    enabled: notFoundAsDelivery,
    retry: false,
  });
  useEffect(() => {
    if (outlet.data) router.replace(settlementHref(outlet.data));
  }, [outlet.data, router]);
  return notFoundAsDelivery && (outlet.isPending || Boolean(outlet.data));
}

/**
 * `/rendiciones/repartos/[id]` and `/rendiciones/boca/[id]` (R7-2): the fixed board, the load, the close and
 * the history of one settlement. A boca has no driver, no pendings and no fuel; its responsables of the day
 * are shown as suggestions, and a $0 boca without movements closes administratively.
 */
export function SettlementDetail({ settlementId, type = "DELIVERY" }: { settlementId: number; type?: SettlementType }) {
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const query = useQuery({
    queryKey: settlementQueryKey(settlementId, type),
    queryFn: () => (type === "OUTLET" ? outletSettlementApi.get(settlementId) : deliveryApi.get(settlementId)),
  });
  const redirecting = useOutletRedirect(settlementId, query.error, type);
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

  if (query.isPending || redirecting) {
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
  const outlet = isOutlet(settlement);
  const who = outlet ? (settlement.outlet_name ?? "") : (settlement.employee_name ?? "");
  const zeroInEffect = settlement.close_in_effect?.kind === "ADMINISTRATIVE_ZERO";

  return (
    <>
      <Link
        href={outlet ? "/rendiciones/boca" : "/rendiciones/repartos"}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" aria-hidden />
        {outlet ? "Rendiciones de boca" : "Rendiciones de reparto"}
      </Link>
      <PageHeader
        title={`${settlementTitle(settlement.sequence_number)} · ${who}`}
        description={`${outlet ? "Boca de expendio · " : ""}Fecha ${formatDate(settlement.business_date)}${settlement.external_reference ? ` · Referencia ${settlement.external_reference}` : ""} · Versión ${settlement.version}${outlet && settlement.outlet_active === false ? " · Boca desactivada: esta rendición se puede terminar" : ""}`}
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
            <p className="font-medium">{zeroInEffect ? BEING_CORRECTED_ZERO_BANNER : BEING_CORRECTED_BANNER}</p>
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
          <span className="font-medium">
            {closedNotice.close_in_effect?.kind === "ADMINISTRATIVE_ZERO"
              ? `Rendición cerrada. ${ZERO_CLOSE_TEXT}`
              : "Rendición cerrada. El efectivo y las transferencias ya están en Cajas."}
          </span>
          {closedNotice.close_in_effect?.operation_id != null && (
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
              {zeroInEffect && <span data-testid="zero-close">{ZERO_CLOSE_TEXT}</span>}
              {settlement.close_in_effect?.operation_id != null && (
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
          {who} tiene otras rendiciones el {formatDate(settlement.business_date)}:{" "}
          {settlement.same_day_settlement_ids.map((id, i) => (
            <span key={id}>
              {i > 0 && ", "}
              <Link href={settlementHref({ id, type: settlement.type })} className="text-primary hover:underline">
                ver rendición
              </Link>
            </span>
          ))}
          .
        </p>
      )}

      {outlet && <Responsables settlement={settlement} />}

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
            canOperate={canOperate}
            boxes={boxes}
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
      {dialog?.kind === "fuel" && (
        <FuelCorrectionDialog settlement={settlement} expense={dialog.expense} onClose={close} />
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
