"use client";

import { useState } from "react";

import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/cn";
import { formatMoney } from "@/lib/format";
import type { Account } from "@/modules/settlements/cash/api";

import { settlementPath, type Denomination, type Settlement } from "./api";
import { ActionDialog, selectClass } from "./form";
import { COUNT_EXAMPLE, COUNT_HELP } from "./labels";
import { fromCents, toCents } from "./money";
import { useDeliveryAction } from "./use-delivery-action";

interface Row {
  code: string;
  value: string;
  quantity: string;
  destination: number | null;
}

function initialRows(settlement: Settlement, denominations: Denomination[]): Row[] {
  return denominations
    .filter((denomination) => denomination.active)
    .sort((a, b) => toCents(b.value) - toCents(a.value))
    .map((denomination) => {
      const line = settlement.count_lines.find((l) => l.denomination_code === denomination.code);
      return {
        code: denomination.code,
        value: denomination.value,
        quantity: line ? String(line.quantity) : "",
        destination: line?.destination_account_id ?? denomination.suggested_destination_account_id,
      };
    });
}

const quantityOf = (row: Row) => (/^\d+$/.test(row.quantity.trim()) ? Number(row.quantity.trim()) : 0);

/**
 * Cash count by banknote: quantity and destination box per denomination (the suggested box comes
 * preselected and can be changed). Only active denominations, no coins. Subtotals here are only a visual
 * aid while typing; the board shows the totals the backend computes once it is saved.
 */
export function CountDialog({
  settlement,
  denominations,
  boxes,
  onClose,
}: {
  settlement: Settlement;
  denominations: Denomination[];
  boxes: Account[];
  onClose: () => void;
}) {
  const outlet = settlement.type === "OUTLET";
  const [rows, setRows] = useState(() => initialRows(settlement, denominations));
  const action = useDeliveryAction(`count:${settlement.id}`, onClose);
  const total = rows.reduce((sum, row) => sum + quantityOf(row) * toCents(row.value), 0);
  const update = (code: string, change: Partial<Row>) =>
    setRows((current) => current.map((row) => (row.code === code ? { ...row, ...change } : row)));

  const validate = () => {
    for (const row of rows) {
      const text = row.quantity.trim();
      if (text && !/^\d+$/.test(text)) return `La cantidad de ${formatMoney(row.value)} no es válida.`;
      if (quantityOf(row) > 0 && row.destination === null)
        return `Elegí la caja de los billetes de ${formatMoney(row.value)}.`;
    }
    return null;
  };

  return (
    <ActionDialog
      title={outlet ? "Conteo final de efectivo" : "Conteo de efectivo"}
      description="Cantidad de billetes de cada denominación y la caja donde quedan."
      submitLabel="Guardar conteo"
      action={action}
      onClose={onClose}
      validate={validate}
      build={() => ({
        method: "PUT",
        path: settlementPath(settlement, "/count"),
        body: {
          lines: rows
            .filter((row) => quantityOf(row) > 0)
            .map((row) => ({
              denomination_code: row.code,
              quantity: quantityOf(row),
              destination_account_id: row.destination,
            })),
        },
        summary: `Conteo de ${formatMoney(fromCents(total))}`,
      })}
    >
      {outlet && (
        <div
          className="space-y-1 rounded-md border border-border bg-muted px-3 py-2 text-sm"
          data-testid="count-dialog-help"
        >
          <p className="font-medium">{COUNT_HELP}</p>
          <p className="text-muted-foreground">{COUNT_EXAMPLE}</p>
          <p>
            Ya entregado en esta rendición:{" "}
            <span className="font-medium tabular-nums">{formatMoney(settlement.summary.remitted ?? "0.00")}</span>
          </p>
        </div>
      )}
      <Table stacked>
        <TableHeader>
          <TableRow>
            <TableHead>Billete</TableHead>
            <TableHead>Cantidad</TableHead>
            <TableHead>Caja</TableHead>
            <TableHead className="text-right">Subtotal</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.code}>
              <TableCell data-label="Billete" className="whitespace-nowrap tabular-nums">
                {formatMoney(row.value)}
              </TableCell>
              <TableCell data-label="Cantidad">
                <Input
                  aria-label={`Cantidad de billetes de ${formatMoney(row.value)}`}
                  inputMode="numeric"
                  autoComplete="off"
                  className="w-20 tabular-nums"
                  value={row.quantity}
                  onChange={(event) => update(row.code, { quantity: event.target.value })}
                />
              </TableCell>
              <TableCell data-label="Caja">
                <select
                  aria-label={`Caja de los billetes de ${formatMoney(row.value)}`}
                  className={cn(selectClass, "max-md:w-44")}
                  value={row.destination ?? ""}
                  onChange={(event) =>
                    update(row.code, { destination: event.target.value ? Number(event.target.value) : null })
                  }
                >
                  <option value="">Elegí…</option>
                  {boxes.map((box) => (
                    <option key={box.id} value={box.id}>
                      {box.name}
                    </option>
                  ))}
                </select>
              </TableCell>
              <TableCell data-label="Subtotal" className="text-right whitespace-nowrap tabular-nums">
                {formatMoney(fromCents(quantityOf(row) * toCents(row.value)))}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <p className="flex justify-between border-t border-border pt-3 text-sm font-medium">
        <span>Total contado</span>
        <span className="tabular-nums" data-testid="count-total">
          {formatMoney(fromCents(total))}
        </span>
      </p>
    </ActionDialog>
  );
}
