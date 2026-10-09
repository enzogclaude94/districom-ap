"use client";

import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/cn";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";

import type { FuelLoadRow, FuelSummary } from "./api";
import { correctionNote, liters, origin, pricePerLiter, STATE_LABELS, STATE_TONES } from "./labels";

const odometer = (km: number | null) => (km === null ? "—" : `${km.toLocaleString("es-AR")} km`);
const struck = (row: FuelLoadRow) => row.state === "VOIDED" || row.state === "REVERSED";

/** The summary of the fuel in effect for the filters, exactly as the backend computed it. */
export function FuelSummaryBar({ summary }: { summary: FuelSummary }) {
  const items = [
    { label: "Litros", value: liters(summary.liters) },
    { label: "Gasto", value: formatMoney(summary.amount) },
    {
      label: "Precio promedio",
      value: summary.weighted_price_per_liter === null ? "—" : `${formatMoney(summary.weighted_price_per_liter)}/l`,
    },
  ];
  return (
    <section aria-label="Resumen del combustible vigente" data-testid="fuel-summary">
      <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {items.map((item) => (
          <Card key={item.label}>
            <CardContent className="pt-4">
              <dt className="text-sm text-muted-foreground">{item.label}</dt>
              <dd className="text-xl font-semibold tabular-nums">{item.value}</dd>
            </CardContent>
          </Card>
        ))}
      </dl>
      <p className="mt-2 text-xs text-muted-foreground">
        Suma lo publicado y vigente: no incluye cargas de rendiciones abiertas, anuladas ni revertidas. Una rendición en
        corrección sigue sumando su cierre vigente hasta el nuevo cierre. Precio promedio = gasto ÷ litros.
      </p>
    </section>
  );
}

function StateBadge({ row }: { row: FuelLoadRow }) {
  return <Badge tone={STATE_TONES[row.state]}>{STATE_LABELS[row.state]}</Badge>;
}

function OriginLink({ row }: { row: FuelLoadRow }) {
  const { label, href } = origin(row);
  return (
    <Link href={href} className="text-primary hover:underline">
      {label}
    </Link>
  );
}

function Vehicle({ row }: { row: FuelLoadRow }) {
  return (
    <>
      <span className="block font-medium tabular-nums">
        {row.vehicle_plate}
        {!row.vehicle_active && <span className="font-normal text-muted-foreground"> (desactivado)</span>}
      </span>
      <span className="block text-xs text-muted-foreground">{row.vehicle_description}</span>
    </>
  );
}

function Employee({ row }: { row: FuelLoadRow }) {
  return (
    <>
      {row.employee_name}
      {!row.employee_active && <span className="text-muted-foreground"> (inactivo)</span>}
    </>
  );
}

function Note({ row }: { row: FuelLoadRow }) {
  const note = correctionNote(row);
  return note ? <p className="text-xs text-warning">{note}</p> : null;
}

/** Desktop: one row per load. `withVehicle` is off inside a vehicle's own page. */
export function FuelLoadTable({ rows, withVehicle = true }: { rows: FuelLoadRow[]; withVehicle?: boolean }) {
  return (
    <div className="hidden md:block" data-testid="fuel-table">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Fecha</TableHead>
            {withVehicle && <TableHead>Vehículo</TableHead>}
            <TableHead>Empleado</TableHead>
            <TableHead className="text-right">Litros</TableHead>
            <TableHead className="text-right">Precio</TableHead>
            <TableHead className="text-right">Importe</TableHead>
            <TableHead className="text-right">Odómetro</TableHead>
            <TableHead>Origen</TableHead>
            <TableHead>Estado</TableHead>
            {withVehicle && <TableHead>Registró</TableHead>}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.id} data-testid={`fuel-row-${row.id}`}>
              <TableCell className="whitespace-nowrap tabular-nums">{formatDate(row.business_date)}</TableCell>
              {withVehicle && (
                <TableCell className="whitespace-nowrap">
                  <Vehicle row={row} />
                </TableCell>
              )}
              <TableCell>
                <Employee row={row} />
              </TableCell>
              <TableCell className={cn("text-right whitespace-nowrap tabular-nums", struck(row) && "line-through")}>
                {liters(row.liters)}
              </TableCell>
              <TableCell className="text-right whitespace-nowrap tabular-nums">
                {pricePerLiter(row.price_per_liter)}
              </TableCell>
              <TableCell className={cn("text-right whitespace-nowrap tabular-nums", struck(row) && "line-through")}>
                {formatMoney(row.amount)}
              </TableCell>
              <TableCell className="text-right whitespace-nowrap tabular-nums">{odometer(row.odometer_km)}</TableCell>
              <TableCell className="min-w-44">
                <OriginLink row={row} />
                <Note row={row} />
              </TableCell>
              <TableCell>
                <StateBadge row={row} />
              </TableCell>
              {withVehicle && (
                <TableCell className="text-sm">
                  <span className="block whitespace-nowrap">{row.created_by_name}</span>
                  <span className="block text-xs whitespace-nowrap text-muted-foreground tabular-nums">
                    {formatDateTime(row.created_at)}
                  </span>
                </TableCell>
              )}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

/** Mobile: one card per load, nothing scrolls sideways. */
export function FuelLoadCards({ rows, withVehicle = true }: { rows: FuelLoadRow[]; withVehicle?: boolean }) {
  return (
    <ul className="space-y-3 md:hidden" data-testid="fuel-cards" aria-label="Cargas de combustible">
      {rows.map((row) => (
        <li key={row.id} className="rounded-md border border-border p-3 text-sm" data-testid={`fuel-card-${row.id}`}>
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="text-muted-foreground tabular-nums">{formatDate(row.business_date)}</p>
              {withVehicle && <Vehicle row={row} />}
            </div>
            <StateBadge row={row} />
          </div>
          <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1">
            <dt className="text-muted-foreground">Litros</dt>
            <dd className={cn("text-right tabular-nums", struck(row) && "line-through")}>{liters(row.liters)}</dd>
            <dt className="text-muted-foreground">Precio</dt>
            <dd className="text-right tabular-nums">{pricePerLiter(row.price_per_liter)}</dd>
            <dt className="text-muted-foreground">Importe</dt>
            <dd className={cn("text-right font-medium tabular-nums", struck(row) && "line-through")}>
              {formatMoney(row.amount)}
            </dd>
            <dt className="text-muted-foreground">Odómetro</dt>
            <dd className="text-right tabular-nums">{odometer(row.odometer_km)}</dd>
            <dt className="text-muted-foreground">Empleado</dt>
            <dd className="text-right break-words">
              <Employee row={row} />
            </dd>
          </dl>
          <div className="mt-2 break-words">
            <OriginLink row={row} />
            <Note row={row} />
          </div>
          {withVehicle && <p className="mt-1 text-xs text-muted-foreground">Registró {row.created_by_name}</p>}
        </li>
      ))}
    </ul>
  );
}

export function Pager({
  page,
  size,
  shown,
  total,
  onPage,
}: {
  page: number;
  size: number;
  shown: number;
  total: number;
  onPage: (page: number) => void;
}) {
  return (
    <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-sm text-muted-foreground">
      <span className="tabular-nums">
        {page * size + 1}–{page * size + shown} de {total}
      </span>
      <div className="flex gap-2">
        <Button variant="secondary" size="sm" disabled={page === 0} onClick={() => onPage(page - 1)}>
          Anterior
        </Button>
        <Button variant="secondary" size="sm" disabled={(page + 1) * size >= total} onClick={() => onPage(page + 1)}>
          Siguiente
        </Button>
      </div>
    </div>
  );
}
