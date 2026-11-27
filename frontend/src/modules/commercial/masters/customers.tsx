"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Users } from "lucide-react";
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
import { mastersApi, mastersQueryKey, type Customer, type CustomerFilters } from "@/lib/api/masters";
import { formatDate } from "@/lib/format";
import { STATUS_LABELS, STATUS_TONES, SYSTEM_LABELS, mappingHref } from "@/modules/commercial/mappings/labels";
import type { MappingStatus } from "@/lib/api/mappings";

import { MASTERS } from "./labels";
import { EditDialog, Fact, PAGE_SIZE, Pager, selectClass, useMastersAccess, yesNo } from "./shared";

const customersKey = [...mastersQueryKey, "customers"] as const;

function Flags({ customer }: { customer: Pick<Customer, "active" | "is_generic"> }) {
  return (
    <span className="flex flex-wrap gap-1">
      {customer.active ? <Badge tone="success">Activo</Badge> : <Badge>Inactivo</Badge>}
      {customer.is_generic && <Badge tone="info">Genérico</Badge>}
    </span>
  );
}

const tri = (value: string) => (value === "" ? undefined : value === "yes");

/** `/comercial/datos/maestros/clientes`: the STORE SYS customers as DistriCom keeps them, searched in the backend. */
export function CustomerList() {
  const ids = { q: useId(), state: useId(), generic: useId(), zone: useId(), seller: useId() };
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [state, setState] = useState("yes");
  const [generic, setGeneric] = useState("");
  const [zone, setZone] = useState("");
  const [seller, setSeller] = useState("");
  const [page, setPage] = useState(0);
  const zones = useQuery({ queryKey: [...mastersQueryKey, "zones"], queryFn: mastersApi.zones });
  const sellers = useQuery({ queryKey: [...mastersQueryKey, "salespeople"], queryFn: mastersApi.salespeople });
  const filters: CustomerFilters = {
    q: q || undefined,
    active: tri(state),
    include_inactive: state === "" ? true : undefined,
    is_generic: tri(generic),
    zone_id: zone ? Number(zone) : undefined,
    salesperson_id: seller ? Number(seller) : undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  };
  const list = useQuery({
    queryKey: [...customersKey, "list", filters],
    queryFn: () => mastersApi.customers(filters),
    placeholderData: keepPreviousData,
  });
  const items = list.data?.items ?? [];
  const pick = (set: (v: string) => void) => (e: { target: { value: string } }) => {
    set(e.target.value);
    setPage(0);
  };

  return (
    <Card>
      <CardHeader className="space-y-3">
        <CardTitle>Clientes</CardTitle>
        <p className="text-sm text-muted-foreground">
          Los datos del cliente, su zona y su vendedor vienen del maestro de clientes de STORE SYS y no se editan acá.
          Un admin solo decide si es genérico o si está activo.
        </p>
        <div className="grid gap-3 sm:grid-cols-5">
          <form
            className="space-y-1.5 sm:col-span-2"
            onSubmit={(e) => {
              e.preventDefault();
              setQ(search.trim());
              setPage(0);
            }}
          >
            <Label htmlFor={ids.q}>Buscar por nombre o código STORE SYS</Label>
            <Input id={ids.q} value={search} maxLength={100} onChange={(e) => setSearch(e.target.value)} />
          </form>
          <div className="space-y-1.5">
            <Label htmlFor={ids.state}>Estado</Label>
            <select id={ids.state} className={selectClass} value={state} onChange={pick(setState)}>
              <option value="yes">Activos</option>
              <option value="no">Inactivos</option>
              <option value="">Todos</option>
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.generic}>Genéricos</Label>
            <select id={ids.generic} className={selectClass} value={generic} onChange={pick(setGeneric)}>
              <option value="">Todos</option>
              <option value="yes">Solo genéricos</option>
              <option value="no">Sin genéricos</option>
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.zone}>Zona vigente</Label>
            <select id={ids.zone} className={selectClass} value={zone} onChange={pick(setZone)}>
              <option value="">Todas</option>
              {(zones.data ?? []).map((z) => (
                <option key={z.id} value={z.id}>
                  {z.display_name}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5 sm:col-start-5">
            <Label htmlFor={ids.seller}>Vendedor vigente</Label>
            <select id={ids.seller} className={selectClass} value={seller} onChange={pick(setSeller)}>
              <option value="">Todos</option>
              {(sellers.data?.items ?? []).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {list.isPending ? (
          <div className="space-y-2" aria-label="Cargando los clientes">
            <Skeleton className="h-10" />
            <Skeleton className="h-10" />
          </div>
        ) : list.isError ? (
          <ErrorState error={list.error} onRetry={() => void list.refetch()} />
        ) : items.length === 0 ? (
          <EmptyState
            icon={Users}
            title="No hay clientes con estos filtros"
            description="Los clientes llegan con el maestro de clientes de STORE SYS."
          />
        ) : (
          <>
            <Table stacked>
              <TableHeader>
                <TableRow>
                  <TableHead>Cliente</TableHead>
                  <TableHead>Código STORE SYS</TableHead>
                  <TableHead>Zona</TableHead>
                  <TableHead>Vendedor</TableHead>
                  <TableHead>Estado</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((c) => (
                  <TableRow key={c.id}>
                    <TableCell data-label="Cliente">
                      <Link
                        href={`${MASTERS}/clientes/${c.id}`}
                        className="font-medium underline-offset-2 hover:underline"
                      >
                        {c.display_name}
                      </Link>
                    </TableCell>
                    <TableCell data-label="Código STORE SYS" className="tabular-nums">
                      {c.store_sys_code ?? "—"}
                    </TableCell>
                    <TableCell data-label="Zona">{c.zone?.name ?? "Sin zona"}</TableCell>
                    <TableCell data-label="Vendedor">
                      <SellerName seller={c.salesperson} />
                    </TableCell>
                    <TableCell data-label="Estado">
                      <Flags customer={c} />
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

function SellerName({ seller }: { seller: Customer["salesperson"] }) {
  if (!seller) return <>Sin vendedor</>;
  if (seller.salesperson_name) return <>{seller.salesperson_name}</>;
  return (
    <span>
      {seller.source_label ?? "—"}
      <span className="block text-xs text-warning">Código sin vincular</span>
    </span>
  );
}

function FlagsDialog({ customer, onClose }: { customer: Customer; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [isGeneric, setIsGeneric] = useState(customer.is_generic);
  const [active, setActive] = useState(customer.active);
  const save = useMutation({
    mutationFn: (reason: string) =>
      mastersApi.setCustomerFlags(customer.id, {
        is_generic: isGeneric,
        active,
        reason,
        expected_updated_at: customer.updated_at,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: customersKey });
      onClose();
    },
  });
  return (
    <EditDialog
      title={`Marcas de ${customer.display_name}`}
      description="Genérico saca al cliente de los universos de clientes (Consumidor Final y similares). Ningún maestro cambia estas marcas."
      onClose={onClose}
      onSave={(reason) => save.mutate(reason)}
      pending={save.isPending}
      error={save.error}
    >
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={isGeneric} onChange={(e) => setIsGeneric(e.target.checked)} />
        Cliente genérico
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
        Activo
      </label>
    </EditDialog>
  );
}

/** `/comercial/datos/maestros/clientes/[id]`: one customer, its codes and its zone and seller history. */
export function CustomerDetailView({ customerId }: { customerId: number }) {
  const { isAdmin } = useMastersAccess();
  const [editing, setEditing] = useState(false);
  const customer = useQuery({
    queryKey: [...customersKey, "detail", customerId],
    queryFn: () => mastersApi.customer(customerId),
  });
  const history = useQuery({
    queryKey: [...customersKey, "history", customerId],
    queryFn: () => mastersApi.customerHistory(customerId),
  });
  if (customer.isPending) return <Skeleton className="h-40" />;
  if (customer.isError) return <ErrorState error={customer.error} onRetry={() => void customer.refetch()} />;
  const c = customer.data;
  const contact = "tax_id" in c; // only an operator or admin receives these keys
  return (
    <div className="space-y-4">
      <Link
        href={`${MASTERS}/clientes`}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:underline"
      >
        <ArrowLeft className="size-4" aria-hidden /> Clientes
      </Link>
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <CardTitle>{c.display_name}</CardTitle>
            <Flags customer={c} />
          </div>
          {isAdmin && (
            <Button variant="secondary" size="sm" onClick={() => setEditing(true)}>
              Cambiar marcas
            </Button>
          )}
        </CardHeader>
        <CardContent>
          <dl className="grid gap-4 sm:grid-cols-3">
            <Fact label="Razón social">{c.legal_name}</Fact>
            <Fact label="Código STORE SYS">{c.store_sys_code}</Fact>
            <Fact label="Códigos AXUM">
              {history.data?.mappings
                .filter((m) => m.source_system === "AXUM")
                .map((m) => m.external_id)
                .join(", ") || null}
            </Fact>
            <Fact label="Zona vigente">{c.zone?.name ?? "Sin zona"}</Fact>
            <Fact label="Vendedor vigente">
              <SellerName seller={c.salesperson} />
            </Fact>
            <Fact label="Última aparición en el maestro">{c.last_seen_on ? formatDate(c.last_seen_on) : null}</Fact>
            <Fact label="Genérico">{yesNo(c.is_generic)}</Fact>
            <Fact label="Activo">{yesNo(c.active)}</Fact>
            {contact && (
              <>
                <Fact label="CUIT">{c.tax_id}</Fact>
                <Fact label="Dirección">{c.address}</Fact>
                <Fact label="Teléfono">{c.phone}</Fact>
              </>
            )}
          </dl>
          <p className="mt-4 text-xs text-muted-foreground">
            Nombre, razón social, CUIT, dirección, teléfono, zona y vendedor los define el maestro de STORE SYS. Para
            cambiarlos, corregilos allá y volvé a importar el maestro.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Códigos vinculados</CardTitle>
        </CardHeader>
        <CardContent>
          {history.isPending ? (
            <Skeleton className="h-10" />
          ) : history.isError ? (
            <ErrorState error={history.error} onRetry={() => void history.refetch()} />
          ) : history.data.mappings.length === 0 ? (
            <p className="text-sm text-muted-foreground">Ningún código externo vinculado.</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {history.data.mappings.map((m) => (
                <li key={m.id} className="flex flex-wrap items-center gap-2">
                  <span>{m.kind_label ?? SYSTEM_LABELS[m.source_system]}</span>
                  <Link href={mappingHref(m.id)} className="font-medium underline-offset-2 hover:underline">
                    {m.external_id}
                  </Link>
                  <Badge tone={STATUS_TONES[m.status as MappingStatus]}>
                    {STATUS_LABELS[m.status as MappingStatus] ?? m.status}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-3 text-xs text-muted-foreground">Los vínculos se deciden en Mapeos.</p>
        </CardContent>
      </Card>

      {history.data && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Historia de zona</CardTitle>
            </CardHeader>
            <CardContent>
              {history.data.zones.length === 0 ? (
                <p className="text-sm text-muted-foreground">El maestro nunca le asignó zona.</p>
              ) : (
                <Table stacked>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Zona</TableHead>
                      <TableHead>Desde</TableHead>
                      <TableHead>Hasta</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {history.data.zones.map((z) => (
                      <TableRow key={z.id} className={z.voided ? "text-muted-foreground line-through" : undefined}>
                        <TableCell data-label="Zona">
                          {z.zone.name}
                          {z.voided && <span className="sr-only"> (anulada)</span>}
                        </TableCell>
                        <TableCell data-label="Desde">{formatDate(z.valid_from)}</TableCell>
                        <TableCell data-label="Hasta">{z.valid_to ? formatDate(z.valid_to) : "Vigente"}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Historia de vendedor</CardTitle>
            </CardHeader>
            <CardContent>
              {history.data.salespeople.length === 0 ? (
                <p className="text-sm text-muted-foreground">El maestro nunca le asignó vendedor.</p>
              ) : (
                <Table stacked>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Vendedor</TableHead>
                      <TableHead>Según STORE SYS</TableHead>
                      <TableHead>Desde</TableHead>
                      <TableHead>Hasta</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {history.data.salespeople.map((s) => (
                      <TableRow key={s.id} className={s.voided ? "text-muted-foreground line-through" : undefined}>
                        <TableCell data-label="Vendedor">{s.salesperson_name ?? "Sin vincular"}</TableCell>
                        <TableCell data-label="Según STORE SYS">
                          {s.mapping_id ? (
                            <Link href={mappingHref(s.mapping_id)} className="underline-offset-2 hover:underline">
                              {s.source_label ?? "—"}
                            </Link>
                          ) : (
                            (s.source_label ?? "—")
                          )}
                        </TableCell>
                        <TableCell data-label="Desde">{formatDate(s.valid_from)}</TableCell>
                        <TableCell data-label="Hasta">{s.valid_to ? formatDate(s.valid_to) : "Vigente"}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        Configurar una zona no reasigna clientes: la zona y el vendedor de cada cliente los asigna el maestro.
      </p>
      {editing && <FlagsDialog customer={c} onClose={() => setEditing(false)} />}
    </div>
  );
}
