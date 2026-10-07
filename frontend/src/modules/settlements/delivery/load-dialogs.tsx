"use client";

import { useState } from "react";

import { formatMoney } from "@/lib/format";
import type { Account } from "@/modules/settlements/cash/api";
import { parseAmount } from "@/modules/settlements/cash/amount";

import { deliveryPath, type ExpenseCategory, type Settlement, type Transfer, type TransferStatus } from "./api";
import { ActionDialog, AmountField, SelectField, TextField } from "./form";
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
        path: deliveryPath(settlement.id, "/additions"),
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
        path: deliveryPath(settlement.id, path),
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
        path: deliveryPath(settlement.id, "/transfers"),
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
        path: deliveryPath(settlement.id, `/transfers/${transfer.id}/status`),
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
  const active = categories.filter((category) => category.active);
  const [category, setCategory] = useState<number | null>(null);
  const [amount, setAmount] = useState("");
  const [description, setDescription] = useState("");
  const action = useDeliveryAction(`expense:${settlement.id}`, onClose);
  return (
    <ActionDialog
      title="Registrar gasto"
      description="Un gasto pagado con dinero de la rendición la justifica por su importe."
      submitLabel="Registrar gasto"
      action={action}
      onClose={onClose}
      validate={() => (category === null ? "Elegí la categoría." : !parseAmount(amount) ? "Ingresá el importe." : null)}
      build={(requestId) => ({
        method: "POST",
        path: deliveryPath(settlement.id, "/expenses"),
        body: {
          client_request_id: requestId,
          category_id: category,
          amount: parseAmount(amount),
          description: description.trim() || null,
        },
        summary: `Gasto de ${formatMoney(parseAmount(amount)!)} (${active.find((c) => c.id === category)?.name ?? ""})`,
      })}
    >
      <SelectField
        label="Categoría"
        value={category}
        onChange={setCategory}
        placeholder="Elegí una categoría…"
        options={active}
      />
      <AmountField value={amount} onChange={setAmount} />
      <TextField label="Descripción (opcional)" value={description} onChange={setDescription} multiline />
    </ActionDialog>
  );
}

/** Correct the expected amount, reference or notes of an open settlement. Driver, date and number never change. */
export function EditSettlementDialog({ settlement, onClose }: Base) {
  const [expected, setExpected] = useState(settlement.expected_amount.replace(".", ","));
  const [reference, setReference] = useState(settlement.external_reference ?? "");
  const [notes, setNotes] = useState(settlement.notes ?? "");
  const action = useDeliveryAction(`edit:${settlement.id}`, onClose);
  return (
    <ActionDialog
      title="Corregir datos"
      description="El repartidor, la fecha y el número no cambian."
      submitLabel="Guardar"
      action={action}
      onClose={onClose}
      validate={() => (!parseAmount(expected) ? "Ingresá el importe esperado." : null)}
      build={() => ({
        method: "PATCH",
        path: deliveryPath(settlement.id),
        body: {
          expected_amount: parseAmount(expected),
          external_reference: reference.trim() || null,
          notes: notes.trim() || null,
        },
        summary: `Esperado ${formatMoney(parseAmount(expected)!)}`,
      })}
    >
      <AmountField label="Importe esperado" value={expected} onChange={setExpected} />
      <TextField label="Referencia (opcional)" value={reference} onChange={setReference} maxLength={100} />
      <TextField label="Notas (opcional)" value={notes} onChange={setNotes} multiline />
    </ActionDialog>
  );
}
