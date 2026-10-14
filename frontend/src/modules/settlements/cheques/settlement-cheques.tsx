"use client";

import Link from "next/link";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ApiError } from "@/lib/api/client";
import { cn } from "@/lib/cn";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";
import { settlementPath, type Settlement } from "@/modules/settlements/delivery/api";
import { ActionDialog, TextField } from "@/modules/settlements/delivery/form";
import { VoidDialog } from "@/modules/settlements/delivery/load-dialogs";
import { useDeliveryAction } from "@/modules/settlements/delivery/use-delivery-action";

import { chequeHref, type Cheque } from "./api";
import { chequeErrorView } from "./errors";
import {
  InstrumentFields,
  draftBody,
  draftOf,
  draftProblem,
  emptyDraft,
  type InstrumentDraft,
} from "./instrument-fields";
import { KIND_LABELS, PAYMENT_TYPE_LABELS, STATE_TONES } from "./labels";

type Open =
  | { kind: "new" }
  | { kind: "edit"; cheque: Cheque }
  | { kind: "void"; cheque: Cheque }
  | { kind: "decide"; cheque: Cheque; to: "VERIFIED" | "VERIFICATION_REJECTED"; correcting: boolean }
  | null;

const errorView = (error: unknown) => chequeErrorView(error);

/** What the cheque means for the settlement: it justifies, it blocks the close, or nothing. */
function Effect({ cheque }: { cheque: Cheque }) {
  if (cheque.voided) return null;
  if (cheque.kind === "ECHEQ" && cheque.verification_status === "DECLARED") {
    return (
      <span className="block text-xs text-warning" data-testid={`echeq-blocks-${cheque.id}`}>
        Pendiente de verificación: bloquea el cierre
        {!cheque.identity_complete && (
          <span className="block text-muted-foreground">
            Para verificarlo falta el identificador ECHEQ, o el CBU y el CUIT del librador.
          </span>
        )}
      </span>
    );
  }
  if (cheque.kind === "ECHEQ" && cheque.verification_status === "VERIFICATION_REJECTED") {
    return <span className="block text-xs text-muted-foreground">No justifica la rendición</span>;
  }
  return <span className="block text-xs text-success">Justifica la rendición</span>;
}

