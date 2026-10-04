"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Lock, Plus, Users } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ApiError } from "@/lib/api/client";
import { employeesApi, employeesQueryKey } from "@/lib/api/employees";
import { formatDate } from "@/lib/format";

const PAGE_SIZE = 50;
const STATUS_FILTERS = [
  { value: "active", label: "Activos", active: true },
  { value: "inactive", label: "Dados de baja", active: false },
  { value: "all", label: "Todos", active: undefined },
] as const;

type StatusFilter = (typeof STATUS_FILTERS)[number]["value"];

export function NewEmployeeButton() {
  return (
    <Link
      href="/empleados/nuevo"
      className="inline-flex h-9 items-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 [&_svg]:size-4"
    >
      <Plus aria-hidden />
      Nuevo empleado
    </Link>
  );
}

export function EmployeeList() {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<StatusFilter>("active");
  const [page, setPage] = useState(0);
  const filters = {
    q: search.trim() || undefined,
    active: STATUS_FILTERS.find((filter) => filter.value === status)?.active,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  };
  const query = useQuery({
    queryKey: [...employeesQueryKey, "list", filters],
    queryFn: () => employeesApi.list(filters),
    placeholderData: keepPreviousData,
  });

  if (query.error instanceof ApiError && query.error.status === 403) {
    return (
      <EmptyState
        icon={Lock}
        title="Sin permisos"
        description="Solo los administradores de DistriCom pueden administrar empleados."
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Input
          type="search"
          aria-label="Buscar por nombre"
          placeholder="Buscar por nombre…"
          className="max-w-xs"
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
            setPage(0);
          }}
        />
        <div role="group" aria-label="Estado" className="flex gap-1">
          {STATUS_FILTERS.map((filter) => (
            <Button
              key={filter.value}
              size="sm"
              variant={status === filter.value ? "primary" : "secondary"}
              aria-pressed={status === filter.value}
              onClick={() => {
                setStatus(filter.value);
                setPage(0);
              }}
            >
              {filter.label}
            </Button>
          ))}
        </div>
      </div>

      {query.isPending ? (
        <div className="space-y-2" aria-busy="true">
          {Array.from({ length: 5 }, (_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : query.data.items.length === 0 ? (
        <EmptyState
          icon={Users}
          title={filters.q ? "No hay empleados con ese nombre" : "Todavía no hay empleados"}
          description={
            filters.q
              ? "Probá con otra búsqueda o cambiá el filtro de estado."
              : "Cargá a las personas que trabajan en la empresa. Cada una existe una sola vez en DistriCom y puede tener varias funciones."
          }
          action={filters.q ? undefined : <NewEmployeeButton />}
        />
      ) : (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Nombre</TableHead>
                  <TableHead>Funciones vigentes</TableHead>
                  <TableHead>Ingreso</TableHead>
                  <TableHead className="text-right">Estado</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {query.data.items.map((employee) => {
                  const current = employee.functions.filter((fn) => fn.current);
                  return (
                    <TableRow key={employee.id}>
                      <TableCell>
                        <Link href={`/empleados/${employee.id}`} className="font-medium text-primary hover:underline">
                          {employee.full_name}
                        </Link>
                      </TableCell>
                      <TableCell>
                        {current.length === 0 ? (
                          <span className="text-muted-foreground">Sin funciones</span>
                        ) : (
                          <div className="flex flex-wrap gap-1">
                            {current.map((fn) => (
                              <Badge key={fn.id} tone="info">
                                {fn.label}
                              </Badge>
                            ))}
                          </div>
                        )}
                      </TableCell>
                      <TableCell className="tabular-nums">{formatDate(employee.hire_date)}</TableCell>
                      <TableCell className="text-right">
                        <Badge tone={employee.active ? "success" : "neutral"}>
                          {employee.active ? "Activo" : "Dado de baja"}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {query.data && query.data.total > PAGE_SIZE && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span className="tabular-nums">
            {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, query.data.total)} de {query.data.total}
          </span>
          <div className="flex gap-2">
            <Button size="sm" variant="secondary" disabled={page === 0} onClick={() => setPage(page - 1)}>
              Anterior
            </Button>
            <Button
              size="sm"
              variant="secondary"
              disabled={(page + 1) * PAGE_SIZE >= query.data.total}
              onClick={() => setPage(page + 1)}
            >
              Siguiente
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
