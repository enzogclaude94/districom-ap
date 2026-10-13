"use client";

import { useState } from "react";

import { formatMoney } from "@/lib/format";
import type { Account } from "@/modules/settlements/cash/api";
import { parseAmount } from "@/modules/settlements/cash/amount";
import { parseExpected } from "@/modules/settlements/outlet-settlements/amount";

import {
  settlementPath,
  FUEL_CODE,
  type Expense,
  type ExpenseCategory,
  type Settlement,
  type Transfer,
  type TransferStatus,
} from "./api";
import { ActionDialog, AmountField, SelectField, TextField } from "./form";
import { formatQuantity, parseDecimal } from "./fuel";
import { FuelFields, fuelBody, fuelForm, fuelProblem, type FuelForm } from "./fuel-fields";
import { TRANSFER_STATUS_LABELS } from "./labels";
import { useDeliveryAction } from "./use-delivery-action";

interface Base {
  settlement: Settlement;
  onClose: () => void;
}

const named = (accounts: Account[], id: number | null) => accounts.find((account) => account.id === id)?.name ?? "";

export function AdditionDialog({ settlement, boxes, onClose }: Base & { boxes: Account[] }) {
  const [amount, setAmount] = useState("");
  const [destination, setDestination] = useState<number | null>(boxes[0]?.id ?? null);
  const [description, setDescription] = useState("");
  const action = useDeliveryAction(`addition:${settlement.id}`, onClose);
  return (
    <ActionDialog
      title="Agregar efectivo"
      description="Efectivo que se suma al conteo, por ejemplo monedas o un pago que llegó después."
      submitLabel="Agregar"
      action={action}
      onClose={onClose}
      validate={() =>
        !parseAmount(amount) ? "Ingresá el importe." : destination === null ? "Elegí la caja de destino." : null
      }
      build={(requestId) => ({
        method: "POST",
        path: settlementPath(settlement, "/additions"),
        body: {
          client_request_id: requestId,
          amount: parseAmount(amount),
          destination_account_id: destination,
          description: description.trim() || null,
        },
        summary: `Agregado de ${formatMoney(parseAmount(amount)!)} a ${named(boxes, destination)}`,
      })}
    >
      <AmountField value={amount} onChange={setAmount} />
      <SelectField
        label="Caja de destino"
        value={destination}
        onChange={setDestination}
        placeholder="Elegí una caja…"
        options={boxes}
      />
      <TextField label="Descripción (opcional)" value={description} onChange={setDescription} multiline />
    </ActionDialog>
  );
}

/** Void an addition or an expense: it stays in the history, marked as voided, with the reason. */
export function VoidDialog({
  settlement,
  title,
  what,
  path,
  onClose,
  note,
}: Base & { title: string; what: string; path: string; note?: string }) {
  const [reason, setReason] = useState("");
  const action = useDeliveryAction(`void:${settlement.id}:${path}`, onClose);
  return (
    <ActionDialog
      title={title}
      description={`${what} no se borra: queda en el historial como anulado, con el motivo.${note ? ` ${note}` : ""}`}
      submitLabel="Anular"
      action={action}
      onClose={onClose}
      validate={() => (!reason.trim() ? "Indicá el motivo." : null)}
      build={() => ({
        method: "POST",
        path: settlementPath(settlement, path),
        body: { reason: reason.trim() },
        summary: `${title}: ${what.toLowerCase()}`,
      })}
    >
      <TextField label="Motivo" value={reason} onChange={setReason} multiline />
    </ActionDialog>
  );
}

export function TransferDialog({ settlement, banks, onClose }: Base & { banks: Account[] }) {
  const [amount, setAmount] = useState("");
  const [payer, setPayer] = useState("");
  const [reference, setReference] = useState("");
  const [bank, setBank] = useState<number | null>(banks[0]?.id ?? null);
  const action = useDeliveryAction(`transfer:${settlement.id}`, onClose);
  return (
    <ActionDialog
      title="Declarar transferencia"
      description="Queda declarada hasta que se verifique en el banco. Mientras esté declarada, la rendición no se puede cerrar."
      submitLabel="Declarar"
      action={action}
      onClose={onClose}
      validate={() => (!parseAmount(amount) ? "Ingresá el importe." : bank === null ? "Elegí la cuenta." : null)}
      build={(requestId) => ({
        method: "POST",
        path: settlementPath(settlement, "/transfers"),
        body: {
          client_request_id: requestId,
          amount: parseAmount(amount),
          bank_account_id: bank,
          payer_name: payer.trim() || null,
          reference: reference.trim() || null,
        },
        summary: `Transferencia de ${formatMoney(parseAmount(amount)!)}${payer.trim() ? ` de ${payer.trim()}` : ""}`,
      })}
    >
      <AmountField value={amount} onChange={setAmount} />
      <TextField label="Quién pagó (opcional)" value={payer} onChange={setPayer} maxLength={200} />
      <TextField label="Referencia (opcional)" value={reference} onChange={setReference} maxLength={100} />
      <SelectField label="Cuenta de destino" value={bank} onChange={setBank} placeholder="Elegí…" options={banks} />
    </ActionDialog>
  );
}

