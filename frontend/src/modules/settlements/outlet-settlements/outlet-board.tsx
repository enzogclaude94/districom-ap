"use client";

import { useQuery } from "@tanstack/react-query";
import { Banknote, Plus, Store } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useId, useState, type ReactNode } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { cn } from "@/lib/cn";
import { formatDate, formatMoney, todayIso } from "@/lib/format";
import {
  EXPECTED_UPDATE_BADGE,
  differenceText,
  differenceTone,
  settlementTitle,
} from "@/modules/settlements/delivery/labels";

import {
  outletSettlementApi,
  outletSettlementQueryKey,
  type OutletBoardRow,
  type OutletBoardSettlement,
  type OutletSettlementRef,
} from "./api";
import { NewOutletSettlementDialog } from "./new-outlet-settlement-dialog";

type BoardState = "NONE" | "OPEN" | "CLOSED" | "BEING_CORRECTED";

export const BOARD_STATE_LABELS: Record<BoardState, string> = {
  NONE: "Sin rendición",
  OPEN: "Abierta",
  CLOSED: "Cerrada",
  BEING_CORRECTED: "En corrección",
};

const BOARD_STATE_TONES: Record<BoardState, NonNullable<BadgeProps["tone"]>> = {
  NONE: "neutral",
  OPEN: "info",
  CLOSED: "success",
  BEING_CORRECTED: "warning",
};

export function boardState(settlement: Pick<OutletSettlementRef, "financial_state"> | undefined): BoardState {
  if (!settlement) return "NONE";
  return settlement.financial_state === "NOT_CLOSED" ? "OPEN" : settlement.financial_state;
}

/** «Entregar efectivo» opens on the detail page, already showing the form. */
export const remitHref = (id: number) => `/rendiciones/boca/${id}?accion=entregar`;

/**
 * Rendiciones › Bocas (R7-4): every boca on one business day, at a glance. Each card shows one settlement's own
 * figures (the first open one, else the last one); a boca with several settlements that day says so and lets
 * the user switch between them, never adding their amounts together. While a settlement is open nothing is
 * called a shortage: «Resta rendir o justificar»; the difference appears only once it is closed.
 */
export function OutletBoard({ initialDate }: { initialDate?: string }) {
  const router = useRouter();
  const dateId = useId();
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const role = moduleRole(me.data, "settlements");
  const canOperate = roleAtLeast(role, "operator");
  const isAdmin = roleAtLeast(role, "admin");
  const [businessDate, setBusinessDate] = useState(initialDate ?? todayIso());
  const [opening, setOpening] = useState<number | null>(null);
  const board = useQuery({
    queryKey: [...outletSettlementQueryKey, "board", businessDate],
    queryFn: () => outletSettlementApi.board(businessDate),
    enabled: Boolean(businessDate),
  });

  return (
    <>
      <Card>
        <CardContent className="flex flex-wrap items-end gap-4 pt-5">
          <div className="space-y-1.5">
            <Label htmlFor={dateId}>Fecha</Label>
            <Input
              id={dateId}
              type="date"
              max={todayIso()}
              value={businessDate}
              onChange={(event) => setBusinessDate(event.target.value)}
              className="w-auto"
            />
          </div>
          {board.data && (
            <p className="text-sm text-muted-foreground" data-testid="board-counts">
              {[
                [board.data.counts.open, "abiertas"],
                [board.data.counts.being_corrected, "en corrección"],
                [board.data.counts.closed, "cerradas"],
                [board.data.counts.without_settlement, "sin rendición"],
              ]
                .map(([count, label]) => `${count} ${label}`)
                .join(" · ")}
            </p>
          )}
        </CardContent>
      </Card>

      {!businessDate ? (
        <p className="text-sm text-muted-foreground">Elegí una fecha.</p>
      ) : board.isPending ? (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3" aria-label="Cargando bocas">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-64 w-full" />
          ))}
        </div>
      ) : board.isError ? (
        <ErrorState error={board.error} onRetry={() => void board.refetch()} />
      ) : board.data.rows.length === 0 ? (
        <EmptyState
          icon={Store}
          title="No hay bocas activas"
          description="Las bocas se dan de alta desde Bocas de expendio."
        />
      ) : (
        <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3" aria-label={`Bocas del ${formatDate(businessDate)}`}>
          {board.data.rows.map((row) => (
            <li key={row.outlet_id}>
              <OutletCard
                row={row}
                canOperate={canOperate}
                isAdmin={isAdmin}
                onOpen={() => setOpening(row.outlet_id)}
              />
            </li>
          ))}
        </ul>
      )}

      {opening !== null && (
        <NewOutletSettlementDialog
          initialOutletId={opening}
          initialBusinessDate={businessDate}
          onClose={() => setOpening(null)}
          onCreated={(settlement) => router.push(`/rendiciones/boca/${settlement.id}`)}
        />
      )}
    </>
  );
}

