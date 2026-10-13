"use client";

import Link from "next/link";
import { useId, useState, type ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/cn";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";
import { parseAmount } from "@/modules/settlements/cash/amount";
import { settlementPath, type Person, type Settlement } from "@/modules/settlements/delivery/api";
import { ActionDialog, AmountField, SelectField, TextField } from "@/modules/settlements/delivery/form";
import { VoidDialog } from "@/modules/settlements/delivery/load-dialogs";
import { useDeliveryAction } from "@/modules/settlements/delivery/use-delivery-action";

import type { Pending } from "./api";
import { LOAD_CHANGE_LABELS, STATE_LABELS, STATE_TONES, pendingHref, pendingTitle } from "./labels";

type Open = { kind: "new" } | { kind: "edit"; pending: Pending } | { kind: "void"; pending: Pending } | null;

/**
 * What a pending means financially next to what the correction proposes. While a settlement is being
 * corrected the close in effect still rules: the load is only the next version, never money already moved.
 */
export function CorrectionNote({ pending }: { pending: Pending }) {
  if (!pending.load_change) return null;
  const inEffect = pending.amount_in_effect;
  let detail: ReactNode;
  if (pending.load_change === "MARKED_FOR_REMOVAL") {
    detail = inEffect && <>Sigue vigente por {formatMoney(inEffect)} hasta confirmar el nuevo cierre.</>;
  } else if (pending.load_change === "MODIFIED") {
    detail = (
      <>
        {inEffect && <span className="block">Vigente: {formatMoney(inEffect)}</span>}
        {inEffect !== pending.amount && (
          <span className="block">Nuevo importe propuesto: {formatMoney(pending.amount)}</span>
        )}
        {pending.customer_in_effect && pending.customer_in_effect !== pending.customer_name && (
          <span className="block">Nuevo cliente propuesto: {pending.customer_name}</span>
        )}
      </>
    );
  } else {
    detail = <>Entra en vigencia con el nuevo cierre por {formatMoney(pending.amount)}.</>;
  }
  return (
    <div
      className="mt-1 rounded-md border border-warning/40 bg-warning-soft px-2 py-1 text-xs"
      data-testid="load-change"
    >
      <p className="font-medium">{LOAD_CHANGE_LABELS[pending.load_change]}</p>
      <div>{detail}</div>
    </div>
  );
}

/** Applied to a pending, for "Parcial": never just the word, always what happened. */
export function AppliedBreakdown({ pending, className }: { pending: Pending; className?: string }) {
  if (pending.balance === null) return null;
  return (
    <p className={cn("text-xs text-muted-foreground tabular-nums", className)}>
      Cobrado: {formatMoney(pending.collected)} · Resuelto: {formatMoney(pending.resolved)} · Saldo:{" "}
      {formatMoney(pending.balance)}
    </p>
  );
}

/** `Pendientes` section of `/rendiciones/repartos/[id]`: load while open, financial state once in Cajas. */
export function SettlementPendings({
  settlement,
  editable,
  employees,
}: {
  settlement: Settlement;
  editable: boolean;
  employees: Person[];
}) {
  const [dialog, setDialog] = useState<Open>(null);
  const close = () => setDialog(null);
  const pendings = settlement.pending_collections;
  const correcting = settlement.financial_state === "BEING_CORRECTED";

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-2">
        <div className="space-y-1">
          <CardTitle>Pendientes</CardTitle>
          <CardDescription>
            Lo que un cliente quedó debiendo. Justifica la rendición y, al cerrarla, pasa a Pendientes de cobro en
            Cajas.
            {correcting && " Durante la corrección, lo vigente sigue en Cajas hasta confirmar el nuevo cierre."}
          </CardDescription>
        </div>
        {editable && (
          <Button variant="secondary" size="sm" onClick={() => setDialog({ kind: "new" })}>
            Agregar pendiente
          </Button>
        )}
      </CardHeader>
      <CardContent>
        {pendings.length === 0 ? (
          <p className="py-3 text-center text-sm text-muted-foreground">Sin pendientes.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Cliente</TableHead>
                <TableHead className="text-right">Importe de carga</TableHead>
                <TableHead className="text-right">Vigente</TableHead>
                <TableHead>Estado</TableHead>
                <TableHead className="text-right">Saldo</TableHead>
                <TableHead>Responsable</TableHead>
                <TableHead>Fecha esperada</TableHead>
                <TableHead>
                  <span className="sr-only">Acciones</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {pendings.map((pending) => (
                <TableRow
                  key={pending.id}
                  data-testid={`pending-row-${pending.id}`}
                  className={cn(pending.state === "VOIDED" && "text-muted-foreground")}
                >
                  <TableCell>
                    {pending.customer_name}
                    <span className="block text-xs text-muted-foreground">{pending.reason}</span>
                    <CorrectionNote pending={pending} />
                  </TableCell>
                  <TableCell
                    className={cn("text-right whitespace-nowrap tabular-nums", pending.voided && "line-through")}
                  >
                    {formatMoney(pending.amount)}
                  </TableCell>
                  <TableCell className="text-right whitespace-nowrap tabular-nums">
                    {pending.amount_in_effect ? formatMoney(pending.amount_in_effect) : "—"}
                  </TableCell>
                  <TableCell>
                    <Badge tone={STATE_TONES[pending.state]}>{STATE_LABELS[pending.state]}</Badge>
                    {pending.state === "PARTIAL" && <AppliedBreakdown pending={pending} />}
                    {pending.state === "VOIDED" && pending.void_reason && (
                      <span className="block text-xs">
                        {pending.voided_by_name} · {pending.voided_at ? formatDateTime(pending.voided_at) : ""}
                        <span className="block">Motivo: {pending.void_reason}</span>
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-right whitespace-nowrap tabular-nums">
                    {pending.balance !== null ? formatMoney(pending.balance) : "—"}
                  </TableCell>
                  <TableCell className="whitespace-nowrap">{pending.responsible_name}</TableCell>
                  <TableCell className="whitespace-nowrap tabular-nums">{formatDate(pending.expected_date)}</TableCell>
                  <TableCell className="whitespace-nowrap">
                    {editable && !pending.voided && (
                      <>
                        <Button variant="ghost" size="sm" onClick={() => setDialog({ kind: "edit", pending })}>
                          Corregir
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => setDialog({ kind: "void", pending })}>
                          Anular
                        </Button>
                      </>
                    )}
                    {pending.amount_in_effect !== null && (
                      <Link
                        href={pendingHref(pending.id)}
                        className="px-2 text-sm text-primary hover:underline"
                        aria-label={`Ver ${pendingTitle(pending.id)} de ${pending.customer_name}`}
                      >
                        Ver
                      </Link>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>

      {(dialog?.kind === "new" || dialog?.kind === "edit") && (
        <PendingDialog
          settlement={settlement}
          pending={dialog.kind === "edit" ? dialog.pending : null}
          employees={employees}
          onClose={close}
        />
      )}
      {dialog?.kind === "void" && (
        <VoidDialog
          settlement={settlement}
          title="Anular pendiente"
          what={`El pendiente de ${formatMoney(dialog.pending.amount)} de ${dialog.pending.customer_name}`}
          path={`/pending-collections/${dialog.pending.id}/void`}
          onClose={close}
          note={
            dialog.pending.amount_in_effect
              ? `Ya está en Cajas: sigue vigente por ${formatMoney(dialog.pending.amount_in_effect)} hasta confirmar el nuevo cierre.`
              : undefined
          }
        />
      )}
    </Card>
  );
}

/** Add a pending to an open settlement, or correct one (in a correction, only the next version changes). */
function PendingDialog({
  settlement,
  pending,
  employees,
  onClose,
}: {
  settlement: Settlement;
  pending: Pending | null;
  employees: Person[];
  onClose: () => void;
}) {
  const dateId = useId();
  const [customer, setCustomer] = useState(pending?.customer_name ?? "");
  const [amount, setAmount] = useState(pending ? pending.amount.replace(".", ",") : "");
  const [reason, setReason] = useState(pending?.reason ?? "");
  // The driver answers for collecting it unless someone else is chosen (R3-D3).
  const [responsible, setResponsible] = useState<number | null>(
    pending?.responsible_employee_id ?? settlement.employee_id ?? null,
  );
  const [expected, setExpected] = useState(pending?.expected_date ?? "");
  const action = useDeliveryAction(
    pending ? `pending-edit:${settlement.id}:${pending.id}` : `pending:${settlement.id}`,
    onClose,
  );
  const driver =
    settlement.employee_id !== undefined
      ? { id: settlement.employee_id, full_name: settlement.employee_name ?? "" }
      : null;
  const options = !driver || employees.some((e) => e.id === driver.id) ? employees : [driver, ...employees];
  const parsed = parseAmount(amount);
  const fields = {
    customer_name: customer.trim(),
    amount: parsed,
    reason: reason.trim(),
    responsible_employee_id: responsible,
    expected_date: expected || null,
  };

  return (
    <ActionDialog
      title={pending ? "Corregir pendiente" : "Agregar pendiente"}
      description={
        pending?.amount_in_effect
          ? `Vigente en Cajas por ${formatMoney(pending.amount_in_effect)}. El cambio se aplica recién con el nuevo cierre.`
          : "Lo que el cliente quedó debiendo de este reparto."
      }
      submitLabel={pending ? "Guardar" : "Agregar pendiente"}
      action={action}
      onClose={onClose}
      validate={() =>
        !customer.trim()
          ? "Indicá el cliente."
          : !parsed
            ? "Ingresá el importe."
            : !reason.trim()
              ? "Indicá el motivo."
              : responsible === null
                ? "Elegí el responsable."
                : expected && expected < settlement.business_date
                  ? "La fecha esperada no puede ser anterior a la fecha de la rendición."
                  : null
      }
      build={(requestId) =>
        pending
          ? {
              method: "PATCH",
              path: settlementPath(settlement, `/pending-collections/${pending.id}`),
              body: fields,
              summary: `Corrección del pendiente de ${customer.trim()} por ${formatMoney(parsed!)}`,
            }
          : {
              method: "POST",
              path: settlementPath(settlement, "/pending-collections"),
              body: { client_request_id: requestId, ...fields },
              summary: `Pendiente de ${customer.trim()} por ${formatMoney(parsed!)}`,
            }
      }
    >
      <TextField label="Cliente" value={customer} onChange={setCustomer} maxLength={200} />
      <AmountField value={amount} onChange={setAmount} />
      <TextField label="Motivo" value={reason} onChange={setReason} multiline />
      <SelectField
        label="Responsable de cobrarlo"
        value={responsible}
        onChange={setResponsible}
        placeholder="Elegí un empleado…"
        options={options.map((e) => ({
          id: e.id,
          name: e.id === settlement.employee_id ? `${e.full_name} (repartidor)` : e.full_name,
        }))}
      />
      <div className="space-y-1.5">
        <Label htmlFor={dateId}>Fecha esperada de cobro (opcional)</Label>
        <Input
          id={dateId}
          type="date"
          min={settlement.business_date}
          value={expected}
          onChange={(event) => setExpected(event.target.value)}
        />
      </div>
    </ActionDialog>
  );
}
