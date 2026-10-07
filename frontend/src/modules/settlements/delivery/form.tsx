"use client";

import { AlertTriangle } from "lucide-react";
import { useId, useState, type FormEvent, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatDateTime, formatMoney } from "@/lib/format";
import { parseAmount } from "@/modules/settlements/cash/amount";

import { deliveryErrorView, type DeliveryErrorView } from "./errors";
import type { DeliveryRequest, WriteAction } from "./use-delivery-action";

/** Reads an API error for the user; each document passes its own (settlement, pending collection). */
export type ErrorView = (error: unknown) => DeliveryErrorView;

export const selectClass =
  "flex h-9 w-full rounded-md border border-border bg-surface px-3 text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:opacity-50";

export const textareaClass =
  "flex min-h-20 w-full rounded-md border border-border bg-surface px-3 py-2 text-sm placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none aria-invalid:border-danger";

export function AmountField({
  value,
  onChange,
  label = "Importe",
}: {
  value: string;
  onChange: (value: string) => void;
  label?: string;
}) {
  const id = useId();
  const parsed = parseAmount(value);
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        inputMode="decimal"
        autoComplete="off"
        placeholder="0,00"
        className="tabular-nums"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-describedby={`${id}-hint`}
      />
      <p id={`${id}-hint`} className="text-xs text-muted-foreground tabular-nums">
        {value && parsed ? `Se registran ${formatMoney(parsed)}` : "Por ejemplo 15.000,50"}
      </p>
    </div>
  );
}

export function SelectField({
  label,
  value,
  onChange,
  placeholder,
  options,
}: {
  label: string;
  value: number | null;
  onChange: (value: number | null) => void;
  placeholder: string;
  options: { id: number; name: string }[];
}) {
  const id = useId();
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <select
        id={id}
        className={selectClass}
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value ? Number(event.target.value) : null)}
      >
        <option value="">{placeholder}</option>
        {options.map((option) => (
          <option key={option.id} value={option.id}>
            {option.name}
          </option>
        ))}
      </select>
    </div>
  );
}

export function TextField({
  label,
  value,
  onChange,
  multiline = false,
  maxLength = 500,
  hint,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  multiline?: boolean;
  maxLength?: number;
  hint?: string;
}) {
  const id = useId();
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {multiline ? (
        <textarea
          id={id}
          className={textareaClass}
          maxLength={maxLength}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      ) : (
        <Input id={id} maxLength={maxLength} value={value} onChange={(event) => onChange(event.target.value)} />
      )}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

export function FormError({ message }: { message: string | null | undefined }) {
  if (!message) return null;
  return (
    <div role="alert" className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">
      {message}
    </div>
  );
}

/**
 * Shown instead of a form when an earlier attempt of the same action has an unknown outcome: it can be
 * retried exactly as it was (never duplicated) or, after a clear warning, abandoned.
 */
export function PendingAttempt({
  action,
  onClose,
  errorView = deliveryErrorView,
  where = "la rendición",
}: {
  action: WriteAction;
  onClose: () => void;
  errorView?: ErrorView;
  /** What to check before abandoning: "la rendición", "el pendiente". */
  where?: string;
}) {
  const [confirmAbandon, setConfirmAbandon] = useState(false);
  const stored = action.stored!;
  const pending = action.mutation.isPending;
  const error = action.mutation.error ? errorView(action.mutation.error) : null;

  return (
    <div className="space-y-4">
      <div className="flex gap-2 rounded-md border border-warning/40 bg-warning-soft px-3 py-2 text-sm">
        <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
        <div className="space-y-1">
          <p className="font-medium">Hay una operación sin confirmar</p>
          <p>
            El {formatDateTime(stored.savedAt)} se intentó registrar <strong>{stored.summary}</strong>, pero no se pudo
            confirmar si quedó registrada. Reintentala: si ya se había registrado, no se duplica.
          </p>
        </div>
      </div>
      <FormError message={error && !error.uncertain ? error.message : null} />
      {confirmAbandon ? (
        <div className="space-y-3 rounded-md border border-danger/30 px-3 py-3 text-sm" role="alertdialog">
          <p className="font-medium">¿Descartar el intento?</p>
          <p>
            Si esa operación sí se registró y después cargás otra igual, quedaría duplicada. Antes de descartar, revisá{" "}
            {where}.
          </p>
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" onClick={() => setConfirmAbandon(false)}>
              Volver
            </Button>
            <Button variant="secondary" onClick={action.abandonStored}>
              Descartar el intento
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cerrar
          </Button>
          <Button variant="secondary" disabled={pending} onClick={() => setConfirmAbandon(true)}>
            Descartar…
          </Button>
          <Button disabled={pending} onClick={action.retryStored}>
            {pending ? "Reintentando…" : "Reintentar la misma operación"}
          </Button>
        </div>
      )}
    </div>
  );
}

/** Shell of every write on a settlement or a pending: idempotent submit, pending attempt and readable errors. */
export function ActionDialog({
  title,
  description,
  submitLabel,
  action,
  onClose,
  validate,
  build,
  children,
  errorView = deliveryErrorView,
  where,
  submitDisabled = false,
}: {
  title: string;
  description?: ReactNode;
  submitLabel: string;
  action: WriteAction;
  errorView?: ErrorView;
  where?: string;
  /** Keeps the confirmation unavailable while the form cannot be sent (parts that do not add up). */
  submitDisabled?: boolean;
  onClose: () => void;
  /** Form-level check before sending; returns the message to show or null. */
  validate: () => string | null;
  build: (requestId: string) => DeliveryRequest;
  children: ReactNode;
}) {
  const [problem, setProblem] = useState<string | null>(null);
  const pending = action.mutation.isPending;
  const error = action.mutation.error ? errorView(action.mutation.error) : null;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const local = validate();
    setProblem(local);
    if (!local) action.submit(build);
  };

  return (
    <Dialog title={title} description={description} onClose={onClose}>
      {action.stored ? (
        <PendingAttempt action={action} onClose={onClose} errorView={errorView} where={where} />
      ) : (
        <form className="space-y-4" onSubmit={submit} noValidate>
          <fieldset disabled={pending} className="space-y-4">
            {children}
          </fieldset>
          <FormError message={problem ?? error?.message} />
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" onClick={onClose}>
              Cancelar
            </Button>
            <Button type="submit" disabled={pending || submitDisabled}>
              {pending ? "Guardando…" : submitLabel}
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}
