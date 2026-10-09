"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";
import { pendingHref } from "@/modules/settlements/pending/labels";

import {
  chequeApi,
  chequeHref,
  chequeQueryKey,
  type CycleDocumentType,
  type CycleItem,
  type DocumentReversal,
} from "./api";
import { ReverseDocumentDialog } from "./cycle-dialogs";
import { DOCUMENT_LABELS, STATE_TONES } from "./labels";

/** One document of the cycle, whatever its type, as this screen shows it. */
interface View {
  businessDate: string;
  operationId: number;
  createdBy: string;
  createdAt: string;
  reversal: DocumentReversal | null;
  facts: [string, string][];
  items: CycleItem[];
  chequeId: number | null;
  pendingId: number | null;
}

async function load(type: CycleDocumentType, id: number): Promise<View> {
  const base = (d: {
    business_date: string;
    operation_id: number;
    created_by_name: string;
    created_at: string;
    reversal: DocumentReversal | null;
  }) => ({
    businessDate: d.business_date,
    operationId: d.operation_id,
    createdBy: d.created_by_name,
    createdAt: d.created_at,
    reversal: d.reversal,
  });
  switch (type) {
    case "DEPOSIT": {
      const d = await chequeApi.deposit(id);
      return {
        ...base(d),
        facts: [
          ["Banco", d.bank_account_name],
          ["Número de boleta", d.slip_number ?? "—"],
          ["Total", formatMoney(d.total)],
          ...(d.notes ? ([["Notas", d.notes]] as [string, string][]) : []),
        ],
        items: d.items,
        chequeId: null,
        pendingId: null,
      };
    }
    case "CREDIT": {
      const d = await chequeApi.credit(id);
      return {
        ...base(d),
        facts: [
          ["Banco", d.bank_account_name],
          ["Total", formatMoney(d.total)],
          ...(d.notes ? ([["Notas", d.notes]] as [string, string][]) : []),
        ],
        items: d.items,
        chequeId: null,
        pendingId: null,
      };
    }
    case "REJECTION": {
      const d = await chequeApi.rejection(id);
      return {
        ...base(d),
        facts: [
          ["Etapa", d.stage === "AFTER_CREDIT" ? "Después de acreditado" : "En tránsito"],
          ["Motivo bancario", d.reason],
        ],
        items: [],
        chequeId: d.cheque_id,
        pendingId: d.pending_id,
      };
    }
    case "RETURN": {
      const d = await chequeApi.return(id);
      return { ...base(d), facts: [["Motivo", d.reason]], items: [], chequeId: d.cheque_id, pendingId: d.pending_id };
    }
  }
}

/** `/rendiciones/cheques/documentos/[tipo]/[id]`: a deposit slip, a crediting, a rejection or a return. */
export function DocumentDetailView({ type, id }: { type: CycleDocumentType; id: number }) {
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const isAdmin = roleAtLeast(moduleRole(me.data, "settlements"), "admin");
  const query = useQuery({ queryKey: [...chequeQueryKey, "document", type, id], queryFn: () => load(type, id) });
  const [reversing, setReversing] = useState(false);

  if (query.isPending) return <Skeleton className="h-40 w-full" aria-label="Cargando el documento" />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  const doc = query.data;
  const title = `${DOCUMENT_LABELS[type]} n.º ${id}`;

  return (
    <>
      <Link href="/rendiciones/cheques" className="flex items-center gap-1 text-sm text-primary hover:underline">
        <ArrowLeft className="size-4" aria-hidden />
        Cartera de cheques
      </Link>
      <PageHeader
        title={title}
        description={`Fecha financiera ${formatDate(doc.businessDate)} · registrado por ${doc.createdBy} el ${formatDateTime(doc.createdAt)}`}
        actions={
          isAdmin &&
          !doc.reversal && (
            <Button variant="secondary" onClick={() => setReversing(true)}>
              Revertir
            </Button>
          )
        }
      />
      {doc.reversal && (
        <div
          className="rounded-md border border-warning/40 bg-warning-soft px-4 py-3 text-sm"
          data-testid="document-reversed"
        >
          <p className="font-medium">Revertido</p>
          <p>
            {doc.reversal.reversed_by_name} · {formatDateTime(doc.reversal.reversed_at)} · Motivo: {doc.reversal.reason}
          </p>
          <Link
            href={`/rendiciones/cajas/operaciones/${doc.reversal.operation_id}`}
            className="text-primary hover:underline"
          >
            Ver el reverso en Cajas
          </Link>
        </div>
      )}
      <Card>
        <CardHeader>
          <CardTitle>Datos</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <dl className="grid gap-3 sm:grid-cols-3">
            {doc.facts.map(([label, value]) => (
              <div key={label}>
                <dt className="text-xs text-muted-foreground">{label}</dt>
                <dd className="font-medium">{value}</dd>
              </div>
            ))}
          </dl>
          <div className="flex flex-wrap gap-3">
            <Link href={`/rendiciones/cajas/operaciones/${doc.operationId}`} className="text-primary hover:underline">
              Ver en Cajas
            </Link>
            {doc.chequeId !== null && (
              <Link href={chequeHref(doc.chequeId)} className="text-primary hover:underline">
                Ver cheque
              </Link>
            )}
            {doc.pendingId !== null && (
              <Link href={pendingHref(doc.pendingId)} className="text-primary hover:underline">
                Ver pendiente creado
              </Link>
            )}
          </div>
        </CardContent>
      </Card>
      {doc.items.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Cheques</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Cheque</TableHead>
                  <TableHead>Entregó</TableHead>
                  <TableHead>Estado actual</TableHead>
                  <TableHead className="text-right">Importe</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {doc.items.map((item) => (
                  <TableRow key={item.cheque_id}>
                    <TableCell>
                      <Link href={chequeHref(item.cheque_id)} className="text-primary hover:underline">
                        N.º {item.cheque_number}
                      </Link>
                      <span className="block text-xs text-muted-foreground">{item.bank_name}</span>
                    </TableCell>
                    <TableCell>{item.delivered_by_name}</TableCell>
                    <TableCell>
                      <Badge tone={STATE_TONES[item.state]}>{item.state_label}</Badge>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{formatMoney(item.amount)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
      {reversing && (
        <ReverseDocumentDialog
          type={type}
          id={id}
          businessDate={doc.businessDate}
          what={`${DOCUMENT_LABELS[type].toLowerCase()} n.º ${id} del ${formatDate(doc.businessDate)}`}
          onClose={() => setReversing(false)}
        />
      )}
    </>
  );
}