const DECISIONS: Record<TransferStatus, { title: string; submit: string }> = {
  VERIFIED: { title: "Verificar transferencia", submit: "Verificar" },
  REJECTED: { title: "Rechazar transferencia", submit: "Rechazar" },
  DECLARED: { title: "Volver a declarada", submit: "Volver a declarada" },
};

/**
 * Verify or reject a declared transfer (operator), or correct a decision already taken (admin, with
 * reason). Only verified transfers justify the settlement.
 */
export function TransferDecisionDialog({
  settlement,
  transfer,
  to,
  onClose,
}: Base & { transfer: Transfer; to: TransferStatus | "CORRECT" }) {
  const correcting = to === "CORRECT";
  const options = (["DECLARED", "VERIFIED", "REJECTED"] as TransferStatus[]).filter((s) => s !== transfer.status);
  const [status, setStatus] = useState<TransferStatus>(correcting ? options[0] : to);
  const [reason, setReason] = useState("");
  const needsReason = correcting || status === "REJECTED";
  const action = useDeliveryAction(`transfer-status:${settlement.id}:${transfer.id}`, onClose);
  const what = `${formatMoney(transfer.amount)}${transfer.payer_name ? ` de ${transfer.payer_name}` : ""}`;
  return (
    <ActionDialog
      title={correcting ? "Corregir la decisión" : DECISIONS[status].title}
      description={
        correcting
          ? `La transferencia de ${what} está ${TRANSFER_STATUS_LABELS[transfer.status].toLowerCase()}. La corrección queda registrada con tu usuario y el motivo.`
          : `Transferencia de ${what}${transfer.reference ? `, referencia ${transfer.reference}` : ""}.`
      }
      submitLabel={correcting ? "Corregir" : DECISIONS[status].submit}
      action={action}
      onClose={onClose}
      validate={() => (needsReason && !reason.trim() ? "Indicá el motivo." : null)}
      build={() => ({
        method: "POST",
        path: settlementPath(settlement, `/transfers/${transfer.id}/status`),
        body: { status, reason: reason.trim() || null },
        summary: `${TRANSFER_STATUS_LABELS[status]}: transferencia de ${what}`,
      })}
    >
      {correcting && (
        <div className="space-y-1.5">
          <p className="text-sm font-medium">Nuevo estado</p>
          <div className="flex flex-wrap gap-3">
            {options.map((option) => (
              <label key={option} className="flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="transfer-status"
                  checked={status === option}
                  onChange={() => setStatus(option)}
                />
                {TRANSFER_STATUS_LABELS[option]}
              </label>
            ))}
          </div>
        </div>
      )}
      {needsReason && <TextField label="Motivo" value={reason} onChange={setReason} multiline />}
    </ActionDialog>
  );
}

export function ExpenseDialog({ settlement, categories, onClose }: Base & { categories: ExpenseCategory[] }) {
  // A boca has no fuel (R7-2): its expenses never offer the FUEL category.
  const active = categories.filter(
    (category) => category.active && !(settlement.type === "OUTLET" && category.code === FUEL_CODE),
  );
  const [category, setCategory] = useState<number | null>(null);
  const [amount, setAmount] = useState("");
  const [description, setDescription] = useState("");
  const [load, setLoad] = useState<FuelForm>(() => fuelForm(settlement.employee_id));
  const action = useDeliveryAction(`expense:${settlement.id}`, onClose);
  const isFuel = active.find((c) => c.id === category)?.code === FUEL_CODE;
  const name = active.find((c) => c.id === category)?.name ?? "";
  return (
    <ActionDialog
      title="Registrar gasto"
      description="Un gasto pagado con dinero de la rendición la justifica por su importe."
      submitLabel="Registrar gasto"
      action={action}
      onClose={onClose}
      validate={() =>
        category === null
          ? "Elegí la categoría."
          : isFuel
            ? fuelProblem(load)
            : !parseAmount(amount)
              ? "Ingresá el importe."
              : null
      }
      build={(requestId) => ({
        method: "POST",
        path: settlementPath(settlement, "/expenses"),
        body: {
          client_request_id: requestId,
          category_id: category,
          ...(isFuel ? { fuel: fuelBody(load) } : { amount: parseAmount(amount) }),
          description: description.trim() || null,
        },
        summary: isFuel
          ? `Combustible: ${formatQuantity(parseDecimal(load.liters, 3)!)} l (${name})`
          : `Gasto de ${formatMoney(parseAmount(amount)!)} (${name})`,
      })}
    >
      <SelectField
        label="Categoría"
        value={category}
        onChange={setCategory}
        placeholder="Elegí una categoría…"
        options={active}
      />
      {isFuel ? <FuelFields form={load} onChange={setLoad} /> : <AmountField value={amount} onChange={setAmount} />}
      <TextField label="Descripción (opcional)" value={description} onChange={setDescription} multiline />
    </ActionDialog>
  );
}

