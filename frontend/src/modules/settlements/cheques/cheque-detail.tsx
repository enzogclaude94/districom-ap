"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle2, HandCoins } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";

import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { thirdPartyHref } from "@/lib/api/third-parties";
import { formatDate, formatDateTime, formatMoney, todayIso } from "@/lib/format";
import { paymentHref } from "@/modules/settlements/payments/api";
import { pendingHref } from "@/modules/settlements/pending/labels";

import {
  chequeApi,
  chequeDetailKey,
  deliverHref,
  documentHref,
  type ChequeDetail as Detail,
  type ChequeDocument,
  type CycleDocumentType,
} from "./api";
import { ExpiryMark } from "./cheque-list";
import {
  CreditDialog,
  DepositDialog,
  NewPendingLink,
  RejectionDialog,
  ReturnDialog,
  ReverseDocumentDialog,
} from "./cycle-dialogs";
import {
  DOCUMENT_LABELS,
  EVENT_LABELS,
  KIND_LABELS,
  ORIGIN_LABELS,
  PAYMENT_TYPE_LABELS,
  REVERSED_LABELS,
  STATE_TONES,
  depositBlocker,
} from "./labels";

type Open =
  | { kind: "deposit" | "credit" | "reject" | "return" }
  | { kind: "reverse"; document: ChequeDocument & { type: CycleDocumentType } }
  | null;

type Notice = { text: string; href?: string; pendingId?: number } | null;

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="font-medium">{children}</dd>
    </div>
  );
}

const operationHref = (id: number) => `/rendiciones/cajas/operaciones/${id}`;

function Origin({ cheque }: { cheque: Detail }) {
  if (cheque.origin_type === "SETTLEMENT" && cheque.settlement_id) {
    return (
      <Link href={`/rendiciones/repartos/${cheque.settlement_id}`} className="text-primary hover:underline">
        Rendición de reparto n.º {cheque.settlement_id}
      </Link>
    );
  }
  if (cheque.origin_type === "PENDING_RECEIPT" && cheque.origin_pending_id) {
    return (
      <Link href={pendingHref(cheque.origin_pending_id)} className="text-primary hover:underline">
        Cobro del pendiente n.º {cheque.origin_pending_id}
      </Link>
    );
  }
  return cheque.entry_operation_id ? (
    <Link href={operationHref(cheque.entry_operation_id)} className="text-primary hover:underline">
      {ORIGIN_LABELS[cheque.origin_type]}
    </Link>
  ) : (
    <>{ORIGIN_LABELS[cheque.origin_type]}</>
  );
}

const isCycle = (doc: ChequeDocument): doc is ChequeDocument & { type: CycleDocumentType } =>
  doc.type !== "PENDING_RECEIPT" && doc.type !== "PAYMENT";

/** Who has a DELIVERED cheque, by which payment and since when (R6-2). */
export function Delivery({ cheque }: { cheque: Detail }) {
  if (cheque.state !== "DELIVERED" || cheque.delivery_payment_id === null) return null;
  return (
    <div
      className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-border px-3 py-2 text-sm"
      data-testid="cheque-delivery"
    >
      <span>
        Entregado a{" "}
        {cheque.delivery_third_party_id !== null ? (
          <Link
            href={thirdPartyHref(cheque.delivery_third_party_id)}
            className="font-medium text-primary hover:underline"
          >
            {cheque.delivery_third_party_name}
          </Link>
        ) : (
          <span className="font-medium">{cheque.delivery_third_party_name}</span>
        )}{" "}
        en el pago n.º {cheque.delivery_payment_id}
        {cheque.delivery_business_date && ` del ${formatDate(cheque.delivery_business_date)}`}.
      </span>
      <Link href={paymentHref(cheque.delivery_payment_id)} className="text-primary hover:underline">
        Ver pago
      </Link>
      <span className="basis-full text-xs text-muted-foreground">
        No se deposita, no se devuelve ni se vuelve a entregar. Vuelve a Cartera solo si se revierte el pago con el
        cheque recuperado.
      </span>
    </div>
  );
}

