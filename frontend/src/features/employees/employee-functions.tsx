"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ApiError } from "@/lib/api/client";
import { employeesApi, employeesQueryKey, type Employee, type EmployeeFunction } from "@/lib/api/employees";
import { formatDate, todayIso } from "@/lib/format";

function errorMessage(error: unknown) {
  return error instanceof ApiError ? error.message : "No se pudo completar la acción.";
}

function useEmployeeUpdate(employeeId: number) {
  const queryClient = useQueryClient();
  return (updated: Employee) => {
    queryClient.setQueryData([...employeesQueryKey, employeeId], updated);
    void queryClient.invalidateQueries({ queryKey: [...employeesQueryKey, "list"] });
  };
}

function EndFunction({ employeeId, fn }: { employeeId: number; fn: EmployeeFunction }) {
  const [validTo, setValidTo] = useState(todayIso);
  const [open, setOpen] = useState(false);
  const onSuccess = useEmployeeUpdate(employeeId);
  const end = useMutation({ mutationFn: () => employeesApi.endFunction(employeeId, fn.id, validTo), onSuccess });

  if (!open) {
    return (
      <Button size="sm" variant="secondary" onClick={() => setOpen(true)}>
        Cerrar período
      </Button>
    );
  }
  return (
    <div className="flex flex-wrap items-center justify-end gap-2">
      <Input
        type="date"
        aria-label={`Fin de ${fn.label}`}
        className="h-8 w-40"
        value={validTo}
        onChange={(event) => setValidTo(event.target.value)}
      />
      <Button size="sm" disabled={!validTo || end.isPending} onClick={() => end.mutate()}>
        Confirmar
      </Button>
      <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
        Cancelar
      </Button>
      {end.error && (
        <p role="alert" className="w-full text-right text-xs text-danger">
          {errorMessage(end.error)}
        </p>
      )}
    </div>
  );
}

/** Function periods of an employee. Rows are never edited: a period is closed and a new one opened. */
export function EmployeeFunctions({ employee }: { employee: Employee }) {
  const codes = useQuery({ queryKey: [...employeesQueryKey, "function-codes"], queryFn: employeesApi.functionCodes });
  const [functionCode, setFunctionCode] = useState("");
  const [validFrom, setValidFrom] = useState(todayIso);
  const onSuccess = useEmployeeUpdate(employee.id);
  const assign = useMutation({
    mutationFn: () => employeesApi.assignFunction(employee.id, functionCode, validFrom),
    onSuccess: (updated) => {
      onSuccess(updated);
      setFunctionCode("");
    },
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Funciones</CardTitle>
        <CardDescription>
          Una persona puede tener varias funciones a la vez (preventista, repartidor…) y cambiar de función sin
          duplicarse. Cada período queda en la historia.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {employee.functions.length === 0 ? (
          <p className="text-sm text-muted-foreground">Todavía no tiene funciones asignadas.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Función</TableHead>
                <TableHead>Desde</TableHead>
                <TableHead>Hasta</TableHead>
                <TableHead className="text-right">
                  <span className="sr-only">Acciones</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {employee.functions.map((fn) => (
                <TableRow key={fn.id}>
                  <TableCell>
                    <span className="flex items-center gap-2">
                      {fn.label}
                      {fn.current && <Badge tone="info">Vigente</Badge>}
                    </span>
                  </TableCell>
                  <TableCell className="tabular-nums">{formatDate(fn.valid_from)}</TableCell>
                  <TableCell className="tabular-nums">{fn.valid_to ? formatDate(fn.valid_to) : "Sin fin"}</TableCell>
                  <TableCell className="text-right">
                    {fn.valid_to === null && <EndFunction employeeId={employee.id} fn={fn} />}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}

        {employee.active && (
          <form
            className="flex flex-wrap items-end gap-3 border-t border-border pt-4"
            onSubmit={(event) => {
              event.preventDefault();
              assign.mutate();
            }}
          >
            <div className="space-y-1.5">
              <Label htmlFor="function_code">Asignar función</Label>
              <select
                id="function_code"
                className="flex h-9 w-56 rounded-md border border-border bg-surface px-3 text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                value={functionCode}
                onChange={(event) => setFunctionCode(event.target.value)}
              >
                <option value="">Elegí una función…</option>
                {codes.data?.map((code) => (
                  <option key={code.code} value={code.code}>
                    {code.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="valid_from">Desde</Label>
              <Input
                id="valid_from"
                type="date"
                className="w-44"
                value={validFrom}
                onChange={(event) => setValidFrom(event.target.value)}
              />
            </div>
            <Button type="submit" disabled={!functionCode || !validFrom || assign.isPending}>
              {assign.isPending ? "Asignando…" : "Asignar"}
            </Button>
            {assign.error && (
              <p role="alert" className="flex w-full items-center gap-2 text-sm text-danger">
                <AlertCircle className="size-4" aria-hidden />
                {errorMessage(assign.error)}
              </p>
            )}
          </form>
        )}
      </CardContent>
    </Card>
  );
}
