"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Package } from "lucide-react";
import Link from "next/link";
import { useId, useState } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  mastersApi,
  mastersQueryKey,
  type Category,
  type Family,
  type ProductDetail,
  type ProductFilters,
} from "@/lib/api/masters";
import { formatDate, formatMoney } from "@/lib/format";

import { CATEGORY_ORIGINS, MASTERS, PRODUCT_KINDS } from "./labels";
import { EditDialog, Fact, Field, PAGE_SIZE, Pager, selectClass, useMastersAccess, yesNo } from "./shared";

const productsKey = [...mastersQueryKey, "products"] as const;
export const categoriesKey = [...mastersQueryKey, "categories"] as const;
export const familiesKey = [...mastersQueryKey, "families"] as const;

const tri = (value: string) => (value === "" ? undefined : value === "yes");

function useCatalogNames() {
  const categories = useQuery({ queryKey: categoriesKey, queryFn: mastersApi.categories });
  const families = useQuery({ queryKey: familiesKey, queryFn: mastersApi.families });
  const category = (id: number | null) => (id === null ? null : categories.data?.find((c) => c.id === id)?.name);
  const family = (id: number | null) => (id === null ? null : families.data?.find((f) => f.id === id)?.name);
  return { categories: categories.data ?? [], families: families.data ?? [], category, family };
}