function OutletCard({
  row,
  canOperate,
  isAdmin,
  onOpen,
}: {
  row: OutletBoardRow;
  canOperate: boolean;
  isAdmin: boolean;
  onOpen: () => void;
}) {
  const [selected, setSelected] = useState<number | null>(row.current_id);
  const shown =
    row.settlements.find((item) => item.id === selected) ??
    row.settlements.find((item) => item.id === row.current_id) ??
    row.settlements[0];
  const state = boardState(shown);
  const several = row.settlements.length > 1;
  return (
    <article
      className="flex h-full flex-col gap-3 rounded-lg border border-border bg-surface p-4 shadow-card"
      aria-label={row.outlet_name}
      data-testid="board-outlet"
    >
      <header className="space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-display text-lg font-semibold tracking-tight">{row.outlet_name}</h2>
          <span className="text-xs text-muted-foreground">{row.outlet_code}</span>
          <Badge tone={BOARD_STATE_TONES[state]} className="ml-auto" data-testid="board-state">
            {BOARD_STATE_LABELS[state]}
          </Badge>
        </div>
        {!row.outlet_active && <p className="text-xs text-muted-foreground">Boca desactivada</p>}
        {shown && (
          <p className="text-sm text-muted-foreground">
            {settlementTitle(shown.sequence_number)}
            {several && (
              <span className="font-medium text-foreground" data-testid="board-multiple">
                {" "}
                · {row.settlements.length} rendiciones este día
              </span>
            )}
          </p>
        )}
        {several && (
          <div className="flex flex-wrap gap-1" role="group" aria-label={`Rendiciones de ${row.outlet_name}`}>
            {row.settlements.map((item) => (
              <Button
                key={item.id}
                size="sm"
                variant={item.id === shown?.id ? "secondary" : "ghost"}
                aria-pressed={item.id === shown?.id}
                onClick={() => setSelected(item.id)}
              >
                N.º {item.sequence_number} · {BOARD_STATE_LABELS[boardState(item)]}
              </Button>
            ))}
          </div>
        )}
      </header>

      {shown ? (
        <Figures settlement={shown} />
      ) : (
        <p className="text-sm text-muted-foreground">Esta boca todavía no tiene rendición este día.</p>
      )}

      <div className="mt-auto flex flex-wrap gap-2">
        <Actions settlement={shown} canOperate={canOperate} isAdmin={isAdmin} onOpen={onOpen} name={row.outlet_name} />
      </div>
    </article>
  );
}

function Figure({ label, children, strong = false }: { label: string; children: ReactNode; strong?: boolean }) {
  return (
    <div className={cn("flex justify-between gap-4 py-0.5", strong && "border-t border-border pt-1.5 font-semibold")}>
      <dt className={cn(!strong && "text-muted-foreground")}>{label}</dt>
      <dd className="text-right tabular-nums">{children}</dd>
    </div>
  );
}

