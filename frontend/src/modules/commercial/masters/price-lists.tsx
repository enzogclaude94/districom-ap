"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ListOrdered } from "lucide-react";
import { useState } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { mastersApi, mastersQueryKey, type PriceList } from "@/lib/api/masters";

import { PRICE_LIST_KINDS } from "./labels";
import { EditDialog, Field, selectClass, useMastersAccess, yesNo } from "./shared";

const listsKey = [...mastersQueryKey, "price-lists"] as const;
const articlesKey = [...mastersQueryKey, "articles-price-list"] as const;

function ListDialog({ list, onClose }: { list: PriceList | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [number, setNumber] = useState("");
  const [name, setName] = useState(list?.name ?? "");
  const [kind, setKind] = useState(list?.kind ?? "UNASSIGNED");
  const [relevant, setRelevant] = useState(list?.commercially_relevant ?? true);
  const [description, setDescription] = useState(list?.description ?? "");
  const [active, setActive] = useState(list?.active ?? true);
  const save = useMutation({
    mutationFn: (reason: string) => {
      const common = {
        name: name.trim(),
        kind,
        commercially_relevant: relevant,
        description: description.trim() || null,
      };
      return list === null
        ? mastersApi.createPriceList({ ...common, list_number: Number(number) })
        : mastersApi.updatePriceList(list.id, { ...common, active, reason, expected_updated_at: list.updated_at });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: listsKey });
      onClose();
    },
  });
  return (
    <EditDialog
      title={list ? `Modificar la lista ${list.list_number}` : "Nueva lista de precios"}
      description="El número de lista es su identidad en STORE SYS y no cambia. Creá una lista solo si existe allá."
      onClose={onClose}
      onSave={(reason) => save.mutate(reason)}
      pending={save.isPending}
      error={save.error}
      requireReason={list !== null}
    >
      {list === null && (
        <Field label="Número de lista en STORE SYS">
          {(id) => (
            <Input id={id} type="number" min={1} value={number} required onChange={(e) => setNumber(e.target.value)} />
          )}
        </Field>
      )}
      <Field label="Nombre">
        {(id) => <Input id={id} value={name} maxLength={120} required onChange={(e) => setName(e.target.value)} />}
      </Field>
      <Field label="Uso">
        {(id) => (
          <select id={id} className={selectClass} value={kind} onChange={(e) => setKind(e.target.value)}>
            {Object.entries(PRICE_LIST_KINDS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        )}
      </Field>
      <Field label="Descripción">
        {(id) => <Input id={id} value={description} maxLength={500} onChange={(e) => setDescription(e.target.value)} />}
      </Field>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={relevant} onChange={(e) => setRelevant(e.target.checked)} />
        Relevante para el análisis comercial
      </label>
      {list !== null && (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
          Activa
        </label>
      )}
    </EditDialog>
  );
}

function ArticlesListDialog({
  current,
  lists,
  onClose,
}: {
  current: number | null;
  lists: PriceList[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [number, setNumber] = useState(current?.toString() ?? "");
  const save = useMutation({
    mutationFn: (reason: string) =>
      mastersApi.setArticlesPriceList({ list_number: number ? Number(number) : null, reason }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: articlesKey });
      void queryClient.invalidateQueries({ queryKey: [...mastersQueryKey, "products"] });
      onClose();
    },
  });
  return (
    <EditDialog
      title="Lista que alimenta el precio del maestro de artículos"
      description="Se aplica a los maestros que se importen desde ahora. Los precios ya cargados quedan en la lista donde se guardaron: nada se reescribe."
      onClose={onClose}
      onSave={(reason) => save.mutate(reason)}
      pending={save.isPending}
      error={save.error}
      saveLabel="Confirmar"
    >
      <Field label="El precio del maestro de artículos alimenta">
        {(id) => (
          <select id={id} className={selectClass} value={number} onChange={(e) => setNumber(e.target.value)}>
            <option value="">Ninguna (no se guardan esos precios)</option>
            {lists.map((l) => (
              <option key={l.id} value={l.list_number}>
                Lista {l.list_number} · {l.name}
              </option>
            ))}
          </select>
        )}
      </Field>
    </EditDialog>
  );
}

/** `/comercial/datos/maestros/listas`: price lists by their STORE SYS number and the articles master's list. */
export function PriceListsView() {
  const { isAdmin } = useMastersAccess();
  const lists = useQuery({ queryKey: listsKey, queryFn: mastersApi.priceLists });
  const articles = useQuery({ queryKey: articlesKey, queryFn: mastersApi.articlesPriceList });
  const [editing, setEditing] = useState<PriceList | null | undefined>(undefined);
  const [choosing, setChoosing] = useState(false);
  const current = articles.data?.list_number ?? null;
  const currentList = lists.data?.find((l) => l.list_number === current);
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <CardTitle>El precio del maestro de artículos alimenta:</CardTitle>
            <p className="text-lg font-medium">
              {articles.isPending
                ? "…"
                : current === null
                  ? "Ninguna lista: los precios del maestro no se guardan"
                  : `Lista ${current}${currentList ? ` · ${currentList.name}` : ""}`}
            </p>
          </div>
          {isAdmin && (
            <Button size="sm" variant="secondary" onClick={() => setChoosing(true)} disabled={!lists.data}>
              Cambiar
            </Button>
          )}
        </CardHeader>
      </Card>
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3">
          <div className="space-y-1">
            <CardTitle>Listas de precios</CardTitle>
            <p className="text-sm text-muted-foreground">
              Una lista que no aparece en las ventas de un período sigue existiendo: no se crean ni se borran listas por
              ausencia.
            </p>
          </div>
          {isAdmin && (
            <Button size="sm" onClick={() => setEditing(null)}>
              Nueva lista
            </Button>
          )}
        </CardHeader>
        <CardContent>
          {lists.isPending ? (
            <Skeleton className="h-20" />
          ) : lists.isError ? (
            <ErrorState error={lists.error} onRetry={() => void lists.refetch()} />
          ) : lists.data.length === 0 ? (
            <EmptyState
              icon={ListOrdered}
              title="Sin listas de precios"
              description="Un admin carga las listas de STORE SYS."
            />
          ) : (
            <Table stacked>
              <TableHeader>
                <TableRow>
                  <TableHead>Número</TableHead>
                  <TableHead>Nombre</TableHead>
                  <TableHead>Uso</TableHead>
                  <TableHead>Relevante</TableHead>
                  <TableHead>Estado</TableHead>
                  {isAdmin && <TableHead className="sr-only">Acciones</TableHead>}
                </TableRow>
              </TableHeader>
              <TableBody>
                {lists.data.map((l) => (
                  <TableRow key={l.id}>
                    <TableCell data-label="Número" className="tabular-nums">
                      {l.list_number}
                    </TableCell>
                    <TableCell data-label="Nombre">
                      <span className="font-medium">{l.name}</span>
                      {l.description && <span className="block text-xs text-muted-foreground">{l.description}</span>}
                    </TableCell>
                    <TableCell data-label="Uso">{PRICE_LIST_KINDS[l.kind] ?? l.kind}</TableCell>
                    <TableCell data-label="Relevante">{yesNo(l.commercially_relevant)}</TableCell>
                    <TableCell data-label="Estado">
                      {l.active ? <Badge tone="success">Activa</Badge> : <Badge>Inactiva</Badge>}
                    </TableCell>
                    {isAdmin && (
                      <TableCell data-label="Acciones">
                        <Button variant="ghost" size="sm" onClick={() => setEditing(l)}>
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
      {editing !== undefined && <ListDialog list={editing} onClose={() => setEditing(undefined)} />}
      {choosing && lists.data && (
        <ArticlesListDialog current={current} lists={lists.data} onClose={() => setChoosing(false)} />
      )}
    </div>
  );
}
