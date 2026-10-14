"use client";

import { AlertTriangle, Banknote } from "lucide-react";
import Link from "next/link";
import { useId, useState, type FormEvent } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { apiFetch } from "@/lib/api/client";
import { cn } from "@/lib/cn";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";
import type { Account } from "@/modules/settlements/cash/api";
import { parseAmount } from "@/modules/settlements/cash/amount";
import {
  settlementPath,
  type Person,
  type Remittance,
  type RemittancePreview,
  type Settlement,
} from "@/modules/settlements/delivery/api";
import { deliveryErrorView } from "@/modules/settlements/delivery/errors";
import {
  ActionDialog,
  AmountField,
  FormError,
  PendingAttempt,
  selectClass,
  TextField,
} from "@/modules/settlements/delivery/form";
import { useDeliveryAction } from "@/modules/settlements/delivery/use-delivery-action";

/**
 * «Entregas parciales» of a boca settlement (R7-3, ADR 009): cash the boca hands over to a cash box during the
 * day, before its settlement closes. Each one moves the money at once («Entregas a cuenta — {Boca}» −X, the
 * cash box +X); the close consolidates them. Never edited nor deleted: an admin reverses one with a reason.
 */

const ZONE = "America/Argentina/Buenos_Aires";
/** Argentina has no daylight saving time: every instant of a business day is at −03:00. */
const OFFSET = "-03:00";

