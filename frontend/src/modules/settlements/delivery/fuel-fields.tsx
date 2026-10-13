"use client";

import { useQuery } from "@tanstack/react-query";
import { useId } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { vehiclesApi, vehiclesQueryKey } from "@/lib/api/vehicles";
import { formatMoney } from "@/lib/format";

import { deliveryApi, deliveryQueryKey, type FuelLoad } from "./api";
import { SelectField } from "./form";
import { estimateAmount, formatQuantity, parseDecimal, parseOdometer } from "./fuel";

export interface FuelForm {
  vehicle: number | null;
  employee: number | null;
  liters: string;
  price: string;
  odometer: string;
}

export const fuelForm = (employee: number | null | undefined, load?: FuelLoad | null): FuelForm => ({
  vehicle: load?.vehicle_id ?? null,
  employee: load?.employee_id ?? employee ?? null,
  liters: load ? formatQuantity(load.liters) : "",
  price: load ? formatQuantity(load.price_per_liter) : "",
  odometer: load?.odometer_km != null ? String(load.odometer_km) : "",
});

/** The message to show, or null when the load can be sent. */
export function fuelProblem(form: FuelForm): string | null {
  if (form.vehicle === null) return "Elegí el vehículo.";
  if (form.employee === null) return "Elegí quién cargó.";
  if (!parseDecimal(form.liters, 3)) return "Ingresá los litros (hasta 3 decimales).";
  if (!parseDecimal(form.price, 4)) return "Ingresá el precio por litro (hasta 4 decimales).";
  if (parseOdometer(form.odometer) === undefined) return "El odómetro va en kilómetros enteros.";
  return null;
}

function NumberField({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
}) {
  const id = useId();
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        inputMode="decimal"
        autoComplete="off"
        placeholder={placeholder}
        className="tabular-nums"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}

/**
 * Vehicle (only active ones for a new choice; the current one stays even if deactivated), who loaded (the
 * driver by default), liters, price per liter and optional odometer, with the live estimate. There is no
 * total to type: the backend computes it.
 */
export function FuelFields({
  form,
  onChange,
  current,
}: {
  form: FuelForm;
  onChange: (form: FuelForm) => void;
  current?: FuelLoad | null;
}) {
  const vehicles = useQuery({ queryKey: [...vehiclesQueryKey, "list", "active"], queryFn: () => vehiclesApi.list() });
  const employees = useQuery({ queryKey: [...deliveryQueryKey, "employees"], queryFn: deliveryApi.employees });
  const vehicleOptions = (vehicles.data ?? []).map((v) => ({
    id: v.id,
    name: `${v.plate_display} · ${v.description}`,
  }));
  if (current && !vehicleOptions.some((v) => v.id === current.vehicle_id)) {
    vehicleOptions.push({
      id: current.vehicle_id,
      name: `${current.vehicle_plate} · ${current.vehicle_description}${current.vehicle_active ? "" : " (desactivado)"}`,
    });
  }
  const employeeOptions = (employees.data ?? []).map((e) => ({ id: e.id, name: e.full_name }));
  if (current && !employeeOptions.some((e) => e.id === current.employee_id)) {
    employeeOptions.push({ id: current.employee_id, name: current.employee_name });
  }
  const liters = parseDecimal(form.liters, 3);
  const price = parseDecimal(form.price, 4);
  const set = (change: Partial<FuelForm>) => onChange({ ...form, ...change });

  return (
    <div className="space-y-4 rounded-md border border-border p-3">
      <p className="text-sm font-medium">Combustible</p>
      <SelectField
        label="Vehículo"
        value={form.vehicle}
        onChange={(vehicle) => set({ vehicle })}
        placeholder={vehicles.isPending ? "Cargando vehículos…" : "Elegí un vehículo…"}
        options={vehicleOptions}
      />
      {vehicles.data?.length === 0 && (
        <p className="text-xs text-muted-foreground">
          No hay vehículos activos: un administrador los registra en Vehículos.
        </p>
      )}
      <SelectField
        label="Quién cargó"
        value={form.employee}
        onChange={(employee) => set({ employee })}
        placeholder="Elegí un empleado…"
        options={employeeOptions}
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <NumberField
          label="Litros"
          value={form.liters}
          onChange={(value) => set({ liters: value })}
          placeholder="45,678"
        />
        <NumberField
          label="Precio por litro"
          value={form.price}
          onChange={(value) => set({ price: value })}
          placeholder="1.234,5678"
        />
      </div>
      <NumberField
        label="Odómetro en km (opcional)"
        value={form.odometer}
        onChange={(value) => set({ odometer: value })}
        placeholder="120500"
      />
      <div role="status" className="rounded-md bg-muted px-3 py-2 text-sm">
        {liters && price ? (
          <p className="tabular-nums">
            {formatQuantity(liters)} l × $ {formatQuantity(price)} por litro ={" "}
            <span className="font-medium">Total estimado {formatMoney(estimateAmount(liters, price))}</span>
          </p>
        ) : (
          <p className="text-muted-foreground">Litros × precio por litro = total estimado</p>
        )}
        <p className="text-xs text-muted-foreground">
          El importe oficial lo calcula el sistema al guardar (redondeado al centavo). No se escribe a mano.
        </p>
      </div>
    </div>
  );
}

/** The `fuel` body of a request: everything for a new load, only what changed for a correction. */
export function fuelBody(form: FuelForm, current?: FuelLoad | null): Record<string, unknown> {
  const full = {
    vehicle_id: form.vehicle,
    employee_id: form.employee,
    liters: parseDecimal(form.liters, 3),
    price_per_liter: parseDecimal(form.price, 4),
    odometer_km: parseOdometer(form.odometer),
  };
  if (!current) return full;
  const was = fuelForm(current.employee_id, current);
  const before = {
    vehicle_id: was.vehicle,
    employee_id: was.employee,
    liters: parseDecimal(was.liters, 3),
    price_per_liter: parseDecimal(was.price, 4),
    odometer_km: parseOdometer(was.odometer),
  };
  return Object.fromEntries(
    Object.entries(full).filter(([key, value]) => before[key as keyof typeof before] !== value),
  );
}
