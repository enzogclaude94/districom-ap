"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, History, Plus, Truck } from "lucide-react";
import Link from "next/link";
import { useId, useState, type FormEvent } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { authApi, currentUserQueryKey } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { vehiclesApi, vehiclesQueryKey, type Vehicle } from "@/lib/api/vehicles";
import { cn } from "@/lib/cn";

import { vehicleHref } from "./vehicle-detail";
import { VehicleHistory } from "./vehicle-history";

const STATUS_FILTERS = [
  { value: "active", label: "Activos" },
  { value: "all", label: "Todos" },
] as const;

type StatusFilter = (typeof STATUS_FILTERS)[number]["value"];

/** Same rule as the backend: spaces, dots and hyphens are ignored, letters are upper case. */
const normalizePlate = (raw: string) => raw.replace(/[\s.-]+/g, "").toUpperCase();
const clean = (text: string) => text.split(/\s+/).filter(Boolean).join(" ");

function vehicleError(error: unknown): string {
  if (!(error instanceof ApiError)) return "No se pudo guardar. Revisá la conexión y volvé a intentar.";
  if (error.code === "vehicle_plate_taken") {
    const inactive =
      typeof error.details === "object" && error.details !== null && "active" in error.details
        ? (error.details as { active: unknown }).active === false
        : false;
    return inactive ? `${error.message} Está desactivado: activalo desde la lista con «Todos».` : error.message;
  }
  if (error.status === 403) return "Solo un administrador de DistriCom administra los vehículos.";
  if (error.status === 0 || error.status >= 500) return "No se pudo guardar. Volvé a intentar.";
  return error.message || "No se pudo guardar.";
}

function Problem({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div role="alert" className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">
      {message}
    </div>
  );
}