/** `/rendiciones/cheques/[id]`: the instrument, where it came from, where it is, and everything that happened. */
export function ChequeDetailView({ chequeId }: { chequeId: number }) {
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const role = moduleRole(me.data, "settlements");
  const canOperate = roleAtLeast(role, "operator");
  const isAdmin = roleAtLeast(role, "admin");
  const query = useQuery({ queryKey: chequeDetailKey(chequeId), queryFn: () => chequeApi.get(chequeId) });
  const router = useRouter();
  const [dialog, setDialog] = useState<Open>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const close = () => setDialog(null);

  if (query.isPending) {
    return (
      <div className="space-y-3" aria-label="Cargando el cheque">
        <Skeleton className="h-10 w-1/2" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  const cheque = query.data;
  const blocked = depositBlocker(cheque, todayIso());
  const rejectable = cheque.state === "DEPOSITED" || cheque.state === "CREDITED";

  return (
    <>
      <Link href="/rendiciones/cheques" className="flex items-center gap-1 text-sm text-primary hover:underline">
        <ArrowLeft className="size-4" aria-hidden />
        Cartera de cheques
      </Link>
      <PageHeader
        title={`Cheque n.º ${cheque.cheque_number}`}
        description={`${KIND_LABELS[cheque.kind]} · ${PAYMENT_TYPE_LABELS[cheque.payment_type]} · ${cheque.bank_name}`}
      />

      {notice && (
        <div
          role="status"
          className="flex flex-wrap items-center gap-2 rounded-md border border-primary/30 bg-primary-soft px-4 py-3 text-sm"
        >
          <CheckCircle2 className="size-4 text-primary" aria-hidden />
          <span className="font-medium">{notice.text}</span>
          {notice.pendingId !== undefined && <NewPendingLink pendingId={notice.pendingId} />}
          {notice.href && (
            <Link href={notice.href} className="text-primary hover:underline">
              Ver documento
            </Link>
          )}
          <button
            type="button"
            className="ml-auto text-muted-foreground hover:text-foreground"
            onClick={() => setNotice(null)}
          >
            Cerrar
          </button>
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2">
            Estado
            <Badge tone={STATE_TONES[cheque.state]} data-testid="cheque-state">
              {cheque.state_label}
            </Badge>
            <ExpiryMark cheque={cheque} />
          </CardTitle>
          <CardDescription>{cheque.location_label ?? "Ya no está en Cartera ni depositado."}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <dl className="grid gap-3 text-sm sm:grid-cols-3">
            <Fact label="Importe">
              <span className="text-lg tabular-nums">{formatMoney(cheque.amount)}</span>
            </Fact>
            <Fact label="Pagadero desde">{formatDate(cheque.payable_from)}</Fact>
            <Fact label="Presentar hasta">{formatDate(cheque.presentation_deadline)}</Fact>
          </dl>
          {cheque.state === "IN_PORTFOLIO" && cheque.payable_from > todayIso() && (
            <p className="text-sm text-muted-foreground">
              Disponible para depósito desde el {formatDate(cheque.payable_from)}.
            </p>
          )}
          <Delivery cheque={cheque} />
          {cheque.state === "DECLARED" && (
            <p className="rounded-md border border-warning/40 bg-warning-soft px-3 py-2 text-sm">
              ECHEQ pendiente de verificación: todavía no justifica nada ni está en cartera.
            </p>
          )}
          {canOperate && (
            <div className="flex flex-wrap gap-2 border-t border-border pt-3">
              {cheque.state === "IN_PORTFOLIO" && (
                <>
                  <Button size="sm" disabled={blocked !== null} onClick={() => setDialog({ kind: "deposit" })}>
                    Depositar
                  </Button>
                  {blocked && <span className="self-center text-xs text-muted-foreground">{blocked}</span>}
                  {/* An expired cheque is never delivered (the payment would be refused): no «Entregar». */}
                  {!cheque.presentation_expired && (
                    <Button variant="secondary" size="sm" onClick={() => router.push(deliverHref(cheque.id))}>
                      <HandCoins aria-hidden />
                      Entregar
                    </Button>
                  )}
                  {isAdmin && (
                    <Button variant="secondary" size="sm" onClick={() => setDialog({ kind: "return" })}>
                      Devolver cheque
                    </Button>
                  )}
                </>
              )}
              {cheque.state === "DEPOSITED" && (
                <Button size="sm" onClick={() => setDialog({ kind: "credit" })}>
                  Registrar acreditación
                </Button>
              )}
              {rejectable && (
                <Button variant="secondary" size="sm" onClick={() => setDialog({ kind: "reject" })}>
                  Registrar rechazo
                </Button>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Instrumento</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-2 gap-3 text-sm">
              <Fact label="Tipo">{KIND_LABELS[cheque.kind]}</Fact>
              <Fact label="Tipo de pago">{PAYMENT_TYPE_LABELS[cheque.payment_type]}</Fact>
              <Fact label="Banco">
                {cheque.bank_name} ({cheque.bank_bcra_code})
              </Fact>
              {cheque.kind === "PHYSICAL" && <Fact label="Sucursal">{cheque.branch_code ?? "—"}</Fact>}
              <Fact label="Cuenta / CBU">{cheque.account_ref ?? "—"}</Fact>
              <Fact label="Número">{cheque.cheque_number}</Fact>
              {cheque.echeq_external_id && <Fact label="Identificador ECHEQ">{cheque.echeq_external_id}</Fact>}
              <Fact label="Emisión">{formatDate(cheque.issue_date)}</Fact>
              {cheque.payment_date && <Fact label="Fecha de pago">{formatDate(cheque.payment_date)}</Fact>}
              <Fact label="Vencimiento">{formatDate(cheque.presentation_deadline)}</Fact>
              <Fact label="Titular / librador">{cheque.drawer_name}</Fact>
              <Fact label="CUIT">{cheque.drawer_tax_id ?? "—"}</Fact>
              <Fact label="Quién lo entregó">{cheque.delivered_by_name}</Fact>
              {cheque.notes && <Fact label="Notas">{cheque.notes}</Fact>}
            </dl>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Origen</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-2 gap-3 text-sm">
              <Fact label="Origen">
                <Origin cheque={cheque} />
              </Fact>
              <Fact label="Recibido">{formatDate(cheque.received_business_date)}</Fact>
              {cheque.received_by_name && <Fact label="Recibió">{cheque.received_by_name}</Fact>}
              {cheque.physical_receipt_confirmed_at && (
                <Fact label="En mano">
                  {cheque.physical_receipt_confirmed_by_name}, {formatDateTime(cheque.physical_receipt_confirmed_at)}
                </Fact>
              )}
              {cheque.verification_status && cheque.verification_changed_at && (
                <Fact label="Verificación">
                  {cheque.verification_changed_by_name}, {formatDateTime(cheque.verification_changed_at)}
                  {cheque.verification_reason && <span className="block text-xs">{cheque.verification_reason}</span>}
                </Fact>
              )}
              {cheque.voided && (
                <Fact label="Anulado">
                  {cheque.voided_by_name} · {cheque.void_reason}
                </Fact>
              )}
            </dl>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Documentos</CardTitle>
          <CardDescription>
            Lo que movió el cheque, en orden. Un documento se corrige con su reverso, que se registra en su fecha
            original; nada se borra.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {cheque.documents.length === 0 ? (
            <p className="py-3 text-center text-sm text-muted-foreground">
              Todavía no se depositó, no se cobró ni se entregó.
            </p>
          ) : (
            <ul className="divide-y divide-border text-sm">
              {cheque.documents.map((doc) => (
                <li
                  key={`${doc.type}-${doc.id}`}
                  className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2"
                  data-testid={`document-${doc.type}-${doc.id}`}
                >
                  <span className="font-medium">{DOCUMENT_LABELS[doc.type]}</span>
                  <span className="text-muted-foreground tabular-nums">{formatDate(doc.business_date)}</span>
                  {doc.reversed && <Badge tone="warning">Revertido</Badge>}
                  {doc.type === "PAYMENT" && doc.third_party_name && (
                    <span className="text-muted-foreground">a {doc.third_party_name}</span>
                  )}
                  {isCycle(doc) && (
                    <Link href={documentHref(doc.type, doc.id)} className="text-primary hover:underline">
                      Ver documento
                    </Link>
                  )}
                  {doc.type === "PAYMENT" && (
                    <Link href={paymentHref(doc.id)} className="text-primary hover:underline">
                      Ver pago
                    </Link>
                  )}
                  <Link href={operationHref(doc.operation_id)} className="text-primary hover:underline">
                    Ver en Cajas
                  </Link>
                  {doc.pending_id !== null && (
                    <Link href={pendingHref(doc.pending_id)} className="text-primary hover:underline">
                      {doc.type === "PENDING_RECEIPT" ? "Ver pendiente cobrado" : "Ver pendiente creado"}
                    </Link>
                  )}
                  {isAdmin && isCycle(doc) && !doc.reversed && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="ml-auto"
                      onClick={() => setDialog({ kind: "reverse", document: doc })}
                    >
                      Revertir
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Historia</CardTitle>
        </CardHeader>
        <CardContent>
          <ol className="space-y-2 text-sm">
            {cheque.events.map((event) => (
              <li key={event.id} className="border-l-2 border-border pl-3">
                <p className="font-medium">{EVENT_LABELS[event.event_type] ?? event.event_type}</p>
                <p className="text-xs text-muted-foreground">
                  {event.created_by_name} · {formatDateTime(event.created_at)}
                  {event.settlement_version !== null && ` · versión ${event.settlement_version} de la rendición`}
                </p>
                {event.reason && <p className="text-xs">Motivo: {event.reason}</p>}
                {event.payment_id !== null && (
                  <Link href={paymentHref(event.payment_id)} className="text-xs text-primary hover:underline">
                    Pago n.º {event.payment_id}
                  </Link>
                )}
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>

      {dialog?.kind === "deposit" && (
        <DepositDialog
          cheques={[cheque]}
          onClose={close}
          onDone={(d) =>
            setNotice({ text: `Depositado en ${d.bank_account_name}.`, href: documentHref("DEPOSIT", d.id) })
          }
        />
      )}
      {dialog?.kind === "credit" && (
        <CreditDialog
          cheques={[cheque]}
          onClose={close}
          onDone={(c) =>
            setNotice({ text: `Acreditado en ${c.bank_account_name}.`, href: documentHref("CREDIT", c.id) })
          }
        />
      )}
      {dialog?.kind === "reject" && (
        <RejectionDialog
          cheque={cheque}
          onClose={close}
          onDone={(r) => {
            close();
            setNotice({ text: `Rechazo registrado por ${formatMoney(cheque.amount)}.`, pendingId: r.pending_id });
          }}
        />
      )}
      {dialog?.kind === "return" && (
        <ReturnDialog
          cheque={cheque}
          onClose={close}
          onDone={(r) => {
            close();
            setNotice({ text: `Cheque devuelto a ${cheque.delivered_by_name}.`, pendingId: r.pending_id });
          }}
        />
      )}
      {dialog?.kind === "reverse" && (
        <ReverseDocumentDialog
          type={dialog.document.type}
          id={dialog.document.id}
          businessDate={dialog.document.business_date}
          what={`${DOCUMENT_LABELS[dialog.document.type].toLowerCase()} del ${formatDate(dialog.document.business_date)} del cheque n.º ${cheque.cheque_number}`}
          onClose={close}
          onDone={() => setNotice({ text: `${REVERSED_LABELS[dialog.document.type]}.` })}
        />
      )}
    </>
  );
}
