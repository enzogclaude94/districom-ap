"use client";

import { useQuery } from "@tanstack/react-query";
import { UserRound } from "lucide-react";
import Link from "next/link";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { MappingStatus } from "@/lib/api/mappings";
import { mastersApi, mastersQueryKey, type EmployeeMapping } from "@/lib/api/masters";
import { STATUS_LABELS, STATUS_TONES, SYSTEM_LABELS, mappingHref, n } from "@/modules/commercial/mappings/labels";

import { useMastersAccess } from "./shared";

function Code({ mapping }: { mapping: EmployeeMapping }) {
  return (
    <span className="block">
      <span className="text-xs text-muted-foreground">
        {mapping.kind_label ?? SYSTEM_LABELS[mapping.source_system]}{" "}
      </span>
      <Link href={mappingHref(mapping.id)} className="font-medium underline-offset-2 hover:underline">
        {mapping.external_id}
      </Link>
      {mapping.external_label && mapping.external_label !== mapping.external_id && (
        <span className="text-xs text-muted-foreground"> · {mapping.external_label}</span>
      )}
    </span>
  );
}

/**
 * `/comercial/datos/maestros/vendedores`: the people are the platform's employees (with the Preventista function);
 * their STORE SYS and AXUM codes are mappings. Nothing is created or linked here: codes are decided in Mapeos and
 * people are edited in Empleados.
 */
export function SalespeopleView() {
  const { isPlatformAdmin } = useMastersAccess();
  const data = useQuery({ queryKey: [...mastersQueryKey, "salespeople"], queryFn: mastersApi.salespeople });
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <CardTitle>Vendedores y preventistas</CardTitle>
            <p className="text-sm text-muted-foreground">
              Son empleados de la plataforma. Sus códigos de STORE SYS y AXUM se vinculan en Mapeos; DistriCom nunca los
              vincula solo.
            </p>
          </div>
          {isPlatformAdmin ? (
            <Link href="/empleados" className={buttonVariants({ variant: "secondary", size: "sm" })}>
              Dar de alta o editar en Empleados
            </Link>
          ) : (
            <p className="max-w-xs text-xs text-muted-foreground">
              El alta y la edición de empleados las hace un administrador de la plataforma, en Empleados.
            </p>
          )}
        </CardHeader>
        <CardContent>
          {data.isPending ? (
            <Skeleton className="h-20" />
          ) : data.isError ? (
            <ErrorState error={data.error} onRetry={() => void data.refetch()} />
          ) : data.data.items.length === 0 ? (
            <EmptyState
              icon={UserRound}
              title="Todavía no hay vendedores vinculados"
              description="Aparecen cuando un empleado tiene la función Preventista o cuando se vincula un código de vendedor en Mapeos."
            />
          ) : (
            <Table stacked>
              <TableHeader>
                <TableRow>
                  <TableHead>Empleado</TableHead>
                  <TableHead>Función Preventista</TableHead>
                  <TableHead>Códigos</TableHead>
                  <TableHead className="text-right">Clientes asignados hoy</TableHead>
                  <TableHead>Estado</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.data.items.map((s) => (
                  <TableRow key={s.id}>
                    <TableCell data-label="Empleado" className="font-medium">
                      {s.name}
                    </TableCell>
                    <TableCell data-label="Función Preventista">
                      {s.is_salesperson ? <Badge tone="success">Sí</Badge> : <Badge tone="warning">No</Badge>}
                    </TableCell>
                    <TableCell data-label="Códigos">
                      {s.mappings.length === 0 ? (
                        <Link
                          href="/comercial/datos/mapeos"
                          className="text-sm text-warning underline-offset-2 hover:underline"
                        >
                          Sin códigos vinculados: vincular en Mapeos
                        </Link>
                      ) : (
                        s.mappings.map((m) => <Code key={m.id} mapping={m} />)
                      )}
                    </TableCell>
                    <TableCell data-label="Clientes asignados hoy" className="text-right tabular-nums">
                      {n(s.customers)}
                    </TableCell>
                    <TableCell data-label="Estado">
                      {s.active ? <Badge tone="success">Activo</Badge> : <Badge>Inactivo</Badge>}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      {data.data && data.data.unlinked.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Códigos de vendedor sin vincular</CardTitle>
            <p className="text-sm text-muted-foreground">
              Los clientes que el maestro asigna con estos códigos quedan sin vendedor hasta decidirlos en Mapeos.
            </p>
          </CardHeader>
          <CardContent>
            <Table stacked>
              <TableHeader>
                <TableRow>
                  <TableHead>Código</TableHead>
                  <TableHead>Estado</TableHead>
                  <TableHead className="text-right">Clientes con este código</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.data.unlinked.map((m) => (
                  <TableRow key={m.id}>
                    <TableCell data-label="Código">
                      <Code mapping={m} />
                    </TableCell>
                    <TableCell data-label="Estado">
                      <Badge tone={STATUS_TONES[m.status as MappingStatus]}>
                        {STATUS_LABELS[m.status as MappingStatus] ?? m.status}
                      </Badge>
                    </TableCell>
                    <TableCell data-label="Clientes con este código" className="text-right tabular-nums">
                      {n(m.customers)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