function Figures({ settlement: s }: { settlement: OutletBoardSettlement }) {
  const closed = s.status === "CLOSED";
  return (
    <dl className="text-sm" aria-label={`Importes de la ${settlementTitle(s.sequence_number)}`}>
      <Figure label="Esperado (Store Sys)">
        {s.expected_amount === null ? (
          <span className="text-warning" data-testid="board-expected-missing">
            Sin cargar
          </span>
        ) : (
          formatMoney(s.expected_amount)
        )}
        {s.expected_update_pending && (
          <Badge tone="warning" className="ml-1" data-testid="expected-update-pending">
            {EXPECTED_UPDATE_BADGE}
          </Badge>
        )}
      </Figure>
      <Figure label="Entregas parciales">{formatMoney(s.remitted)}</Figure>
      <Figure label="Efectivo final">{formatMoney(s.cash_total)}</Figure>
      <Figure label="Transferencias verificadas">{formatMoney(s.transfers_verified)}</Figure>
      <Figure label="Cheques">{formatMoney(s.cheques)}</Figure>
      <Figure label="Gastos">{formatMoney(s.expenses)}</Figure>
      <Figure label="Total justificado" strong>
        {formatMoney(s.justified)}
      </Figure>
      {closed ? (
        <Figure label="Diferencia" strong>
          <span data-testid="board-card-difference">
            {s.difference === null ? (
              "—"
            ) : (
              <Badge tone={differenceTone(s.difference)}>{differenceText(s.difference)}</Badge>
            )}
          </span>
        </Figure>
      ) : s.justified_above_expected !== null ? (
        <Figure label="Justificado por encima del esperado" strong>
          <span data-testid="board-card-remaining">{formatMoney(s.justified_above_expected)}</span>
        </Figure>
      ) : (
        <Figure label="Resta rendir o justificar" strong>
          <span data-testid="board-card-remaining">{s.remaining === null ? "—" : formatMoney(s.remaining)}</span>
        </Figure>
      )}
      {closed && s.administrative_zero && (
        <p className="pt-1 text-xs text-muted-foreground">Cerrada en $0, sin movimientos financieros.</p>
      )}
    </dl>
  );
}

function Actions({
  settlement,
  canOperate,
  isAdmin,
  onOpen,
  name,
}: {
  settlement: OutletBoardSettlement | undefined;
  canOperate: boolean;
  isAdmin: boolean;
  onOpen: () => void;
  name: string;
}) {
  const link = (href: string, label: ReactNode, aria: string, primary = false) => (
    <Link
      href={href}
      aria-label={aria}
      className={buttonVariants({ variant: primary ? "primary" : "secondary", size: "sm" })}
    >
      {label}
    </Link>
  );
  if (!settlement) {
    return canOperate ? (
      <Button size="sm" onClick={onOpen} aria-label={`Abrir rendición de ${name}`}>
        <Plus aria-hidden />
        Abrir rendición
      </Button>
    ) : null;
  }
  const title = `${settlementTitle(settlement.sequence_number)} de ${name}`;
  const detail = `/rendiciones/boca/${settlement.id}`;
  const remit = (
    <>
      <Banknote className="size-4" aria-hidden />
      Entregar efectivo
    </>
  );
  switch (boardState(settlement)) {
    case "OPEN":
      return (
        <>
          {link(detail, "Ver rendición", `Ver ${title}`)}
          {canOperate && link(remitHref(settlement.id), remit, `Entregar efectivo de ${title}`, true)}
        </>
      );
    case "BEING_CORRECTED":
      return (
        <>
          {link(detail, "Ver corrección", `Ver corrección de ${title}`)}
          {isAdmin && link(remitHref(settlement.id), remit, `Entregar efectivo de ${title}`, true)}
        </>
      );
    default:
      return link(detail, "Ver rendición", `Ver ${title}`);
  }
}
