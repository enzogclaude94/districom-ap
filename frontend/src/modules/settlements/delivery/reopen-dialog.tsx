"use client";

import { useState } from "react";

import { settlementPath, type Settlement } from "./api";
import { ActionDialog, TextField } from "./form";
import { settlementTitle } from "./labels";
import { useDeliveryAction } from "./use-delivery-action";

/**
 * Reabrir para corregir (admin, with reason). Reopening does not touch Cajas: the close stays in effect
 * until the corrected version is closed, when it is reversed and replaced in one step.
 */
export function ReopenDialog({ settlement, onClose }: { settlement: Settlement; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const action = useDeliveryAction(`reopen:${settlement.id}:v${settlement.version}`, onClose);
  return (
    <ActionDialog
      title="Reabrir para corregir"
      description={
        settlement.close_in_effect?.kind === "ADMINISTRATIVE_ZERO"
          ? "El cierre en cero sigue vigente hasta que confirmes el nuevo cierre. Reabrir no mueve dinero."
          : "El cierre actual sigue vigente en Cajas hasta que confirmes el nuevo cierre. Reabrir no mueve dinero."
      }
      submitLabel="Reabrir"
      action={action}
      onClose={onClose}
      validate={() => (!reason.trim() ? "Indicá el motivo de la reapertura." : null)}
      build={() => ({
        method: "POST",
        path: settlementPath(settlement, "/reopen"),
        body: { version: settlement.version, reason: reason.trim() },
        summary: `Reapertura de la ${settlementTitle(settlement.sequence_number).toLowerCase()}`,
      })}
    >
      <TextField label="Motivo" value={reason} onChange={setReason} multiline />
    </ActionDialog>
  );
}