/**
 * Correct the load of a fuel expense while the settlement is open; the amount follows. A fuel expense
 * loaded before R5-2 without detail gets it here, which ends its legacy exception.
 */
export function FuelCorrectionDialog({ settlement, expense, onClose }: Base & { expense: Expense }) {
  const [load, setLoad] = useState<FuelForm>(() => fuelForm(settlement.employee_id, expense.fuel));
  const action = useDeliveryAction(`fuel:${settlement.id}:${expense.id}`, onClose);
  const legacy = expense.fuel === null;
  return (
    <ActionDialog
      title={legacy ? "Cargar detalle de combustible" : "Corregir carga de combustible"}
      description={
        legacy
          ? "Este gasto se registró antes del detalle de combustible. Para cambiar su importe cargá vehículo, litros y precio por litro: el importe pasa a calcularse."
          : "El importe del gasto se vuelve a calcular con los litros y el precio por litro."
      }
      submitLabel="Guardar"
      action={action}
      onClose={onClose}
      validate={() =>
        fuelProblem(load) ?? (Object.keys(fuelBody(load, expense.fuel)).length ? null : "No cambiaste nada.")
      }
      build={() => ({
        method: "PATCH",
        path: settlementPath(settlement, `/expenses/${expense.id}`),
        body: { fuel: fuelBody(load, expense.fuel) },
        summary: `Carga de combustible: ${formatQuantity(parseDecimal(load.liters, 3)!)} l`,
      })}
    >
      <FuelFields form={load} onChange={setLoad} current={expense.fuel} />
    </ActionDialog>
  );
}

/**
 * Correct the expected amount, reference or notes of an open settlement. Driver (or boca), date and number
 * never change. A boca's expected amount may be 0 or left empty until Store Sys gives it (R7-2).
 */
export function EditSettlementDialog({ settlement, onClose }: Base) {
  const outlet = settlement.type === "OUTLET";
  const [expected, setExpected] = useState((settlement.expected_amount ?? "").replace(".", ","));
  const [reference, setReference] = useState(settlement.external_reference ?? "");
  const [notes, setNotes] = useState(settlement.notes ?? "");
  const action = useDeliveryAction(`edit:${settlement.id}`, onClose);
  return (
    <ActionDialog
      title="Corregir datos"
      description={
        outlet ? "La boca, la fecha y el número no cambian." : "El repartidor, la fecha y el número no cambian."
      }
      submitLabel="Guardar"
      action={action}
      onClose={onClose}
      validate={() =>
        outlet
          ? expected.trim() && parseExpected(expected) === null
            ? "Ingresá un importe válido o dejalo vacío."
            : null
          : !parseAmount(expected)
            ? "Ingresá el importe esperado."
            : null
      }
      build={() => ({
        method: "PATCH",
        path: settlementPath(settlement),
        body: {
          expected_amount: outlet ? parseExpected(expected) : parseAmount(expected),
          external_reference: reference.trim() || null,
          notes: notes.trim() || null,
        },
        summary:
          outlet && parseExpected(expected) === null
            ? "Esperado sin cargar"
            : `Esperado ${formatMoney((outlet ? parseExpected(expected) : parseAmount(expected))!)}`,
      })}
    >
      <AmountField
        label={outlet ? "Importe esperado de Store Sys (vacío si todavía no está)" : "Importe esperado"}
        value={expected}
        onChange={setExpected}
      />
      <TextField label="Referencia (opcional)" value={reference} onChange={setReference} maxLength={100} />
      <TextField label="Notas (opcional)" value={notes} onChange={setNotes} multiline />
    </ActionDialog>
  );
}
