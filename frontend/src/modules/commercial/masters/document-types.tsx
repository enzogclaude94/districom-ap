"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useState } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { mastersApi, mastersQueryKey, type DocumentType } from "@/lib/api/masters";
import { cn } from "@/lib/cn";

import { DOCUMENT_CLASSES, signLabel } from "./labels";
import { EditDialog, Field, selectClass, useMastersAccess, yesNo } from "./shared";

const typesKey = [...mastersQueryKey, "document-types"] as const;

function ClassifyDialog({ type, onClose }: { type: DocumentType; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [documentClass, setDocumentClass] = useState(type.document_class);
  const [sign, setSign] = useState(String(type.sign));
  const [universe, setUniverse] = useState(type.commercial_universe);
  const unclassified = documentClass === "UNCLASSIFIED";
  const save = useMutation({
    mutationFn: (reason: string) =>
      mastersApi.classifyDocumentType(type.id, {
        document_class: documentClass,
        sign: unclassified ? 0 : Number(sign),
        commercial_universe: unclassified ? false : universe,
        reason,
        expected_updated_at: type.updated_at,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: typesKey });
      onClose();
    },
  });
  return (
    <EditDialog
      title={`Clasificar «${type.source_name}»`}
      description="Es una interpretación contable: decide si sus comprobantes entran en las ventas y con qué signo."
      onClose={onClose}
      onSave={(reason) => save.mutate(reason)}
      pending={save.isPending}
      error={save.error}
    >
      <Field label="Clase">
        {(id) => (
          <select
            id={id}
            className={selectClass}
            value={documentClass}
            onChange={(e) => setDocumentClass(e.target.value)}
          >
            {Object.entries(DOCUMENT_CLASSES).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        )}
      </Field>
      <Field label="Signo">
        {(id) => (
          <select
            id={id}
            className={selectClass}
            value={unclassified ? "0" : sign}
            disabled={unclassified}
            onChange={(e) => setSign(e.target.value)}
          >
            <option value="1">+1 suma</option>
            <option value="-1">−1 resta</option>
            <option value="0">0 no suma</option>
          </select>
        )}
      </Field>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={universe && !unclassified}
          disabled={unclassified}
          onChange={(e) => setUniverse(e.target.checked)}
        />
        Entra en el universo comercial
      </label>
    </EditDialog>
  );
}

/** `/comercial/datos/maestros/comprobantes`: the document types imports found and how each one counts. */
export function DocumentTypesView() {
  const { isAdmin } = useMastersAccess();
  const wanted = Number(useSearchParams().get("tipo")) || null; // from Calidad: the exact type
  const types = useQuery({ queryKey: typesKey, queryFn: mastersApi.documentTypes });
  const [editing, setEditing] = useState<DocumentType | null>(null);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Tipos de comprobante</CardTitle>
        <p className="text-sm text-muted-foreground">
          Los tipos aparecen con las importaciones de comprobantes. Un tipo sin clasificar queda fuera de las ventas
          hasta que un admin lo clasifica.
        </p>
      </CardHeader>
      <CardContent>
        {types.isPending ? (
          <Skeleton className="h-20" />
        ) : types.isError ? (
          <ErrorState error={types.error} onRetry={() => void types.refetch()} />
        ) : types.data.length === 0 ? (
          <EmptyState
            icon={FileText}
            title="Todavía no hay tipos"
            description="Aparecen al importar comprobantes de STORE SYS."
          />
        ) : (
          <Table stacked>
            <TableHeader>
              <TableRow>
                <TableHead>Tipo en STORE SYS</TableHead>
                <TableHead>Clase</TableHead>
                <TableHead>Signo</TableHead>
                <TableHead>Universo comercial</TableHead>
                <TableHead>Estado</TableHead>
                {isAdmin && <TableHead className="sr-only">Acciones</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {types.data.map((t) => (
                <TableRow
                  key={t.id}
                  aria-current={t.id === wanted ? "true" : undefined}
                  className={cn(t.id === wanted && "bg-warning-soft")}
                >
                  <TableCell data-label="Tipo en STORE SYS" className="font-medium">
                    {t.source_name}
                  </TableCell>
                  <TableCell data-label="Clase">
                    {t.document_class === "UNCLASSIFIED" ? (
                      <Badge tone="warning">Sin clasificar</Badge>
                    ) : (
                      DOCUMENT_CLASSES[t.document_class]
                    )}
                  </TableCell>
                  <TableCell data-label="Signo">{signLabel(t.sign)}</TableCell>
                  <TableCell data-label="Universo comercial">{yesNo(t.commercial_universe)}</TableCell>
                  <TableCell data-label="Estado">
                    {t.active ? <Badge tone="success">Activo</Badge> : <Badge>Inactivo</Badge>}
                  </TableCell>
                  {isAdmin && (
                    <TableCell data-label="Acciones">
                      <Button variant="ghost" size="sm" onClick={() => setEditing(t)}>
                        Clasificar
                      </Button>
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
      {editing && <ClassifyDialog type={editing} onClose={() => setEditing(null)} />}
    </Card>
  );
}
