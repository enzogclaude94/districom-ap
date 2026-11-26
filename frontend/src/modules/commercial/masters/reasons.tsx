"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageSquareOff } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useState } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { mastersApi, mastersQueryKey, type Reason, type ReasonCategory } from "@/lib/api/masters";
import { cn } from "@/lib/cn";

import { EditDialog, Field, selectClass, useMastersAccess } from "./shared";

const reasonsKey = [...mastersQueryKey, "reasons"] as const;
const categoriesKey = [...mastersQueryKey, "reason-categories"] as const;

function AssignDialog({
  reason,
  categories,
  onClose,
}: {
  reason: Reason;
  categories: ReasonCategory[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [category, setCategory] = useState(reason.category_id?.toString() ?? "");
  const save = useMutation({
    mutationFn: (why: string) =>
      mastersApi.setReasonCategory(reason.id, {
        category_id: category ? Number(category) : null,
        reason: why,
        expected_updated_at: reason.updated_at,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: reasonsKey });
      onClose();
    },
  });
  return (
    <EditDialog
      title={`Categoría de «${reason.reason_source}»`}
      description="Se decide a mano: DistriCom nunca infiere la categoría del texto del motivo."
      onClose={onClose}
      onSave={(why) => save.mutate(why)}
      pending={save.isPending}
      error={save.error}
    >
      <Field label="Categoría">
        {(id) => (
          <select id={id} className={selectClass} value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">Sin categoría</option>
            {categories
              .filter((c) => c.active || c.id === reason.category_id)
              .map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
          </select>
        )}
      </Field>
    </EditDialog>
  );
}

function CategoryDialog({ category, onClose }: { category: ReasonCategory | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [code, setCode] = useState("");
  const [name, setName] = useState(category?.name ?? "");
  const [active, setActive] = useState(category?.active ?? true);
  const save = useMutation({
    mutationFn: (why: string) =>
      category === null
        ? mastersApi.createReasonCategory({ code: code.trim(), name: name.trim() })
        : mastersApi.updateReasonCategory(category.id, {
            name: name.trim(),
            active,
            reason: why,
            expected_updated_at: category.updated_at,
          }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: categoriesKey });
      onClose();
    },
  });
  return (
    <EditDialog
      title={category ? `Modificar ${category.name}` : "Nueva categoría de motivo"}
      onClose={onClose}
      onSave={(why) => save.mutate(why)}
      pending={save.isPending}
      error={save.error}
      requireReason={category !== null}
    >
      {category === null && (
        <Field label="Código">
          {(id) => <Input id={id} value={code} maxLength={40} required onChange={(e) => setCode(e.target.value)} />}
        </Field>
      )}
      <Field label="Nombre">
        {(id) => <Input id={id} value={name} maxLength={120} required onChange={(e) => setName(e.target.value)} />}
      </Field>
      {category !== null && (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
          Activa
        </label>
      )}
    </EditDialog>
  );
}

/** `/comercial/datos/maestros/motivos`: AXUM's no-purchase reasons and the categories DistriCom groups them in. */
export function ReasonsView() {
  const { isAdmin } = useMastersAccess();
  const wanted = Number(useSearchParams().get("motivo")) || null; // from Calidad: the exact reason
  const reasons = useQuery({ queryKey: reasonsKey, queryFn: mastersApi.reasons });
  const categories = useQuery({ queryKey: categoriesKey, queryFn: mastersApi.reasonCategories });
  const [assigning, setAssigning] = useState<Reason | null>(null);
  const [editing, setEditing] = useState<ReasonCategory | null | undefined>(undefined);
  const nameOf = (id: number | null) => (id === null ? null : categories.data?.find((c) => c.id === id)?.name);
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Motivos de no compra</CardTitle>
          <p className="text-sm text-muted-foreground">
            Los motivos llegan con el reporte de AXUM tal como están escritos allá.
          </p>
        </CardHeader>
        <CardContent>
          {reasons.isPending ? (
            <Skeleton className="h-20" />
          ) : reasons.isError ? (
            <ErrorState error={reasons.error} onRetry={() => void reasons.refetch()} />
          ) : reasons.data.length === 0 ? (
            <EmptyState
              icon={MessageSquareOff}
              title="Todavía no hay motivos"
              description="Aparecen al importar el reporte de no compra de AXUM."
            />
          ) : (
            <Table stacked>
              <TableHeader>
                <TableRow>
                  <TableHead>Motivo en AXUM</TableHead>
                  <TableHead>Categoría</TableHead>
                  {isAdmin && <TableHead className="sr-only">Acciones</TableHead>}
                </TableRow>
              </TableHeader>
              <TableBody>
                {reasons.data.map((r) => (
                  <TableRow
                    key={r.id}
                    aria-current={r.id === wanted ? "true" : undefined}
                    className={cn(r.id === wanted && "bg-warning-soft")}
                  >
                    <TableCell data-label="Motivo en AXUM" className="font-medium">
                      {r.reason_source}
                    </TableCell>
                    <TableCell data-label="Categoría">
                      {nameOf(r.category_id) ?? <Badge tone="warning">Sin categoría</Badge>}
                    </TableCell>
                    {isAdmin && (
                      <TableCell data-label="Acciones">
                        <Button variant="ghost" size="sm" onClick={() => setAssigning(r)} disabled={!categories.data}>
                          Asignar categoría
                        </Button>
                      </TableCell>
                    )}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3">
          <CardTitle>Categorías de motivo</CardTitle>
          {isAdmin && (
            <Button size="sm" variant="secondary" onClick={() => setEditing(null)}>
              Nueva categoría
            </Button>
          )}
        </CardHeader>
        <CardContent>
          {categories.isPending ? (
            <Skeleton className="h-10" />
          ) : categories.isError ? (
            <ErrorState error={categories.error} onRetry={() => void categories.refetch()} />
          ) : (
            <Table stacked>
              <TableHeader>
                <TableRow>
                  <TableHead>Código</TableHead>
                  <TableHead>Nombre</TableHead>
                  <TableHead>Estado</TableHead>
                  {isAdmin && <TableHead className="sr-only">Acciones</TableHead>}
                </TableRow>
              </TableHeader>
              <TableBody>
                {categories.data.map((c) => (
                  <TableRow key={c.id}>
                    <TableCell data-label="Código">{c.code}</TableCell>
                    <TableCell data-label="Nombre" className="font-medium">
                      {c.name}
                    </TableCell>
                    <TableCell data-label="Estado">
                      {c.active ? <Badge tone="success">Activa</Badge> : <Badge>Inactiva</Badge>}
                    </TableCell>
                    {isAdmin && (
                      <TableCell data-label="Acciones">
                        <Button variant="ghost" size="sm" onClick={() => setEditing(c)}>
                          Modificar
                        </Button>
                      </TableCell>
                    )}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      {assigning && categories.data && (
        <AssignDialog reason={assigning} categories={categories.data} onClose={() => setAssigning(null)} />
      )}
      {editing !== undefined && <CategoryDialog category={editing} onClose={() => setEditing(undefined)} />}
    </div>
  );
}