function VehicleDialog({
  vehicle,
  onClose,
  onSaved,
}: {
  vehicle: Vehicle | null;
  onClose: () => void;
  onSaved: (vehicle: Vehicle, created: boolean) => void;
}) {
  const plateId = useId();
  const descriptionId = useId();
  const [plate, setPlate] = useState(vehicle?.plate ?? "");
  const [description, setDescription] = useState(vehicle?.description ?? "");
  const [problem, setProblem] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (input: { plate: string; description: string }) => {
      if (!vehicle) return vehiclesApi.create(input);
      return vehiclesApi.update(vehicle.id, {
        ...(input.plate !== vehicle.plate && { plate: input.plate }),
        ...(input.description !== vehicle.description && { description: input.description }),
      });
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const value = { plate: normalizePlate(plate), description: clean(description) };
    if (!value.plate) return setProblem("Ingresá la patente.");
    if (!/^[A-Z0-9]{5,10}$/.test(value.plate))
      return setProblem("La patente tiene que tener entre 5 y 10 letras o números.");
    if (!value.description) return setProblem("Indicá una descripción del vehículo.");
    if (vehicle && value.plate === vehicle.plate && value.description === vehicle.description) return onClose();
    setProblem(null);
    save.mutate(value, { onSuccess: (saved) => onSaved(saved, !vehicle) });
  };

  return (
    <Dialog
      title={vehicle ? `Corregir ${vehicle.plate_display}` : "Nuevo vehículo"}
      description={
        vehicle
          ? "Es el mismo vehículo: lo ya registrado con él lo sigue nombrando, con la patente corregida."
          : "Espacios, puntos y guiones de la patente se ignoran: «AB 123 CD» y «AB-123-CD» son la misma."
      }
      onClose={onClose}
    >
      <form className="space-y-4" onSubmit={submit} noValidate>
        <div className="space-y-1.5">
          <Label htmlFor={plateId}>Patente</Label>
          <Input
            id={plateId}
            maxLength={20}
            autoCapitalize="characters"
            placeholder="AB 123 CD"
            value={plate}
            onChange={(event) => setPlate(event.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={descriptionId}>Descripción</Label>
          <Input
            id={descriptionId}
            maxLength={200}
            placeholder="Por ejemplo Camión Mercedes reparto zona norte"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </div>
        <Problem message={problem ?? (save.error ? vehicleError(save.error) : null)} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button type="submit" disabled={save.isPending}>
            {save.isPending ? "Guardando…" : vehicle ? "Guardar" : "Crear vehículo"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function HistoryDialog({ vehicle, onClose }: { vehicle: Vehicle; onClose: () => void }) {
  const detail = useQuery({ queryKey: [...vehiclesQueryKey, vehicle.id], queryFn: () => vehiclesApi.get(vehicle.id) });
  return (
    <Dialog title={`Historia de ${vehicle.plate_display}`} description={vehicle.description} onClose={onClose}>
      {detail.isPending ? (
        <Skeleton className="h-24 w-full" aria-label="Cargando historia" />
      ) : detail.isError ? (
        <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
      ) : (
        <VehicleHistory history={detail.data.history} />
      )}
    </Dialog>
  );
}

/**
 * Vehículos (R5-1): every user consults them; only a platform admin creates, corrects and (de)activates one.
 * Nothing is deleted: a deactivated vehicle is not offered for new records and the old ones keep it.
 */
export function VehicleList() {
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const isAdmin = me.data?.role === "admin";
  const [status, setStatus] = useState<StatusFilter>("active");
  const list = useQuery({
    queryKey: [...vehiclesQueryKey, "list", status],
    queryFn: () => vehiclesApi.list(status === "all"),
  });
  const [editing, setEditing] = useState<Vehicle | "new" | null>(null);
  const [viewing, setViewing] = useState<Vehicle | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const done = (text: string) => {
    setNotice(text);
    void queryClient.invalidateQueries({ queryKey: vehiclesQueryKey });
  };
  const toggle = useMutation({
    mutationFn: (vehicle: Vehicle) => vehiclesApi.update(vehicle.id, { active: !vehicle.active }),
    onSuccess: (vehicle) =>
      done(
        vehicle.active
          ? `${vehicle.plate_display} está activo otra vez.`
          : `${vehicle.plate_display} quedó desactivado: no se ofrece para registros nuevos.`,
      ),
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div role="group" aria-label="Estado" className="flex gap-1">
          {STATUS_FILTERS.map((filter) => (
            <Button
              key={filter.value}
              size="sm"
              variant={status === filter.value ? "primary" : "secondary"}
              aria-pressed={status === filter.value}
              onClick={() => setStatus(filter.value)}
            >
              {filter.label}
            </Button>
          ))}
        </div>
        {isAdmin ? (
          <Button onClick={() => setEditing("new")}>
            <Plus aria-hidden />
            Nuevo vehículo
          </Button>
        ) : (
          me.data && <p className="text-sm text-muted-foreground">Solo un administrador de DistriCom los modifica.</p>
        )}
      </div>

      <Problem message={toggle.error ? vehicleError(toggle.error) : null} />
      {notice && (
        <p role="status" className="flex items-center gap-2 text-sm text-primary">
          <CheckCircle2 className="size-4" aria-hidden />
          {notice}
        </p>
      )}

      {list.isPending ? (
        <div className="space-y-2" aria-busy="true">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : list.data.length === 0 ? (
        <EmptyState
          icon={Truck}
          title={status === "active" ? "No hay vehículos activos" : "Todavía no hay vehículos"}
          description={
            isAdmin
              ? "Registrá los vehículos de la empresa. Cada uno existe una sola vez en DistriCom."
              : "Pedile a un administrador de DistriCom que los registre."
          }
        />
      ) : (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Patente</TableHead>
                  <TableHead>Descripción</TableHead>
                  <TableHead>Estado</TableHead>
                  <TableHead>
                    <span className="sr-only">Acciones</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.data.map((vehicle) => (
                  <TableRow key={vehicle.id}>
                    <TableCell className={cn("font-medium tabular-nums", !vehicle.active && "text-muted-foreground")}>
                      {vehicle.plate_display}
                    </TableCell>
                    <TableCell className={cn(!vehicle.active && "text-muted-foreground")}>
                      {vehicle.description}
                    </TableCell>
                    <TableCell>
                      {vehicle.active ? <Badge tone="success">Activo</Badge> : <Badge>Desactivado</Badge>}
                    </TableCell>
                    <TableCell className="text-right whitespace-nowrap">
                      <Link
                        href={vehicleHref(vehicle.id)}
                        className="px-2 text-sm text-primary hover:underline"
                        aria-label={`Ficha de ${vehicle.plate_display}`}
                      >
                        Ficha
                      </Link>
                      <Button
                        variant="ghost"
                        size="sm"
                        aria-label={`Historia de ${vehicle.plate_display}`}
                        onClick={() => setViewing(vehicle)}
                      >
                        <History aria-hidden />
                        Historia
                      </Button>
                      {isAdmin && (
                        <>
                          <Button
                            variant="ghost"
                            size="sm"
                            aria-label={`Corregir ${vehicle.plate_display}`}
                            onClick={() => setEditing(vehicle)}
                          >
                            Corregir
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={toggle.isPending}
                            aria-label={`${vehicle.active ? "Desactivar" : "Activar"} ${vehicle.plate_display}`}
                            onClick={() => toggle.mutate(vehicle)}
                          >
                            {vehicle.active ? "Desactivar" : "Activar"}
                          </Button>
                        </>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {editing && (
        <VehicleDialog
          vehicle={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={(vehicle, created) => {
            setEditing(null);
            done(created ? `Vehículo ${vehicle.plate_display} registrado.` : `${vehicle.plate_display} actualizado.`);
          }}
        />
      )}
      {viewing && <HistoryDialog vehicle={viewing} onClose={() => setViewing(null)} />}
    </div>
  );
}