/** `/comercial/datos/maestros/productos`: the STORE SYS articles and what DistriCom decided about each one. */
export function ProductList() {
  const ids = { q: useId(), category: useId(), family: useId(), has: useId(), eligible: useId(), active: useId() };
  const names = useCatalogNames();
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [category, setCategory] = useState("");
  const [family, setFamily] = useState("");
  const [has, setHas] = useState("");
  const [eligible, setEligible] = useState("");
  const [active, setActive] = useState("");
  const [page, setPage] = useState(0);
  const filters: ProductFilters = {
    q: q || undefined,
    category_id: category ? Number(category) : undefined,
    family_id: family ? Number(family) : undefined,
    has_category: tri(has),
    analytics_eligible: tri(eligible),
    active: tri(active),
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  };
  const list = useQuery({
    queryKey: [...productsKey, "list", filters],
    queryFn: () => mastersApi.products(filters),
    placeholderData: keepPreviousData,
  });
  const pick = (set: (v: string) => void) => (e: { target: { value: string } }) => {
    set(e.target.value);
    setPage(0);
  };
  const items = list.data?.items ?? [];
  const yesNoAll = (id: string, label: string, value: string, set: (v: string) => void, yes: string, no: string) => (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <select id={id} className={selectClass} value={value} onChange={pick(set)}>
        <option value="">Todos</option>
        <option value="yes">{yes}</option>
        <option value="no">{no}</option>
      </select>
    </div>
  );

  return (
    <Card>
      <CardHeader className="space-y-3">
        <CardTitle>Productos</CardTitle>
        <p className="text-sm text-muted-foreground">
          Código, descripción y precios vienen del maestro de artículos de STORE SYS. DistriCom decide categoría, tipo,
          familia y si el artículo entra en los análisis.
        </p>
        <div className="grid gap-3 sm:grid-cols-4">
          <form
            className="space-y-1.5 sm:col-span-2"
            onSubmit={(e) => {
              e.preventDefault();
              setQ(search.trim());
              setPage(0);
            }}
          >
            <Label htmlFor={ids.q}>Buscar por código o descripción</Label>
            <Input id={ids.q} value={search} maxLength={100} onChange={(e) => setSearch(e.target.value)} />
          </form>
          <div className="space-y-1.5">
            <Label htmlFor={ids.category}>Categoría</Label>
            <select id={ids.category} className={selectClass} value={category} onChange={pick(setCategory)}>
              <option value="">Todas</option>
              {names.categories.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.family}>Familia</Label>
            <select id={ids.family} className={selectClass} value={family} onChange={pick(setFamily)}>
              <option value="">Todas</option>
              {names.families.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.name}
                </option>
              ))}
            </select>
          </div>
          {yesNoAll(ids.has, "Con categoría", has, setHas, "Con categoría", "Sin categoría")}
          {yesNoAll(ids.eligible, "Entra en análisis", eligible, setEligible, "Sí", "No")}
          {yesNoAll(ids.active, "Estado", active, setActive, "Activos", "Inactivos")}
        </div>
      </CardHeader>
      <CardContent>
        {list.isPending ? (
          <Skeleton className="h-20" />
        ) : list.isError ? (
          <ErrorState error={list.error} onRetry={() => void list.refetch()} />
        ) : items.length === 0 ? (
          <EmptyState
            icon={Package}
            title="No hay productos con estos filtros"
            description="Los productos llegan con el maestro de artículos de STORE SYS."
          />
        ) : (
          <>
            <Table stacked>
              <TableHeader>
                <TableRow>
                  <TableHead>Código</TableHead>
                  <TableHead>Descripción</TableHead>
                  <TableHead>Categoría</TableHead>
                  <TableHead>Tipo</TableHead>
                  <TableHead>Familia</TableHead>
                  <TableHead>Análisis</TableHead>
                  <TableHead className="text-right">Precio del maestro</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((p) => (
                  <TableRow key={p.id}>
                    <TableCell data-label="Código" className="tabular-nums">
                      <Link
                        href={`${MASTERS}/productos/${p.id}`}
                        className="font-medium underline-offset-2 hover:underline"
                      >
                        {p.source_code}
                      </Link>
                    </TableCell>
                    <TableCell data-label="Descripción">
                      {p.source_description ?? "—"}
                      {!p.active && <Badge className="ml-2">Inactivo</Badge>}
                    </TableCell>
                    <TableCell data-label="Categoría">
                      {names.category(p.canonical_category_id) ?? <Badge tone="warning">Sin categoría</Badge>}
                    </TableCell>
                    <TableCell data-label="Tipo">{PRODUCT_KINDS[p.product_kind] ?? p.product_kind}</TableCell>
                    <TableCell data-label="Familia">{names.family(p.family_id) ?? "—"}</TableCell>
                    <TableCell data-label="Análisis">{yesNo(p.analytics_eligible)}</TableCell>
                    <TableCell data-label="Precio del maestro" className="text-right tabular-nums">
                      {p.master_price !== null ? formatMoney(p.master_price) : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <Pager page={page} total={list.data.total} onPage={setPage} />
          </>
        )}
      </CardContent>
    </Card>
  );
}

function ProductDialog({
  product,
  categories,
  families,
  onClose,
}: {
  product: ProductDetail;
  categories: Category[];
  families: Family[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [category, setCategory] = useState(product.canonical_category_id?.toString() ?? "");
  const [kind, setKind] = useState(product.product_kind);
  const [family, setFamily] = useState(product.family_id?.toString() ?? "");
  const [eligible, setEligible] = useState(product.analytics_eligible);
  const [active, setActive] = useState(product.active);
  const save = useMutation({
    mutationFn: (reason: string) => {
      // Only what changed: re-sending the same category would turn its origin into a manual decision.
      const categoryId = category ? Number(category) : null;
      const familyId = family ? Number(family) : null;
      return mastersApi.updateProduct(product.id, {
        ...(categoryId !== product.canonical_category_id ? { category_id: categoryId } : {}),
        ...(kind !== product.product_kind ? { product_kind: kind } : {}),
        ...(familyId !== product.family_id ? { family_id: familyId } : {}),
        // Without a category a product never enters the analytics; with a new one, its default applies.
        ...(eligible !== product.analytics_eligible && categoryId !== null ? { analytics_eligible: eligible } : {}),
        ...(active !== product.active ? { active } : {}),
        reason,
        expected_updated_at: product.updated_at,
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: productsKey });
      onClose();
    },
  });
  return (
    <EditDialog
      title={`Clasificar ${product.source_code}`}
      description="Cambia solo lo que DistriCom decide del artículo. El código, la descripción y los precios siguen siendo los de STORE SYS."
      onClose={onClose}
      onSave={(reason) => save.mutate(reason)}
      pending={save.isPending}
      error={save.error}
    >
      <Field label="Categoría" hint="Al asignarla se aplica la elegibilidad por defecto de la categoría.">
        {(id) => (
          <select id={id} className={selectClass} value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">Sin categoría</option>
            {categories
              .filter((c) => c.active || c.id === product.canonical_category_id)
              .map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
          </select>
        )}
      </Field>
      <Field label="Tipo">
        {(id) => (
          <select id={id} className={selectClass} value={kind} onChange={(e) => setKind(e.target.value)}>
            {Object.entries(PRODUCT_KINDS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        )}
      </Field>
      <Field label="Familia">
        {(id) => (
          <select id={id} className={selectClass} value={family} onChange={(e) => setFamily(e.target.value)}>
            <option value="">Sin familia</option>
            {families
              .filter((f) => f.active || f.id === product.family_id)
              .map((f) => (
                <option key={f.id} value={f.id}>
                  {f.name}
                </option>
              ))}
          </select>
        )}
      </Field>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={eligible && category !== ""}
          disabled={category === ""}
          onChange={(e) => setEligible(e.target.checked)}
        />
        Entra en los análisis
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
        Activo
      </label>
    </EditDialog>
  );
}

/** `/comercial/datos/maestros/productos/[id]`: one article, its classification and its price history per list. */
export function ProductDetailView({ productId }: { productId: number }) {
  const { isAdmin } = useMastersAccess();
  const names = useCatalogNames();
  const [editing, setEditing] = useState(false);
  const product = useQuery({
    queryKey: [...productsKey, "detail", productId],
    queryFn: () => mastersApi.product(productId),
  });
  if (product.isPending) return <Skeleton className="h-40" />;
  if (product.isError) return <ErrorState error={product.error} onRetry={() => void product.refetch()} />;
  const p = product.data;
  const lists = [...new Set(p.prices.map((x) => x.list_number))];
  return (
    <div className="space-y-4">
      <Link
        href={`${MASTERS}/productos`}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:underline"
      >
        <ArrowLeft className="size-4" aria-hidden /> Productos
      </Link>
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <CardTitle>
              {p.source_code} · {p.source_description ?? "Sin descripción"}
            </CardTitle>
            {p.canonical_category_id === null && <Badge tone="warning">Sin categoría</Badge>}
          </div>
          {isAdmin && (
            <Button size="sm" onClick={() => setEditing(true)}>
              Clasificar
            </Button>
          )}
        </CardHeader>
        <CardContent>
          <dl className="grid gap-4 sm:grid-cols-3">
            <Fact label="Rubro en STORE SYS">{p.source_category}</Fact>
            <Fact label="Línea">{p.source_line}</Fact>
            <Fact label="Envase / pack">{[p.source_capacity, p.source_pack].filter(Boolean).join(" · ") || null}</Fact>
            <Fact label="Categoría">
              {names.category(p.canonical_category_id) ?? "Sin categoría"}
              <span className="block text-xs text-muted-foreground">{CATEGORY_ORIGINS[p.category_origin]}</span>
            </Fact>
            <Fact label="Tipo">{PRODUCT_KINDS[p.product_kind] ?? p.product_kind}</Fact>
            <Fact label="Familia">{names.family(p.family_id)}</Fact>
            <Fact label="Entra en los análisis">{yesNo(p.analytics_eligible)}</Fact>
            <Fact label="Activo">{yesNo(p.active)}</Fact>
            <Fact label="Precio del maestro hoy">{p.master_price !== null ? formatMoney(p.master_price) : null}</Fact>
          </dl>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Historia de precios</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {lists.length === 0 ? (
            <p className="text-sm text-muted-foreground">Sin precios cargados.</p>
          ) : (
            lists.map((number) => {
              const rows = p.prices.filter((x) => x.list_number === number);
              return (
                <div key={number}>
                  <h3 className="mb-1 text-sm font-medium">
                    Lista {number} · {rows[0].price_list_name}
                  </h3>
                  <Table stacked>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Desde</TableHead>
                        <TableHead>Hasta</TableHead>
                        <TableHead className="text-right">Precio</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {rows.map((r) => (
                        <TableRow key={r.id} className={r.voided ? "text-muted-foreground line-through" : undefined}>
                          <TableCell data-label="Desde">{formatDate(r.valid_from)}</TableCell>
                          <TableCell data-label="Hasta">{r.valid_to ? formatDate(r.valid_to) : "Vigente"}</TableCell>
                          <TableCell data-label="Precio" className="text-right tabular-nums">
                            {formatMoney(r.price)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              );
            })
          )}
        </CardContent>
      </Card>
      {editing && (
        <ProductDialog
          product={p}
          categories={names.categories}
          families={names.families}
          onClose={() => setEditing(false)}
        />
      )}
    </div>
  );
}
