"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Plus } from "lucide-react";
import { useId, useRef, useState, type FormEvent } from "react";

import { ErrorState } from "@/components/common/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { cn } from "@/lib/cn";
import { deliveryApi, deliveryQueryKey, FUEL_CODE, type ExpenseCategory } from "@/modules/settlements/delivery/api";
import { FormError } from "@/modules/settlements/delivery/form";

const categoriesKey = [...deliveryQueryKey, "categories"] as const;

/** Same rule as the backend: surrounding and repeated spaces never make a different name. */
const clean = (name: string) => name.split(/\s+/).filter(Boolean).join(" ");

function categoryError(error: unknown): string {
  if (!(error instanceof ApiError)) return "No se pudo guardar. Revisá la conexión y volvé a intentar.";
  if (error.code === "expense_category_name_taken") {
    const inactive =
      typeof error.details === "object" && error.details !== null && "active" in error.details
        ? (error.details as { active: unknown }).active === false
        : false;
    return inactive ? `${error.message} Está desactivada: activala desde la lista.` : error.message;
  }
  if (error.code === "forbidden") return "Solo un administrador modifica las categorías.";
  if (error.status === 0 || error.status >= 500) return "No se pudo guardar. Volvé a intentar.";
  return error.message || "No se pudo guardar.";
}

function RenameDialog({
  category,
  onClose,
  onSaved,
}: {
  category: ExpenseCategory;
  onClose: () => void;
  onSaved: (category: ExpenseCategory) => void;
}) {
  const id = useId();
  const [name, setName] = useState(category.name);
  const [problem, setProblem] = useState<string | null>(null);
  const rename = useMutation({
    mutationFn: (value: string) => deliveryApi.updateCategory(category.id, { name: value }),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const value = clean(name);
    if (!value) return setProblem("Ingresá el nombre de la categoría.");
    if (value === category.name) return onClose();
    setProblem(null);
    rename.mutate(value, { onSuccess: onSaved });
  };

  return (
    <Dialog
      title="Renombrar categoría"
      description="Los gastos ya cargados con esta categoría van a mostrar el nombre nuevo."
      onClose={onClose}
    >
      <form className="space-y-4" onSubmit={submit} noValidate>
        <div className="space-y-1.5">
          <Label htmlFor={id}>Nombre</Label>
          <Input id={id} maxLength={100} value={name} onChange={(event) => setName(event.target.value)} />
        </div>
        <FormError message={problem ?? (rename.error ? categoryError(rename.error) : null)} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button type="submit" disabled={rename.isPending}>
            {rename.isPending ? "Guardando…" : "Guardar"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

/**
 * Categorías de gastos (R2): admins create, rename, activate and deactivate them; nothing is ever deleted.
 * A deactivated category is not offered for new expenses, and expenses already loaded with it keep it.
 * Names are compared ignoring case, accents and spacing by the backend, which refuses duplicates.
 */
export function ExpenseCategories() {
  const queryClient = useQueryClient();
  const nameId = useId();
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const isAdmin = roleAtLeast(moduleRole(me.data, "settlements"), "admin");
  const list = useQuery({ queryKey: [...categoriesKey, "all"], queryFn: deliveryApi.allCategories });
  const [name, setName] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<ExpenseCategory | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const busy = useRef(false);

  const done = (text: string) => {
    setNotice(text);
    void queryClient.invalidateQueries({ queryKey: categoriesKey });
  };
  const create = useMutation({
    mutationFn: deliveryApi.createCategory,
    onSuccess: (category) => {
      setName("");
      done(`Categoría «${category.name}» creada.`);
    },
  });
  const toggle = useMutation({
    mutationFn: (category: ExpenseCategory) => deliveryApi.updateCategory(category.id, { active: !category.active }),
    onSuccess: (category) =>
      done(
        category.active
          ? `«${category.name}» está activa otra vez.`
          : category.code === FUEL_CODE
            ? `«${category.name}» quedó desactivada: no se pueden registrar cargas de combustible nuevas. Las ya cargadas se conservan.`
            : `«${category.name}» quedó desactivada.`,
      ),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const value = clean(name);
    if (!value) return setProblem("Ingresá el nombre de la categoría.");
    if (busy.current) return;
    setProblem(null);
    busy.current = true;
    create.mutate(value, {
      onSettled: () => {
        busy.current = false;
      },
    });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Categorías de gastos</CardTitle>
        <CardDescription>
          Las que se eligen al registrar un gasto en una rendición. No se borran: una categoría desactivada deja de
          ofrecerse para gastos nuevos y los gastos ya cargados la conservan.
          {!isAdmin && " Solo un administrador las modifica."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {isAdmin && (
          <form className="flex flex-wrap items-end gap-2" onSubmit={submit} noValidate>
            <div className="min-w-56 flex-1 space-y-1.5">
              <Label htmlFor={nameId}>Nueva categoría</Label>
              <Input
                id={nameId}
                maxLength={100}
                placeholder="Por ejemplo Peajes"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </div>
            <Button type="submit" disabled={create.isPending}>
              <Plus aria-hidden />
              {create.isPending ? "Creando…" : "Crear categoría"}
            </Button>
          </form>
        )}
        <FormError
          message={
            problem ?? (create.error ? categoryError(create.error) : toggle.error ? categoryError(toggle.error) : null)
          }
        />
        {notice && (
          <p role="status" className="flex items-center gap-2 text-sm text-primary">
            <CheckCircle2 className="size-4" aria-hidden />
            {notice}
          </p>
        )}

        {list.isPending ? (
          <Skeleton className="h-32 w-full" aria-label="Cargando categorías" />
        ) : list.isError ? (
          <ErrorState error={list.error} onRetry={() => void list.refetch()} />
        ) : list.data.length === 0 ? (
          <p className="py-4 text-center text-sm text-muted-foreground">
            Todavía no hay categorías. {isAdmin ? "Creá la primera arriba." : "Pedile a un administrador que las cree."}
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Nombre</TableHead>
                <TableHead>Estado</TableHead>
                {isAdmin && (
                  <TableHead>
                    <span className="sr-only">Acciones</span>
                  </TableHead>
                )}
              </TableRow>
            </TableHeader>
            <TableBody>
              {list.data.map((category) => (
                <TableRow key={category.id}>
                  <TableCell className={cn(!category.active && "text-muted-foreground")}>
                    {category.name}
                    {category.code === FUEL_CODE && (
                      <Badge
                        tone="info"
                        className="ml-2"
                        title="Pide vehículo, litros y precio por litro; el importe se calcula"
                      >
                        Combustible
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell>
                    {category.active ? <Badge tone="success">Activa</Badge> : <Badge>Desactivada</Badge>}
                  </TableCell>
                  {isAdmin && (
                    <TableCell className="text-right whitespace-nowrap">
                      <Button variant="ghost" size="sm" onClick={() => setRenaming(category)}>
                        Renombrar
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={toggle.isPending}
                        onClick={() => toggle.mutate(category)}
                        aria-label={`${category.active ? "Desactivar" : "Activar"} ${category.name}`}
                      >
                        {category.active ? "Desactivar" : "Activar"}
                      </Button>
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
      {renaming && (
        <RenameDialog
          category={renaming}
          onClose={() => setRenaming(null)}
          onSaved={(category) => {
            setRenaming(null);
            done(`Categoría renombrada a «${category.name}».`);
          }}
        />
      )}
    </Card>
  );
}
