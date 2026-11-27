"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Tags } from "lucide-react";
import { useState } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { mastersApi, type Category, type Family } from "@/lib/api/masters";

import { PRODUCT_KINDS } from "./labels";
import { categoriesKey, familiesKey } from "./products";
import { EditDialog, Field, selectClass, useMastersAccess, yesNo } from "./shared";

function CategoryDialog({
  category,
  categories,
  onClose,
}: {
  category: Category | null;
  categories: Category[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [code, setCode] = useState("");
  const [name, setName] = useState(category?.name ?? "");
  const [kind, setKind] = useState(category?.default_product_kind ?? "");
  const [eligible, setEligible] = useState(category?.default_analytics_eligible ?? true);
  const [parent, setParent] = useState(category?.parent_id?.toString() ?? "");
  const [order, setOrder] = useState(String(category?.sort_order ?? 0));
  const [active, setActive] = useState(category?.active ?? true);
  const save = useMutation({
    mutationFn: (reason: string) => {
      const common = {
        name: name.trim(),
        default_product_kind: kind || null,
        default_analytics_eligible: eligible,
        parent_id: parent ? Number(parent) : null,
        sort_order: Number(order) || 0,
      };
      return category === null
        ? mastersApi.createCategory({ ...common, code: code.trim() })
        : mastersApi.updateCategory(category.id, {
            ...common,
            active,
            reason,
            expected_updated_at: category.updated_at,
          });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: categoriesKey });
      onClose();
    },
  });
  return (
    <EditDialog
      title={category ? `Modificar ${category.name}` : "Nueva categoría"}
      description="Cambiar los valores por defecto no reescribe los productos ya clasificados: se aplican a los que reciban la categoría desde ahora."
      onClose={onClose}
      onSave={(reason) => save.mutate(reason)}
      pending={save.isPending}
      error={save.error}
      requireReason={category !== null}
    >
      {category === null && (
        <Field label="Código" hint="Identificador estable, en mayúsculas. No cambia después.">
          {(id) => <Input id={id} value={code} maxLength={40} required onChange={(e) => setCode(e.target.value)} />}
        </Field>
      )}
      <Field label="Nombre">
        {(id) => <Input id={id} value={name} maxLength={120} required onChange={(e) => setName(e.target.value)} />}
      </Field>
      <Field label="Tipo de producto por defecto">
        {(id) => (
          <select id={id} className={selectClass} value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">Ninguno</option>
            {Object.entries(PRODUCT_KINDS)
              .filter(([value]) => value !== "UNCLASSIFIED")
              .map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
          </select>
        )}
      </Field>
      <Field label="Categoría padre">
        {(id) => (
          <select id={id} className={selectClass} value={parent} onChange={(e) => setParent(e.target.value)}>
            <option value="">Ninguna</option>
            {categories
              .filter((c) => c.id !== category?.id)
              .map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
          </select>
        )}
      </Field>
      <Field label="Orden">
        {(id) => <Input id={id} type="number" value={order} onChange={(e) => setOrder(e.target.value)} />}
      </Field>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={eligible} onChange={(e) => setEligible(e.target.checked)} />
        Sus productos entran en los análisis por defecto
      </label>
      {category !== null && (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
          Activa
        </label>
      )}
    </EditDialog>
  );
}

function FamilyDialog({ family, onClose }: { family: Family | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [code, setCode] = useState("");
  const [name, setName] = useState(family?.name ?? "");
  const [description, setDescription] = useState(family?.description ?? "");
  const [active, setActive] = useState(family?.active ?? true);
  const save = useMutation({
    mutationFn: (reason: string) =>
      family === null
        ? mastersApi.createFamily({ code: code.trim(), name: name.trim(), description: description.trim() || null })
        : mastersApi.updateFamily(family.id, {
            name: name.trim(),
            description: description.trim() || null,
            active,
            reason,
            expected_updated_at: family.updated_at,
          }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: familiesKey });
      onClose();
    },
  });
  return (
    <EditDialog
      title={family ? `Modificar ${family.name}` : "Nueva familia"}
      description="Las familias se asignan a mano a cada producto; nunca se infieren de la descripción."
      onClose={onClose}
      onSave={(reason) => save.mutate(reason)}
      pending={save.isPending}
      error={save.error}
      requireReason={family !== null}
    >
      {family === null && (
        <Field label="Código">
          {(id) => <Input id={id} value={code} maxLength={40} required onChange={(e) => setCode(e.target.value)} />}
        </Field>
      )}
      <Field label="Nombre">
        {(id) => <Input id={id} value={name} maxLength={120} required onChange={(e) => setName(e.target.value)} />}
      </Field>
      <Field label="Descripción">
        {(id) => <Input id={id} value={description} maxLength={500} onChange={(e) => setDescription(e.target.value)} />}
      </Field>
      {family !== null && (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
          Activa
        </label>
      )}
    </EditDialog>
  );
}

/** `/comercial/datos/maestros/categorias`: product categories and families. */
export function CategoriesView() {
  const { isAdmin } = useMastersAccess();
  const categories = useQuery({ queryKey: categoriesKey, queryFn: mastersApi.categories });
  const families = useQuery({ queryKey: familiesKey, queryFn: mastersApi.families });
  const [category, setCategory] = useState<Category | null | undefined>(undefined);
  const [family, setFamily] = useState<Family | null | undefined>(undefined);
  const nameOf = (id: number | null) => (id === null ? "—" : (categories.data?.find((c) => c.id === id)?.name ?? "—"));

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3">
          <CardTitle>Categorías</CardTitle>
          {isAdmin && (
            <Button size="sm" onClick={() => setCategory(null)}>
              Nueva categoría
            </Button>
          )}
        </CardHeader>
        <CardContent>
          {categories.isPending ? (
            <Skeleton className="h-20" />
          ) : categories.isError ? (
            <ErrorState error={categories.error} onRetry={() => void categories.refetch()} />
          ) : categories.data.length === 0 ? (
            <EmptyState icon={Tags} title="Sin categorías" description="Un admin crea las categorías de producto." />
          ) : (
            <Table stacked>
              <TableHeader>
                <TableRow>
                  <TableHead>Categoría</TableHead>
                  <TableHead>Padre</TableHead>
                  <TableHead>Tipo por defecto</TableHead>
                  <TableHead>Análisis por defecto</TableHead>
                  <TableHead>Estado</TableHead>
                  {isAdmin && <TableHead className="sr-only">Acciones</TableHead>}
                </TableRow>
              </TableHeader>
              <TableBody>
                {categories.data.map((c) => (
                  <TableRow key={c.id}>
                    <TableCell data-label="Categoría" className="font-medium">
                      {c.name}
                    </TableCell>
                    <TableCell data-label="Padre">{nameOf(c.parent_id)}</TableCell>
                    <TableCell data-label="Tipo por defecto">
                      {c.default_product_kind ? PRODUCT_KINDS[c.default_product_kind] : "—"}
                    </TableCell>
                    <TableCell data-label="Análisis por defecto">{yesNo(c.default_analytics_eligible)}</TableCell>
                    <TableCell data-label="Estado">
                      {c.active ? <Badge tone="success">Activa</Badge> : <Badge>Inactiva</Badge>}
                    </TableCell>
                    {isAdmin && (
                      <TableCell data-label="Acciones">
                        <Button variant="ghost" size="sm" onClick={() => setCategory(c)}>
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

      <Card>
        <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3">
          <CardTitle>Familias</CardTitle>
          {isAdmin && (
            <Button size="sm" variant="secondary" onClick={() => setFamily(null)}>
              Nueva familia
            </Button>
          )}
        </CardHeader>
        <CardContent>
          {families.isPending ? (
            <Skeleton className="h-20" />
          ) : families.isError ? (
            <ErrorState error={families.error} onRetry={() => void families.refetch()} />
          ) : families.data.length === 0 ? (
            <EmptyState
              icon={Tags}
              title="Sin familias"
              description="Las familias agrupan productos a mano (por ejemplo, un mismo producto en distintos envases)."
            />
          ) : (
            <Table stacked>
              <TableHeader>
                <TableRow>
                  <TableHead>Familia</TableHead>
                  <TableHead>Descripción</TableHead>
                  <TableHead>Estado</TableHead>
                  {isAdmin && <TableHead className="sr-only">Acciones</TableHead>}
                </TableRow>
              </TableHeader>
              <TableBody>
                {families.data.map((f) => (
                  <TableRow key={f.id}>
                    <TableCell data-label="Familia" className="font-medium">
                      {f.name}
                    </TableCell>
                    <TableCell data-label="Descripción">{f.description ?? "—"}</TableCell>
                    <TableCell data-label="Estado">
                      {f.active ? <Badge tone="success">Activa</Badge> : <Badge>Inactiva</Badge>}
                    </TableCell>
                    {isAdmin && (
                      <TableCell data-label="Acciones">
                        <Button variant="ghost" size="sm" onClick={() => setFamily(f)}>
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

      {category !== undefined && (
        <CategoryDialog category={category} categories={categories.data ?? []} onClose={() => setCategory(undefined)} />
      )}
      {family !== undefined && <FamilyDialog family={family} onClose={() => setFamily(undefined)} />}
    </div>
  );
}