/** `Cheques` section of `/rendiciones/repartos/[id]`: the cheques received in this delivery. */
export function SettlementCheques({
  settlement,
  editable,
  isAdmin,
}: {
  settlement: Settlement;
  editable: boolean;
  isAdmin: boolean;
}) {
  const [dialog, setDialog] = useState<Open>(null);
  const close = () => setDialog(null);
  const cheques = settlement.cheques;

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-2">
        <div className="space-y-1">
          <CardTitle>Cheques</CardTitle>
          <CardDescription>
            Cheques físicos en mano y ECHEQ recibidos en este reparto. Justifican la rendición y, al cerrarla, entran a
            la Cartera de cheques. Un ECHEQ declarado bloquea el cierre hasta verificarlo o rechazar su verificación.
          </CardDescription>
        </div>
        {editable && (
          <Button variant="secondary" size="sm" onClick={() => setDialog({ kind: "new" })}>
            Cargar cheque
          </Button>
        )}
      </CardHeader>
      <CardContent>
        {cheques.length === 0 ? (
          <p className="py-3 text-center text-sm text-muted-foreground">Sin cheques.</p>
        ) : (
          <Table stacked aria-label="Cheques de la rendición">
            <TableHeader>
              <TableRow>
                <TableHead>Cheque</TableHead>
                <TableHead className="text-right">Importe</TableHead>
                <TableHead>Librador · Entregó</TableHead>
                <TableHead>Estado</TableHead>
                <TableHead>
                  <span className="sr-only">Acciones</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {cheques.map((cheque) => {
                const declared = cheque.kind === "ECHEQ" && cheque.verification_status === "DECLARED";
                const decided = cheque.kind === "ECHEQ" && !declared && cheque.verification_status !== null;
                return (
                  <TableRow
                    key={cheque.id}
                    data-testid={`cheque-row-${cheque.id}`}
                    className={cn(cheque.voided && "text-muted-foreground")}
                  >
                    <TableCell data-label="Cheque">
                      <span>
                        <span className="font-medium">
                          {KIND_LABELS[cheque.kind]} · {PAYMENT_TYPE_LABELS[cheque.payment_type]}
                        </span>
                        <span className="block text-xs text-muted-foreground">
                          N.º {cheque.cheque_number} · {cheque.bank_name}
                        </span>
                        {cheque.load_change_detail && (
                          <span className="mt-1 block rounded-md border border-warning/40 bg-warning-soft px-2 py-1 text-xs">
                            {cheque.load_change_detail}
                          </span>
                        )}
                      </span>
                    </TableCell>
                    <TableCell
                      data-label="Importe"
                      className={cn("text-right whitespace-nowrap tabular-nums", cheque.voided && "line-through")}
                    >
                      {formatMoney(cheque.amount)}
                    </TableCell>
                    <TableCell data-label="Librador">
                      <span>
                        {cheque.drawer_name}
                        <span className="block text-xs text-muted-foreground">Entregó: {cheque.delivered_by_name}</span>
                      </span>
                    </TableCell>
                    <TableCell data-label="Estado">
                      <span>
                        <Badge tone={STATE_TONES[cheque.state]}>{cheque.state_label}</Badge>
                        <Effect cheque={cheque} />
                        {cheque.kind === "PHYSICAL" && cheque.physical_receipt_confirmed_at && (
                          <span className="block text-xs text-muted-foreground">
                            En mano: {cheque.physical_receipt_confirmed_by_name},{" "}
                            {formatDateTime(cheque.physical_receipt_confirmed_at)}
                          </span>
                        )}
                        {cheque.verification_reason && (
                          <span className="block text-xs text-muted-foreground">
                            Motivo: {cheque.verification_reason}
                          </span>
                        )}
                        {cheque.voided && cheque.void_reason && (
                          <span className="block text-xs">Anulado: {cheque.void_reason}</span>
                        )}
                      </span>
                    </TableCell>
                    <TableCell className="md:whitespace-nowrap">
                      {editable && declared && !cheque.voided && (
                        <>
                          <Button
                            size="sm"
                            onClick={() => setDialog({ kind: "decide", cheque, to: "VERIFIED", correcting: false })}
                          >
                            Verificar
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() =>
                              setDialog({ kind: "decide", cheque, to: "VERIFICATION_REJECTED", correcting: false })
                            }
                          >
                            Rechazar verificación
                          </Button>
                        </>
                      )}
                      {editable && isAdmin && decided && !cheque.published && !cheque.voided && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() =>
                            setDialog({
                              kind: "decide",
                              cheque,
                              to: cheque.verification_status === "VERIFIED" ? "VERIFICATION_REJECTED" : "VERIFIED",
                              correcting: true,
                            })
                          }
                        >
                          Corregir decisión
                        </Button>
                      )}
                      {editable && !cheque.voided && !cheque.published && (
                        <Button variant="ghost" size="sm" onClick={() => setDialog({ kind: "edit", cheque })}>
                          Corregir
                        </Button>
                      )}
                      {editable && !cheque.voided && (
                        <Button variant="ghost" size="sm" onClick={() => setDialog({ kind: "void", cheque })}>
                          Anular
                        </Button>
                      )}
                      <Link
                        href={chequeHref(cheque.id)}
                        className="px-2 text-sm text-primary hover:underline"
                        aria-label={`Ver el cheque n.º ${cheque.cheque_number}`}
                      >
                        Ver
                      </Link>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>

      {(dialog?.kind === "new" || dialog?.kind === "edit") && (
        <ChequeLoadDialog
          settlement={settlement}
          cheque={dialog.kind === "edit" ? dialog.cheque : null}
          onClose={close}
        />
      )}
      {dialog?.kind === "void" && (
        <VoidDialog
          settlement={settlement}
          title="Anular cheque"
          what={`El cheque n.º ${dialog.cheque.cheque_number} de ${formatMoney(dialog.cheque.amount)}`}
          path={`/cheques/${dialog.cheque.id}/void`}
          onClose={close}
          note={
            dialog.cheque.in_effect
              ? "Ya está en Cartera de cheques: sigue ahí hasta confirmar el nuevo cierre."
              : undefined
          }
        />
      )}
      {dialog?.kind === "decide" && (
        <DecisionDialog
          settlement={settlement}
          cheque={dialog.cheque}
          to={dialog.to}
          correcting={dialog.correcting}
          onClose={close}
        />
      )}
    </Card>
  );
}

/** Body of a correction (`ChequePatch`): the kind and the physical confirmation never change. */
function patchBody(draft: InstrumentDraft) {
  const body = draftBody(draft);
  delete body.kind;
  delete body.physical_receipt_confirmed;
  return body;
}

/** Load a cheque into an open settlement, or correct one that had no financial effect yet. */
function ChequeLoadDialog({
  settlement,
  cheque,
  onClose,
}: {
  settlement: Settlement;
  cheque: Cheque | null;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState<InstrumentDraft>(() => (cheque ? draftOf(cheque) : emptyDraft()));
  const action = useDeliveryAction(
    cheque ? `cheque-edit:${settlement.id}:${cheque.id}` : `cheque:${settlement.id}`,
    onClose,
  );
  const error = action.mutation.error;
  const possibleDuplicate = error instanceof ApiError && error.code === "cheque_possible_duplicate";
  return (
    <ActionDialog
      title={cheque ? "Corregir cheque" : "Cargar cheque"}
      description={
        cheque
          ? "Solo se corrige un cheque que todavía no tuvo efecto financiero."
          : `Recibido en el reparto del ${formatDate(settlement.business_date)} de ${settlement.employee_name}.`
      }
      submitLabel={cheque ? "Guardar" : "Cargar cheque"}
      action={action}
      onClose={onClose}
      errorView={errorView}
      where="la rendición"
      validate={() => draftProblem(cheque ? { ...draft, receivedInHand: true } : draft)}
      build={(requestId) =>
        cheque
          ? {
              method: "PATCH",
              path: settlementPath(settlement, `/cheques/${cheque.id}`),
              body: patchBody(draft),
              summary: `Corrección del cheque n.º ${draft.number.trim()}`,
            }
          : {
              method: "POST",
              path: settlementPath(settlement, "/cheques"),
              body: { client_request_id: requestId, ...draftBody(draft) },
              summary: `${KIND_LABELS[draft.kind]} n.º ${draft.number.trim()} de ${draft.deliveredBy.trim()}`,
            }
      }
    >
      <InstrumentFields
        draft={draft}
        onChange={setDraft}
        possibleDuplicate={possibleDuplicate}
        lockKind={cheque !== null}
      />
    </ActionDialog>
  );
}

/** Verify an ECHEQ or reject its verification (with a reason); an admin corrects a decision with a reason. */
function DecisionDialog({
  settlement,
  cheque,
  to,
  correcting,
  onClose,
}: {
  settlement: Settlement;
  cheque: Cheque;
  to: "VERIFIED" | "VERIFICATION_REJECTED";
  correcting: boolean;
  onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  const action = useDeliveryAction(`echeq:${settlement.id}:${cheque.id}`, onClose);
  const verify = to === "VERIFIED";
  const needsReason = correcting || !verify;
  return (
    <ActionDialog
      title={correcting ? "Corregir la decisión del ECHEQ" : verify ? "Verificar ECHEQ" : "Rechazar verificación"}
      description={
        verify
          ? "Confirmá que el ECHEQ está acreditado a nombre de la empresa en el banco: pasa a justificar la rendición."
          : "El ECHEQ no se pudo validar. No justifica la rendición y queda en la historia con el motivo."
      }
      submitLabel={verify ? "Verificar" : "Rechazar verificación"}
      action={action}
      onClose={onClose}
      errorView={errorView}
      where="la rendición"
      validate={() => (needsReason && !reason.trim() ? "Indicá el motivo." : null)}
      build={() => ({
        method: "POST",
        path: settlementPath(settlement, `/cheques/${cheque.id}/verification`),
        body: { status: to, reason: reason.trim() || null },
        summary: `${verify ? "Verificación" : "Rechazo de verificación"} del ECHEQ n.º ${cheque.cheque_number}`,
      })}
    >
      <p className="text-sm">
        ECHEQ n.º {cheque.cheque_number} · {cheque.bank_name} · <strong>{formatMoney(cheque.amount)}</strong>
      </p>
      <TextField label={needsReason ? "Motivo" : "Motivo (opcional)"} value={reason} onChange={setReason} multiline />
    </ActionDialog>
  );
}
