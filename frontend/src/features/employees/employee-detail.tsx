"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Lock, UserX } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api/client";
import { employeesApi, employeesQueryKey, type Employee } from "@/lib/api/employees";
import { formatDate, todayIso } from "@/lib/format";

import { EmployeeForm } from "./employee-form";
import { EmployeeFunctions } from "./employee-functions";

function errorMessage(error: unknown) {
  return error instanceof ApiError ? error.message : "No se pudo completar la acción.";
}

function StatusCard({ employee }: { employee: Employee }) {
  const queryClient = useQueryClient();
  const [endDate, setEndDate] = useState(todayIso);
  const onSuccess = (updated: Employee) => {
    queryClient.setQueryData([...employeesQueryKey, employee.id], updated);
    void queryClient.invalidateQueries({ queryKey: [...employeesQueryKey, "list"] });
  };
  const deactivate = useMutation({ mutationFn: () => employeesApi.deactivate(employee.id, endDate), onSuccess });
  const reactivate = useMutation({ mutationFn: () => employeesApi.reactivate(employee.id), onSuccess });
  const error = deactivate.error ?? reactivate.error;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          Estado
          <Badge tone={employee.active ? "success" : "neutral"}>{employee.active ? "Activo" : "Dado de baja"}</Badge>
        </CardTitle>
        <CardDescription>
          {employee.active
            ? "La baja es lógica: el empleado y su historia se conservan, y sus funciones vigentes terminan en la fecha de baja."
            : "Al reactivarlo, las funciones no se reabren: asignale un período nuevo."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {employee.active ? (
          <div className="flex flex-wrap items-end gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="end_date">Último día de trabajo</Label>
              <Input
                id="end_date"
                type="date"
                className="w-44"
                value={endDate}
                onChange={(event) => setEndDate(event.target.value)}
              />
            </div>
            <Button variant="danger" disabled={!endDate || deactivate.isPending} onClick={() => deactivate.mutate()}>
              <UserX aria-hidden />
              {deactivate.isPending ? "Dando de baja…" : "Dar de baja"}
            </Button>
          </div>
        ) : (
          <Button variant="secondary" disabled={reactivate.isPending} onClick={() => reactivate.mutate()}>
            {reactivate.isPending ? "Reactivando…" : "Reactivar"}
          </Button>
        )}
        {error && (
          <p role="alert" className="flex items-center gap-2 text-sm text-danger">
            <AlertCircle className="size-4" aria-hidden />
            {errorMessage(error)}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

export function EmployeeDetail({ employeeId }: { employeeId: number }) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: [...employeesQueryKey, employeeId],
    queryFn: () => employeesApi.get(employeeId),
  });
  const update = useMutation({
    mutationFn: (input: Parameters<typeof employeesApi.update>[1]) => employeesApi.update(employeeId, input),
    onSuccess: (updated) => {
      queryClient.setQueryData([...employeesQueryKey, employeeId], updated);
      void queryClient.invalidateQueries({ queryKey: [...employeesQueryKey, "list"] });
    },
  });

  if (query.isPending) {
    return (
      <div className="space-y-4" aria-busy="true">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }
  if (query.error instanceof ApiError && query.error.status === 403) {
    return <EmptyState icon={Lock} title="Sin permisos" description="Solo los administradores pueden ver empleados." />;
  }
  if (query.error instanceof ApiError && query.error.status === 404) {
    return (
      <EmptyState
        title="El empleado no existe"
        description="Puede que el enlace sea incorrecto."
        action={
          <Link href="/empleados" className="text-sm font-medium text-primary hover:underline">
            Volver a empleados
          </Link>
        }
      />
    );
  }
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;

  const employee = query.data;
  return (
    <div className="space-y-6">
      <PageHeader
        title={employee.full_name}
        description={`Ingreso: ${formatDate(employee.hire_date)}`}
        actions={
          <Link href="/empleados" className="text-sm font-medium text-primary hover:underline">
            ← Empleados
          </Link>
        }
      />
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Datos</CardTitle>
            <CardDescription>Identidad única de la persona en toda la plataforma.</CardDescription>
          </CardHeader>
          <CardContent>
            <EmployeeForm
              key={`${employee.id}-${employee.full_name}-${employee.hire_date}-${employee.phone}`}
              employee={employee}
              submitLabel="Guardar cambios"
              pending={update.isPending}
              error={update.error}
              onSubmit={(input) => update.mutate(input)}
            />
            {update.isSuccess && !update.isPending && (
              <p role="status" className="mt-3 text-sm text-success">
                Cambios guardados.
              </p>
            )}
          </CardContent>
        </Card>
        <StatusCard employee={employee} />
      </div>
      <EmployeeFunctions employee={employee} />
    </div>
  );
}
