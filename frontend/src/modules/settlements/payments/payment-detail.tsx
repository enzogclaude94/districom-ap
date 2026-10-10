"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useState, type ReactNode } from "react";

import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { thirdPartyHref } from "@/lib/api/third-parties";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";
import type { Operation } from "@/modules/settlements/cash/api";
import { ActionDialog, TextField } from "@/modules/settlements/delivery/form";

import { paymentApi, paymentDetailKey, paymentPath, type PaymentDetail, type PaymentEvent } from "./api";
import { usePaymentAction } from "./payment-dialog";
import { paymentErrorView } from "./errors";
import { CONCEPT_LABELS, eventLabel, METHOD_LABELS, STATE_LABELS, STATE_TONES } from "./labels";

const operationHref = (id: number) => `/rendiciones/cajas/operaciones/${id}`;

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0 space-y-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm break-words">{children}</dd>
    </div>
  );
}

function Legs({ operation, title }: { operation: Operation; title: string }) {
  return (
    <div className="space-y-1.5 text-sm">
      <p className="flex flex-wrap items-center gap-2 font-medium">
        {title}
        <Link href={operationHref(operation.id)} className="font-normal text-primary hover:underline">
          Operación n.º {operation.id}
        </Link>
        <span className="font-normal text-muted-foreground">
          {formatDate(operation.business_date)} · registrada {formatDateTime(operation.created_at)}
        </span>
      </p>
      <ul className="space-y-0.5">
        {operation.entries.map((entry) => (
          <li key={entry.account_id} className="flex justify-between gap-4 tabular-nums">
            <span>{entry.account_name}</span>
            <span>{formatMoney(entry.amount, { signed: true })}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ReverseDialog({ payment, onClose }: { payment: PaymentDetail; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const action = usePaymentAction(`payment-reverse:${payment.id}`, onClose);
  const back = payment.items.map((item) => `${formatMoney(item.amount)} a ${item.account_name}`).join(", ");
  return (
    <ActionDialog
      title="Revertir pago"
      description={
        <>
          Se registra el movimiento inverso de todo el pago con su misma fecha ({formatDate(payment.business_date)}):{" "}
          vuelven {back}. El pago no se borra: queda revertido, con el motivo. Para corregir un dato financiero,
          revertilo y registrá uno nuevo.
        </>
      }
      submitLabel="Revertir pago"
      action={action}
      errorView={(e) => paymentErrorView(e)}
      where="el pago"
      onClose={onClose}
      validate={() => (!reason.trim() ? "Indicá el motivo." : null)}
      build={(requestId) => ({
        method: "POST",
        path: paymentPath(payment.id, "/reverse"),
        body: { client_request_id: requestId, reason: reason.trim() },
        summary: `Reverso del pago n.º ${payment.id} por ${formatMoney(payment.amount)}`,
      })}
    >
      <TextField label="Motivo" value={reason} onChange={setReason} multiline />
    </ActionDialog>
  );
}

function CorrectDialog({ payment, onClose }: { payment: PaymentDetail; onClose: () => void }) {
  const [reference, setReference] = useState(payment.reference ?? "");
  const [description, setDescription] = useState(payment.description ?? "");
  const [itemReferences, setItemReferences] = useState<Record<number, string>>(() =>
    Object.fromEntries(payment.items.map((item) => [item.id, item.reference ?? ""])),
  );
  const [reason, setReason] = useState("");
  const action = usePaymentAction(`payment-correct:${payment.id}`, onClose);
  const changes = (): Record<string, unknown> => {
    const change: Record<string, unknown> = {};
    const ref = reference.trim() || null;
    if (ref !== payment.reference) change.reference = ref;
    const text = description.trim() || null;
    if (text !== payment.description) change.description = text;
    const items = payment.items
      .map((item) => ({ id: item.id, reference: (itemReferences[item.id] ?? "").trim() || null, was: item.reference }))
      .filter((item) => item.reference !== item.was)
      .map(({ id, reference: value }) => ({ id, reference: value }));
    if (items.length > 0) change.items = items;
    return change;
  };
  return (
    <ActionDialog
      title="Corregir datos descriptivos"
      description="Solo referencias y descripción. El tercero, la fecha, el concepto, la categoría, los importes y las cuentas no se corrigen: se revierte el pago y se registra uno nuevo."
      submitLabel="Guardar corrección"
      action={action}
      errorView={(e) => paymentErrorView(e)}
      where="el pago"
      onClose={onClose}
      validate={() =>
        (Object.keys(changes()).length === 0 ? "No cambiaste nada." : null) ??
        (payment.concept === "OTHER" && !description.trim() ? "En «Otro pago» la descripción es obligatoria." : null) ??
        (!reason.trim() ? "Indicá el motivo." : null)
      }
      build={() => ({
        method: "POST",
        path: paymentPath(payment.id, "/descriptive"),
        body: { reason: reason.trim(), ...changes() },
        summary: `Corrección del pago n.º ${payment.id}`,
      })}
    >
      <TextField label="Referencia del pago" value={reference} onChange={setReference} maxLength={100} />
      <TextField label="Descripción" value={description} onChange={setDescription} multiline />
      {payment.items.map((item) => (
        <TextField
          key={item.id}
          label={`Referencia de ${METHOD_LABELS[item.method].toLowerCase()} · ${item.account_name}`}
          value={itemReferences[item.id] ?? ""}
          onChange={(value) => setItemReferences({ ...itemReferences, [item.id]: value })}
          maxLength={100}
        />
      ))}
      <TextField label="Motivo de la corrección" value={reason} onChange={setReason} multiline />
    </ActionDialog>
  );
}

const shown = (value: unknown) => (value === null || value === undefined || value === "" ? "—" : String(value));

function Correction({ event }: { event: PaymentEvent }) {
  const before = event.before ?? {};
  const after = event.after ?? {};
  const lines: string[] = [];
  if ("reference" in after) lines.push(`Referencia: ${shown(before.reference)} → ${shown(after.reference)}`);
  if ("description" in after) lines.push(`Descripción: ${shown(before.description)} → ${shown(after.description)}`);
  if (Array.isArray(after.items)) {
    const was = new Map(
      (Array.isArray(before.items) ? (before.items as { id: number; reference: unknown }[]) : []).map((i) => [
        i.id,
        i.reference,
      ]),
    );
    for (const item of after.items as { id: number; reference: unknown }[]) {
      lines.push(`Referencia del medio: ${shown(was.get(item.id))} → ${shown(item.reference)}`);
    }
  }
  if ("reason" in after) lines.push(`Motivo: ${shown(after.reason)}`);
  return (
    <ul className="text-muted-foreground">
      {lines.map((line, index) => (
        <li key={index} className="break-words">
          {line}
        </li>
      ))}
    </ul>
  );
}

/** `/rendiciones/pagos/[id]`: a payment, its means, its movement in the ledger, its reversal and history. */
export function PaymentDetailView({ paymentId }: { paymentId: number }) {
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const isAdmin = roleAtLeast(moduleRole(me.data, "settlements"), "admin");
  const query = useQuery({ queryKey: paymentDetailKey(paymentId), queryFn: () => paymentApi.get(paymentId) });
  const [open, setOpen] = useState<"reverse" | "correct" | null>(null);

  const back = (
    <Link href="/rendiciones/pagos" className="inline-flex items-center gap-1 text-sm text-primary hover:underline">
      <ArrowLeft className="size-4" aria-hidden />
      Pagos
    </Link>
  );
  if (query.isPending) {
    return (
      <>
        {back}
        <Skeleton className="h-40 w-full" aria-label="Cargando pago" />
      </>
    );
  }
  if (query.isError) {
    return (
      <>
        {back}
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      </>
    );
  }
  const payment = query.data;
  const editable = isAdmin && payment.state === "IN_EFFECT";

  return (
    <>
      {back}
      <PageHeader
        title={`Pago n.º ${payment.id}`}
        description={`${CONCEPT_LABELS[payment.concept]} a ${payment.third_party_name} · ${formatMoney(payment.amount)} · ${formatDate(payment.business_date)}`}
        actions={
          editable && (
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" onClick={() => setOpen("correct")}>
                Corregir referencias
              </Button>
              <Button variant="secondary" onClick={() => setOpen("reverse")}>
                Revertir pago
              </Button>
            </div>
          )
        }
      />

      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2">
            {CONCEPT_LABELS[payment.concept]}
            <Badge tone={STATE_TONES[payment.state]} data-testid="payment-state">
              {STATE_LABELS[payment.state]}
            </Badge>
          </CardTitle>
          <CardDescription>Un pago hecho. No genera ni cancela deudas: no hay saldos con terceros.</CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="Tercero">
              <Link href={thirdPartyHref(payment.third_party_id)} className="text-primary hover:underline">
                {payment.third_party_name}
              </Link>
              {payment.third_party_tax_id_display && (
                <span className="block text-muted-foreground tabular-nums">
                  CUIT {payment.third_party_tax_id_display}
                </span>
              )}
              {!payment.third_party_active && <span className="block text-muted-foreground">Desactivado</span>}
            </Field>
            <Field label="Importe">
              <span className="tabular-nums" data-testid="payment-amount">
                {formatMoney(payment.amount)}
              </span>
            </Field>
            <Field label="Fecha del pago">{formatDate(payment.business_date)}</Field>
            {payment.expense_category_name && <Field label="Categoría">{payment.expense_category_name}</Field>}
            <Field label="Contrapartida">{payment.counter_account_name}</Field>
            <Field label="Referencia">{payment.reference ?? "—"}</Field>
            <Field label="Descripción">{payment.description ?? "—"}</Field>
            <Field label="Registrado por">
              {payment.created_by_name} · {formatDateTime(payment.created_at)}
            </Field>
            {payment.reversed_at && (
              <Field label="Revertido por">
                {payment.reversed_by_name} · {formatDateTime(payment.reversed_at)}
                {payment.reversal_reason && (
                  <span className="block text-muted-foreground">Motivo: {payment.reversal_reason}</span>
                )}
              </Field>
            )}
          </dl>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Medios de pago</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="divide-y divide-border text-sm" data-testid="payment-items">
            {payment.items.map((item) => (
              <li key={item.id} className="flex flex-wrap justify-between gap-x-4 gap-y-0.5 py-2">
                <span>
                  {METHOD_LABELS[item.method]} · {item.account_name}
                  {item.reference && <span className="block text-xs text-muted-foreground">Ref. {item.reference}</span>}
                </span>
                <span className="font-medium tabular-nums">{formatMoney(item.amount)}</span>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>

      {(payment.operation || payment.reversal) && (
        <Card>
          <CardHeader>
            <CardTitle>Movimiento en Cajas</CardTitle>
            <CardDescription>
              El pago y su reverso son operaciones del ledger: se corrigen solo desde este pago.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {payment.operation && <Legs operation={payment.operation} title="Pago" />}
            {payment.reversal && <Legs operation={payment.reversal} title="Reverso" />}
          </CardContent>
        </Card>
      )}

      {payment.history.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Historial</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2 text-sm">
              {payment.history.map((event) => (
                <li key={event.id} className="space-y-0.5">
                  <p>
                    <span className="font-medium">{eventLabel(event.event_type)}</span>{" "}
                    <span className="text-muted-foreground">
                      {formatDateTime(event.occurred_at)}
                      {event.user_name && ` · ${event.user_name}`}
                    </span>
                  </p>
                  {event.event_type === "corrected" && <Correction event={event} />}
                  {event.event_type === "reversed" && event.after?.reason != null && (
                    <p className="text-muted-foreground">Motivo: {shown(event.after.reason)}</p>
                  )}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {open === "reverse" && <ReverseDialog payment={payment} onClose={() => setOpen(null)} />}
      {open === "correct" && <CorrectDialog payment={payment} onClose={() => setOpen(null)} />}
    </>
  );
}