/** HH:MM now in Buenos Aires, the default time of a new remittance. */
export function nowInBuenosAires(now: Date = new Date()) {
  return new Intl.DateTimeFormat("es-AR", { timeZone: ZONE, hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
    .format(now)
    .replace(".", ":");
}

/** Today (YYYY-MM-DD) in Buenos Aires. */
export function todayInBuenosAires(now: Date = new Date()) {
  return new Intl.DateTimeFormat("en-CA", { timeZone: ZONE }).format(now);
}

/** The instant a remittance happened: the settlement's business date at the time given, in Buenos Aires. */
export const deliveredAt = (businessDate: string, time: string) => `${businessDate}T${time}:00${OFFSET}`;

/** HH:MM of an instant, in Buenos Aires. */
const timeOf = (iso: string) => nowInBuenosAires(new Date(iso));

/** The warning Enzo set for an excess (never «sobrante»: there is no difference until the close). */
export const excessWarning = (excess: string) =>
  `Con esta entrega lo entregado supera el importe esperado en ${formatMoney(excess)}.`;

/** Who may hand over cash now: operator or admin while never closed, only an admin while correcting. */
export function canRemit(settlement: Settlement, canOperate: boolean, isAdmin: boolean) {
  if (settlement.status !== "OPEN") return false;
  return settlement.financial_state === "BEING_CORRECTED" ? isAdmin : canOperate;
}

/**
 * Where a remittance stands while its settlement is corrected (R7-4): the close in effect keeps the ones it
 * consolidated until the new close, and the new close will consolidate the ones in effect now.
 */
export function correctionNote(row: Remittance) {
  if (row.status === "REVERSED") return row.consolidated ? "Sigue en el cierre vigente hasta el nuevo cierre" : null;
  return row.consolidated ? "Incluida en el cierre vigente" : "Se incluye en el nuevo cierre";
}

export function SettlementRemittances({
  settlement,
  canOperate,
  isAdmin,
  boxes,
  employees,
  autoOpen = false,
}: {
  settlement: Settlement;
  canOperate: boolean;
  isAdmin: boolean;
  boxes: Account[];
  employees: Person[];
  /** Open «Entregar efectivo» as soon as the cash boxes are known (from the board of bocas). */
  autoOpen?: boolean;
}) {
  const [dialog, setDialog] = useState<{ kind: "remit" } | { kind: "reverse"; remittance: Remittance } | null>(null);
  const [autoDismissed, setAutoDismissed] = useState(false);
  const rows = settlement.remittances ?? [];
  const allowed = canRemit(settlement, canOperate, isAdmin);
  const remitting = dialog?.kind === "remit" || (autoOpen && !autoDismissed && allowed && boxes.length > 0);
  const close = () => {
    setDialog(null);
    setAutoDismissed(true);
  };
  const reversible = settlement.status === "OPEN" && isAdmin;
  const correcting = settlement.financial_state === "BEING_CORRECTED";
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-2">
        <div className="space-y-1">
          <CardTitle>Entregas parciales</CardTitle>
          <CardDescription>
            Efectivo que la boca entregó a una caja durante el día. Entra a la caja en el momento; el cierre lo
            consolida. Una entrega no se edita ni se borra: un administrador la revierte con motivo.
          </CardDescription>
        </div>
        {allowed && (
          <Button variant="secondary" size="sm" onClick={() => setDialog({ kind: "remit" })}>
            <Banknote aria-hidden />
            Entregar efectivo
          </Button>
        )}
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="py-3 text-center text-sm text-muted-foreground">Sin entregas parciales.</p>
        ) : (
          <Table stacked aria-label="Entregas parciales">
            <TableHeader>
              <TableRow>
                <TableHead>Hora</TableHead>
                <TableHead className="text-right">Importe</TableHead>
                <TableHead>Caja</TableHead>
                <TableHead>Entregó</TableHead>
                <TableHead>Referencia</TableHead>
                <TableHead>Estado</TableHead>
                <TableHead>
                  <span className="sr-only">Acciones</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => {
                const reversed = row.status === "REVERSED";
                return (
                  <TableRow key={row.id} className={cn(reversed && "text-muted-foreground")} data-testid="remittance">
                    <TableCell data-label="Hora" className="tabular-nums md:whitespace-nowrap">
                      <span>
                        {timeOf(row.delivered_at)}
                        <span className="block text-xs text-muted-foreground">Cargada por {row.created_by_name}</span>
                      </span>
                    </TableCell>
                    <TableCell
                      data-label="Importe"
                      className={cn("text-right tabular-nums", reversed && "line-through")}
                    >
                      {formatMoney(row.amount)}
                    </TableCell>
                    <TableCell data-label="Caja">
                      {row.operation_id != null ? (
                        <Link
                          href={`/rendiciones/cajas/operaciones/${row.operation_id}`}
                          className="text-primary hover:underline"
                        >
                          {row.destination_account_name}
                        </Link>
                      ) : (
                        row.destination_account_name
                      )}
                    </TableCell>
                    <TableCell data-label="Entregó">{row.delivered_by_name ?? "—"}</TableCell>
                    <TableCell data-label="Referencia">{row.reference ?? "—"}</TableCell>
                    <TableCell data-label="Estado">
                      <div className="space-y-0.5">
                        {reversed ? (
                          <>
                            <Badge>Revertida</Badge>
                            <p className="text-xs">
                              {row.reversed_by_name} · {row.reversed_at ? formatDateTime(row.reversed_at) : ""}
                              {row.reversal_reason && <span className="block">Motivo: {row.reversal_reason}</span>}
                            </p>
                          </>
                        ) : (
                          <Badge tone="success">Entregada</Badge>
                        )}
                        {correcting && correctionNote(row) && (
                          <p className="text-xs text-muted-foreground" data-testid="remittance-correction-note">
                            {correctionNote(row)}
                          </p>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      {reversible && !reversed && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setDialog({ kind: "reverse", remittance: row })}
                        >
                          Revertir
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
      {remitting && <RemittanceDialog settlement={settlement} boxes={boxes} employees={employees} onClose={close} />}
      {dialog?.kind === "reverse" && (
        <ReverseRemittanceDialog settlement={settlement} remittance={dialog.remittance} onClose={close} />
      )}
    </Card>
  );
}

interface Draft {
  amount: string;
  destination: number | null;
  time: string;
  employeeId: number | null;
  reference: string;
}

function draftBody(settlement: Settlement, draft: Draft) {
  return {
    amount: parseAmount(draft.amount),
    destination_account_id: draft.destination,
    delivered_at: deliveredAt(settlement.business_date, draft.time),
    delivered_by_employee_id: draft.employeeId,
    reference: draft.reference.trim() || null,
  };
}

function draftProblem(settlement: Settlement, draft: Draft): string | null {
  if (!parseAmount(draft.amount)) return "Ingresá el importe.";
  if (draft.destination === null) return "Elegí la caja que recibe el efectivo.";
  if (!/^\d{2}:\d{2}$/.test(draft.time)) return "Indicá la hora de la entrega.";
  if (settlement.business_date === todayInBuenosAires() && draft.time > nowInBuenosAires()) {
    return "La hora de la entrega no puede ser futura.";
  }
  return null;
}

/**
 * ENTREGAR EFECTIVO: the form, then the backend's preview of what it publishes («Caja principal +$400.000 /
 * Entregas a cuenta — Boca Centro −$400.000») and, if the remittances would go above the expected amount, the
 * warning to confirm. Never a one-click action.
 */
export function RemittanceDialog({
  settlement,
  boxes,
  employees,
  onClose,
}: {
  settlement: Settlement;
  boxes: Account[];
  employees: Person[];
  onClose: () => void;
}) {
  const main = boxes.find((box) => box.code === "main_cash") ?? boxes[0];
  const suggested = settlement.responsible_suggestions ?? [];
  const [draft, setDraft] = useState<Draft>({
    amount: "",
    destination: main?.id ?? null,
    time: settlement.business_date === todayInBuenosAires() ? nowInBuenosAires() : "",
    employeeId: suggested.length === 1 ? suggested[0]!.employee_id : null,
    reference: "",
  });
  const [preview, setPreview] = useState<RemittancePreview | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const action = useDeliveryAction(`remittance:${settlement.id}`, onClose);
  const timeId = useId();
  const employeeId = useId();
  const destinationId = useId();
  const others = employees.filter((person) => !suggested.some((s) => s.employee_id === person.id));
  const set = (change: Partial<Draft>) => setDraft((current) => ({ ...current, ...change }));
  const pending = action.mutation.isPending;
  const error = action.mutation.error ? deliveryErrorView(action.mutation.error) : null;

  const review = async (event: FormEvent) => {
    event.preventDefault();
    const local = draftProblem(settlement, draft);
    setProblem(local);
    if (local) return;
    setLoading(true);
    try {
      setPreview(
        await apiFetch<RemittancePreview>(`/settlements${settlementPath(settlement, "/remittances/preview")}`, {
          method: "POST",
          body: JSON.stringify(draftBody(settlement, draft)),
        }),
      );
    } catch (caught) {
      setProblem(deliveryErrorView(caught).message);
    } finally {
      setLoading(false);
    }
  };

  const confirm = () => {
    if (!preview) return;
    action.submit((requestId) => ({
      method: "POST",
      path: settlementPath(settlement, "/remittances"),
      body: {
        client_request_id: requestId,
        ...draftBody(settlement, draft),
        confirm_above_expected: preview.excess !== null,
      },
      summary: `Entrega de ${formatMoney(parseAmount(draft.amount)!)} de ${settlement.outlet_name ?? "la boca"} a ${preview.legs[0]?.account_name ?? "la caja"}`,
    }));
  };

  return (
    <Dialog
      title="Entregar efectivo"
      description={`Efectivo que ${settlement.outlet_name ?? "la boca"} entrega a una caja el ${formatDate(settlement.business_date)}. Entra a la caja ahora; el cierre de la rendición lo consolida.`}
      onClose={onClose}
    >
      {action.stored ? (
        <PendingAttempt action={action} onClose={onClose} />
      ) : preview ? (
        <div className="space-y-4" data-testid="remittance-confirmation">
          <p className="text-sm font-medium">Se va a registrar en Cajas:</p>
          <dl className="rounded-md border border-border px-3 py-2 text-sm" aria-label="Movimiento en Cajas">
            {preview.legs.map((leg) => (
              <div key={leg.account_name} className="flex justify-between gap-4 py-1">
                <dt>{leg.account_name}</dt>
                <dd className="tabular-nums">{formatMoney(leg.amount, { signed: true })}</dd>
              </div>
            ))}
          </dl>
          <p className="text-sm" data-testid="remittance-explanation">
            Este efectivo entra ahora a {preview.legs[0]?.account_name ?? "la caja"} y quedará imputado a esta rendición
            cuando se cierre.
          </p>
          <p className="text-sm text-muted-foreground">
            Entregado en esta rendición: {formatMoney(preview.remitted_before)} → {formatMoney(preview.remitted_after)}
            {preview.expected !== null && ` · Esperado ${formatMoney(preview.expected)}`}
          </p>
          {preview.excess !== null && (
            <div
              role="alert"
              className="flex gap-2 rounded-md border border-warning/40 bg-warning-soft px-3 py-2 text-sm"
              data-testid="remittance-excess"
            >
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
              <p>{excessWarning(preview.excess)} ¿Confirmás la entrega igual?</p>
            </div>
          )}
          <FormError message={error?.message} />
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" disabled={pending} onClick={() => setPreview(null)}>
              Volver
            </Button>
            <Button disabled={pending} onClick={confirm}>
              {pending ? "Guardando…" : "Confirmar entrega"}
            </Button>
          </div>
        </div>
      ) : (
        <form className="space-y-4" onSubmit={(event) => void review(event)} noValidate>
          <fieldset disabled={loading} className="space-y-4">
            <AmountField value={draft.amount} onChange={(amount) => set({ amount })} />
            <div className="space-y-1.5">
              <Label htmlFor={destinationId}>Caja que recibe</Label>
              <select
                id={destinationId}
                className={selectClass}
                value={draft.destination ?? ""}
                onChange={(event) => set({ destination: event.target.value ? Number(event.target.value) : null })}
              >
                <option value="">Elegí una caja…</option>
                {boxes.map((box) => (
                  <option key={box.id} value={box.id}>
                    {box.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={timeId}>Hora de la entrega</Label>
              <Input
                id={timeId}
                type="time"
                value={draft.time}
                onChange={(event) => set({ time: event.target.value })}
                aria-describedby={`${timeId}-hint`}
              />
              <p id={`${timeId}-hint`} className="text-xs text-muted-foreground">
                Del {formatDate(settlement.business_date)}, hora de Argentina.
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={employeeId}>Quién entregó (opcional)</Label>
              <select
                id={employeeId}
                className={selectClass}
                value={draft.employeeId ?? ""}
                onChange={(event) => set({ employeeId: event.target.value ? Number(event.target.value) : null })}
              >
                <option value="">Sin indicar</option>
                {suggested.length > 0 && (
                  <optgroup label="Responsables del día">
                    {suggested.map((person) => (
                      <option key={person.employee_id} value={person.employee_id}>
                        {person.full_name}
                      </option>
                    ))}
                  </optgroup>
                )}
                {others.length > 0 && (
                  <optgroup label="Otros empleados">
                    {others.map((person) => (
                      <option key={person.id} value={person.id}>
                        {person.full_name}
                      </option>
                    ))}
                  </optgroup>
                )}
              </select>
              <p className="text-xs text-muted-foreground">
                Solo registra quién llevó el efectivo: no reparte diferencias.
              </p>
            </div>
            <TextField
              label="Referencia (opcional)"
              value={draft.reference}
              onChange={(reference) => set({ reference })}
              maxLength={100}
            />
          </fieldset>
          <FormError message={problem} />
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" onClick={onClose}>
              Cancelar
            </Button>
            <Button type="submit" disabled={loading}>
              {loading ? "Revisando…" : "Revisar entrega"}
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}

/** REVERTIR ENTREGA (admin): the remittance stays, struck through, with the reason; Cajas gets its reversal. */
export function ReverseRemittanceDialog({
  settlement,
  remittance,
  onClose,
}: {
  settlement: Settlement;
  remittance: Remittance;
  onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  const action = useDeliveryAction(`remittance-reversal:${remittance.id}`, onClose);
  return (
    <ActionDialog
      title="Revertir entrega"
      description={`La entrega de ${formatMoney(remittance.amount)} a ${remittance.destination_account_name} no se borra: queda tachada, con el motivo, y en Cajas se registra su reverso (${remittance.destination_account_name} ${formatMoney(`-${remittance.amount}`)}). La caja tiene que tener ese efectivo.`}
      submitLabel="Revertir"
      action={action}
      onClose={onClose}
      validate={() => (!reason.trim() ? "Indicá el motivo." : null)}
      build={(requestId) => ({
        method: "POST",
        path: settlementPath(settlement, `/remittances/${remittance.id}/reverse`),
        body: { client_request_id: requestId, reason: reason.trim() },
        summary: `Reverso de la entrega de ${formatMoney(remittance.amount)}`,
      })}
    >
      <TextField label="Motivo" value={reason} onChange={setReason} multiline />
    </ActionDialog>
  );
}
