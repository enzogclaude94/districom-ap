"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Info } from "lucide-react";
import { useId, useState, type FormEvent, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api/client";
import { formatDate, formatMoney, todayIso } from "@/lib/format";
import { parseAmount } from "@/modules/settlements/cash/amount";
import { cashApi, cashQueryKey } from "@/modules/settlements/cash/api";
import { cashErrorView } from "@/modules/settlements/cash/errors";
import { AmountField, PendingAttempt, SelectField, TextField } from "@/modules/settlements/delivery/form";
import type { DeliveryErrorView } from "@/modules/settlements/delivery/errors";
import { deliveryErrorView } from "@/modules/settlements/delivery/errors";
import { useWriteAction } from "@/modules/settlements/delivery/use-delivery-action";

import {
  MOVEMENT_PATHS,
  employeeFinanceApi,
  employeeFinanceQueryKey,
  type Direction,
  type EmployeeMovement,
  type MovementKind,
  type MovementLeg,
  type MovementPreview,
} from "./api";
import { ADJUSTMENT_NOTICE, READING_LABELS, adjustmentEffect, movementTitle } from "./labels";

/** What the user reads for each error of a movement: the backend writes business errors for the user. */
export function movementErrorView(
  error: unknown,
  accountName?: (id: number) => string | undefined,
  businessDate?: string,
): DeliveryErrorView {
  if (error instanceof ApiError) {
    if (error.code === "insufficient_funds")
      return { message: cashErrorView(error, accountName, businessDate).message };
    if (error.code === "idempotency_conflict") {
      return {
        message:
          "Este movimiento ya se había registrado con otros datos. Revisá la cuenta interna; si querés cargar uno distinto, empezá de nuevo.",
        conflict: true,
      };
    }
  }
  return deliveryErrorView(error, accountName);
}

function Problem({ children }: { children: ReactNode }) {
  return (
    <div
      role="alert"
      className="flex gap-2 rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger"
    >
      <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
      <p>{children}</p>
    </div>
  );
}

/** The ledger legs of a movement, each account with its signed amount. */
export function MovementLegs({ legs, testId }: { legs: MovementLeg[]; testId?: string }) {
  return (
    <dl className="divide-y divide-border rounded-md border border-border text-sm" data-testid={testId}>
      {legs.map((leg) => (
        <div
          key={`${leg.account_kind}-${leg.account_id ?? "new"}`}
          className="flex flex-wrap justify-between gap-x-4 gap-y-0.5 px-3 py-2"
        >
          <dt className="min-w-0 break-words text-muted-foreground">{leg.account_name}</dt>
          <dd className="font-medium whitespace-nowrap tabular-nums">{formatMoney(leg.amount, { signed: true })}</dd>
        </div>
      ))}
    </dl>
  );
}

const reading = (balance: string) => {
  const value = Number(balance);
  return READING_LABELS[value > 0 ? "OWES_COMPANY" : value < 0 ? "COMPANY_OWES" : "NONE"];
};

/** The confirmation: in words first, then the balance before and after, then the legs of the ledger. */
function Confirmation({ kind, preview }: { kind: MovementKind; preview: MovementPreview }) {
  const effect =
    kind === "ADJUSTMENT" && preview.direction
      ? adjustmentEffect(preview.direction, preview.amount)
      : kind === "ADVANCE"
        ? `Se entregan ${formatMoney(preview.amount)} al empleado: deberá ${formatMoney(preview.amount)} más a la empresa.`
        : `El empleado deberá ${formatMoney(preview.amount)} más a la empresa.`;
  return (
    <div className="space-y-3" data-testid="movement-confirmation">
      <p className="font-medium">{effect}</p>
      <dl className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
        <div className="rounded-md border border-border px-3 py-2">
          <dt className="text-muted-foreground">Saldo antes</dt>
          <dd className="font-medium tabular-nums">
            {formatMoney(preview.balance_before)} · {reading(preview.balance_before)}
          </dd>
        </div>
        <div className="rounded-md border border-border px-3 py-2">
          <dt className="text-muted-foreground">Saldo después</dt>
          <dd className="font-medium tabular-nums" data-testid="balance-after">
            {formatMoney(preview.balance_after)} · {reading(preview.balance_after)}
          </dd>
        </div>
      </dl>
      <MovementLegs legs={preview.legs} testId="movement-legs" />
    </div>
  );
}

const TITLES: Record<MovementKind, string> = {
  CONSUMPTION: "Registrar consumo",
  ADVANCE: "Entregar adelanto",
  ADJUSTMENT: "Registrar ajuste",
};

const INTROS: Record<MovementKind, string> = {
  CONSUMPTION: "Algo que el empleado consumió de la empresa. Se registra solo el importe: no mueve stock.",
  ADVANCE: "Dinero entregado al empleado desde una caja o el banco, a cuenta de lo que se le pague.",
  ADJUSTMENT: ADJUSTMENT_NOTICE,
};

/**
 * Registers a consumption, an advance or an adjustment on the internal account (R8-2, Enzo's point 11). Two
 * steps, as every write that moves money: the form, then a confirmation computed by the backend (the effect in
 * words, the balance before and after, the legs), so nothing is registered from one click. The request id is
 * kept for every retry of the action and an attempt with an unknown outcome is offered again (ADR 008).
 */
export function MovementDialog({
  kind,
  employeeId,
  employeeName,
  onClose,
}: {
  kind: MovementKind;
  employeeId: number;
  employeeName: string;
  onClose: () => void;
}) {
  const dateId = useId();
  const accounts = useQuery({
    queryKey: [...cashQueryKey, "accounts"],
    queryFn: cashApi.accounts,
    enabled: kind === "ADVANCE",
  });
  const funding = (accounts.data ?? []).filter(
    (account) => account.active && (account.kind === "CASH_BOX" || account.kind === "BANK"),
  );
  const [businessDate, setBusinessDate] = useState(todayIso);
  const [amount, setAmount] = useState("");
  const [direction, setDirection] = useState<Direction | null>(null);
  const [fundingId, setFundingId] = useState<number | null>(null);
  const [description, setDescription] = useState("");
  const [reference, setReference] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  const action = useWriteAction<EmployeeMovement>({
    slot: `employee-movement:${kind}:${employeeId}`,
    write: (request) => employeeFinanceApi.write(request.path, request.body),
    invalidate: [employeeFinanceQueryKey, cashQueryKey],
    isUncertain: (error) => Boolean(movementErrorView(error).uncertain),
    onDone: onClose,
  });
  const accountName = (id: number) => (accounts.data ?? []).find((a) => a.id === id)?.name;
  const parsed = parseAmount(amount);
  const previewBody = {
    kind,
    business_date: businessDate,
    amount: parsed ?? "0",
    ...(kind === "ADJUSTMENT" && direction ? { direction } : {}),
    ...(kind === "ADVANCE" && fundingId !== null ? { funding_account_id: fundingId } : {}),
  };
  const preview = useQuery({
    queryKey: [...employeeFinanceQueryKey, "preview", employeeId, previewBody],
    queryFn: () => employeeFinanceApi.previewMovement(employeeId, previewBody),
    enabled: confirming,
    retry: false,
    staleTime: 0,
  });
  const pending = action.mutation.isPending;
  const error = action.mutation.error ? movementErrorView(action.mutation.error, accountName, businessDate) : null;
  const previewError = preview.error ? movementErrorView(preview.error, accountName, businessDate) : null;

  const validate = () =>
    (!businessDate ? "Indicá la fecha." : null) ??
    (businessDate > todayIso() ? "La fecha no puede ser futura." : null) ??
    (!parsed ? "Ingresá un importe mayor a cero, con hasta dos decimales." : null) ??
    (kind === "ADVANCE" && fundingId === null ? "Elegí la caja o el banco desde el que se entrega." : null) ??
    (kind === "ADJUSTMENT" && direction === null ? "Elegí si el ajuste es en contra o a favor del empleado." : null) ??
    (!description.trim() ? (kind === "ADJUSTMENT" ? "Indicá el motivo del ajuste." : "Indicá una descripción.") : null);

  const review = (event: FormEvent) => {
    event.preventDefault();
    const local = validate();
    setProblem(local);
    if (!local) {
      action.mutation.reset();
      setConfirming(true);
    }
  };

  const confirm = () =>
    action.submit((requestId) => ({
      method: "POST",
      path: `/employees/${employeeId}/${MOVEMENT_PATHS[kind]}`,
      body: {
        client_request_id: requestId,
        business_date: businessDate,
        amount: parsed,
        description: description.trim(),
        reference: reference.trim() || null,
        ...(kind === "ADVANCE" ? { funding_account_id: fundingId } : {}),
        ...(kind === "ADJUSTMENT" ? { direction } : {}),
      },
      summary: `${movementTitle(kind, direction)} de ${formatMoney(parsed ?? "0")} a ${employeeName}`,
    }));

  const title = TITLES[kind];
  const description_ = `${employeeName}. ${INTROS[kind]}`;

  if (action.stored) {
    return (
      <Dialog title={title} description={description_} onClose={onClose}>
        <PendingAttempt
          action={action}
          onClose={onClose}
          errorView={(e) => movementErrorView(e, accountName)}
          where="la cuenta interna del empleado"
        />
      </Dialog>
    );
  }

  if (confirming) {
    return (
      <Dialog
        title={`Confirmá: ${movementTitle(kind, direction).toLowerCase()}`}
        description={`${employeeName} · ${formatDate(businessDate)} · ${description.trim()}`}
        onClose={onClose}
      >
        <div className="space-y-4">
          {preview.isPending ? (
            <p className="text-sm text-muted-foreground">Calculando el movimiento…</p>
          ) : preview.data ? (
            <Confirmation kind={kind} preview={preview.data} />
          ) : null}
          {(previewError ?? error) && <Problem>{(previewError ?? error)!.message}</Problem>}
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" disabled={pending} onClick={() => setConfirming(false)}>
              Volver a editar
            </Button>
            {error?.conflict ? (
              <Button variant="secondary" onClick={action.abandonStored}>
                Empezar de nuevo
              </Button>
            ) : (
              <Button disabled={pending || !preview.data} onClick={confirm}>
                {pending ? "Registrando…" : "Confirmar y registrar"}
              </Button>
            )}
          </div>
        </div>
      </Dialog>
    );
  }

  return (
    <Dialog title={title} description={description_} onClose={onClose}>
      <form className="space-y-4" onSubmit={review} noValidate>
        {kind === "ADJUSTMENT" && (
          <div className="flex gap-2 rounded-md border border-border bg-muted/40 px-3 py-2 text-sm">
            <Info className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
            <p>Usalo solo para corregir: cada movimiento con nombre propio se registra con su acción.</p>
          </div>
        )}
        <div className="space-y-1.5">
          <Label htmlFor={dateId}>Fecha</Label>
          <Input
            id={dateId}
            type="date"
            max={todayIso()}
            className="w-44 tabular-nums"
            value={businessDate}
            onChange={(event) => setBusinessDate(event.target.value)}
          />
        </div>
        {kind === "ADJUSTMENT" && (
          <fieldset className="space-y-1.5">
            <legend className="text-sm font-medium">Sentido</legend>
            {(["CHARGE", "CREDIT"] as const).map((value) => (
              <label key={value} className="flex items-start gap-2 text-sm">
                <input
                  type="radio"
                  name="direction"
                  className="mt-1"
                  checked={direction === value}
                  onChange={() => setDirection(value)}
                />
                <span>
                  <span className="font-medium">
                    {value === "CHARGE" ? "En contra del empleado" : "A favor del empleado"}
                  </span>
                  <span className="block text-muted-foreground">
                    {value === "CHARGE"
                      ? "El empleado pasa a deber más a la empresa."
                      : "La empresa le reconoce un importe a favor."}
                  </span>
                </span>
              </label>
            ))}
          </fieldset>
        )}
        <AmountField value={amount} onChange={setAmount} />
        {kind === "ADJUSTMENT" && direction && parsed && (
          <p className="text-sm font-medium" data-testid="adjustment-effect">
            {adjustmentEffect(direction, parsed)}
          </p>
        )}
        {kind === "ADVANCE" && (
          <SelectField
            label="Se entrega desde"
            value={fundingId}
            onChange={setFundingId}
            placeholder={accounts.isPending ? "Cargando cuentas…" : "Elegí la caja o el banco…"}
            options={funding.map((a) => ({ id: a.id, name: `${a.name} · saldo ${formatMoney(a.balance)}` }))}
          />
        )}
        <TextField
          label={kind === "ADJUSTMENT" ? "Motivo" : "Descripción"}
          value={description}
          onChange={setDescription}
          multiline
          hint={kind === "CONSUMPTION" ? "Qué consumió, por ejemplo «2 packs de gaseosa»." : undefined}
        />
        <TextField label="Referencia (opcional)" value={reference} onChange={setReference} maxLength={100} />
        {problem && <Problem>{problem}</Problem>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button type="submit">Revisar</Button>
        </div>
      </form>
    </Dialog>
  );
}

/** Reverses the whole movement on its original date, once, with a reason (area admin); nothing is deleted. */
export function ReverseMovementDialog({ movement, onClose }: { movement: EmployeeMovement; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const action = useWriteAction<EmployeeMovement>({
    slot: `employee-movement-reverse:${movement.id}`,
    write: (request) => employeeFinanceApi.write(request.path, request.body),
    invalidate: [employeeFinanceQueryKey, cashQueryKey],
    isUncertain: (error) => Boolean(movementErrorView(error).uncertain),
    onDone: onClose,
  });
  const error = action.mutation.error ? movementErrorView(action.mutation.error) : null;
  const pending = action.mutation.isPending;
  const title = `Revertir ${movementTitle(movement.kind, movement.direction).toLowerCase()} n.º ${movement.id}`;
  const intro = `Se registra el reverso exacto con fecha ${formatDate(movement.business_date)}. El movimiento original queda visible como revertido.`;
  const reversed = movement.legs.map((leg) => ({
    ...leg,
    amount: leg.amount.startsWith("-") ? leg.amount.slice(1) : `-${leg.amount}`,
  }));
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const local = reason.trim() ? null : "Indicá el motivo del reverso.";
    setProblem(local);
    if (local) return;
    action.submit((requestId) => ({
      method: "POST",
      path: `/employee-movements/${movement.id}/reverse`,
      body: { client_request_id: requestId, reason: reason.trim() },
      summary: `Reverso del movimiento n.º ${movement.id}`,
    }));
  };
  if (action.stored) {
    return (
      <Dialog title={title} description={intro} onClose={onClose}>
        <PendingAttempt
          action={action}
          onClose={onClose}
          errorView={(e) => movementErrorView(e)}
          where="el movimiento"
        />
      </Dialog>
    );
  }
  return (
    <Dialog title={title} description={intro} onClose={onClose}>
      <form className="space-y-4" onSubmit={submit} noValidate>
        <MovementLegs legs={reversed} testId="reversal-legs" />
        <TextField label="Motivo del reverso" value={reason} onChange={setReason} multiline />
        {(problem ?? error) && <Problem>{problem ?? error!.message}</Problem>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button type="submit" variant="danger" disabled={pending}>
            {pending ? "Revirtiendo…" : "Confirmar reverso"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
