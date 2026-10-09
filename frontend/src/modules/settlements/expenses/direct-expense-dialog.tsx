"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { useId, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { vehiclesApi, vehiclesQueryKey } from "@/lib/api/vehicles";
import { formatDate, formatMoney, todayIso } from "@/lib/format";
import { parseAmount } from "@/modules/settlements/cash/amount";
import { cashQueryKey, type Account } from "@/modules/settlements/cash/api";
import { deliveryApi, deliveryQueryKey, FUEL_CODE } from "@/modules/settlements/delivery/api";
import { AmountField, FormError, PendingAttempt, SelectField, TextField } from "@/modules/settlements/delivery/form";
import { estimateAmount, formatQuantity, parseDecimal } from "@/modules/settlements/delivery/fuel";
import { FuelFields, fuelBody, fuelProblem, type FuelForm } from "@/modules/settlements/delivery/fuel-fields";
import { useWriteAction, type WriteAction } from "@/modules/settlements/delivery/use-delivery-action";

import { expenseApi, expenseQueryKey, type ExpenseDetail } from "./api";
import { expenseErrorView } from "./errors";

/** Cash boxes and banks in effect: the only accounts an expense can be paid from (the backend checks it again). */
export function payingAccounts(accounts: Account[]) {
  return accounts.filter((account) => account.active && (account.kind === "CASH_BOX" || account.kind === "BANK"));
}

const EMPTY_FUEL: FuelForm = { vehicle: null, employee: null, liters: "", price: "", odometer: "" };

/** What the confirmation shows: every line of the debit, before anything is sent. */
export interface ExpensePreview {
  category: string;
  account: string;
  expenseAccount: string;
  amount: string;
  businessDate: string;
  description: string | null;
  vehicle?: string;
  employee?: string;
  liters?: string;
  price?: string;
  odometer?: string;
}

export function ExpenseConfirmation({ preview }: { preview: ExpensePreview }) {
  const rows: [string, string][] = [
    ["Gasto", preview.category],
    [preview.account, formatMoney(`-${preview.amount}`)],
    [preview.expenseAccount, formatMoney(preview.amount, { signed: true })],
    ["Fecha", formatDate(preview.businessDate)],
  ];
  if (preview.vehicle) rows.push(["Vehículo", preview.vehicle]);
  if (preview.employee) rows.push(["Empleado", preview.employee]);
  if (preview.liters && preview.price) {
    rows.push(["Carga", `${formatQuantity(preview.liters)} l × $ ${formatQuantity(preview.price)} por litro`]);
  }
  if (preview.odometer) rows.push(["Odómetro", `${preview.odometer} km`]);
  if (preview.description) rows.push(["Descripción", preview.description]);
  return (
    <dl className="divide-y divide-border rounded-md border border-border text-sm" data-testid="expense-confirmation">
      {rows.map(([label, value]) => (
        <div key={label} className="flex flex-wrap justify-between gap-x-4 gap-y-0.5 px-3 py-2">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="font-medium tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** One write on an expense: create, reverse or correct it, never duplicated (ADR 008). */
export function useExpenseAction(slot: string, onDone: (expense: ExpenseDetail) => void) {
  return useWriteAction<ExpenseDetail>({
    slot,
    write: (request) => expenseApi.write(request.path, request.body),
    invalidate: [expenseQueryKey, cashQueryKey],
    isUncertain: (error) => Boolean(expenseErrorView(error).uncertain),
    onDone,
  });
}

/**
 * «Registrar gasto» from Cajas (R5-3): an expense paid from one cash box or the bank, today or a past day.
 * Two steps: the form, then an explicit confirmation with the debit and the credit of the ledger, so a
 * financial debit never comes from one accidental click. A fuel expense has no amount to type: liters ×
 * price per liter, computed by the backend; who loaded it is chosen explicitly, never by default.
 */
export function DirectExpenseDialog({
  accounts,
  onClose,
  onDone,
}: {
  accounts: Account[];
  onClose: () => void;
  onDone: (expense: ExpenseDetail) => void;
}) {
  const dateId = useId();
  const payable = payingAccounts(accounts);
  const expenseAccount = accounts.find((account) => account.kind === "EXPENSE")?.name ?? "Gastos operativos";
  const categories = useQuery({ queryKey: [...deliveryQueryKey, "categories"], queryFn: deliveryApi.categories });
  const vehicles = useQuery({ queryKey: [...vehiclesQueryKey, "list", "active"], queryFn: () => vehiclesApi.list() });
  const employees = useQuery({ queryKey: [...deliveryQueryKey, "employees"], queryFn: deliveryApi.employees });

  const [account, setAccount] = useState<number | null>(payable.length === 1 ? payable[0].id : null);
  const [businessDate, setBusinessDate] = useState(todayIso);
  const [categoryId, setCategoryId] = useState<number | null>(null);
  const [amount, setAmount] = useState("");
  const [description, setDescription] = useState("");
  const [fuel, setFuel] = useState<FuelForm>(EMPTY_FUEL);
  const [problem, setProblem] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  const action: WriteAction = useExpenseAction("direct-expense", (expense) => {
    onDone(expense);
    onClose();
  });
  const pending = action.mutation.isPending;
  const accountName = (id: number) => accounts.find((a) => a.id === id)?.name;
  const error = action.mutation.error ? expenseErrorView(action.mutation.error, accountName, businessDate) : null;

  const category = (categories.data ?? []).find((c) => c.id === categoryId);
  const isFuel = category?.code === FUEL_CODE;
  const liters = parseDecimal(fuel.liters, 3);
  const price = parseDecimal(fuel.price, 4);
  const total = isFuel ? (liters && price ? estimateAmount(liters, price) : null) : parseAmount(amount);

  const validate = () =>
    (account === null ? "Elegí la cuenta pagadora." : null) ??
    (!businessDate ? "Indicá la fecha del gasto." : null) ??
    (businessDate > todayIso() ? "La fecha del gasto no puede ser futura." : null) ??
    (category === undefined ? "Elegí la categoría." : null) ??
    (isFuel ? fuelProblem(fuel) : null) ??
    (!isFuel && !parseAmount(amount) ? "Ingresá un importe mayor a cero, con hasta dos decimales." : null);

  const review = (event: FormEvent) => {
    event.preventDefault();
    const local = validate();
    setProblem(local);
    if (!local) {
      action.mutation.reset();
      setConfirming(true);
    }
  };

  const preview = (): ExpensePreview => {
    const vehicle = (vehicles.data ?? []).find((v) => v.id === fuel.vehicle);
    return {
      category: category?.name ?? "",
      account: accountName(account ?? 0) ?? "",
      expenseAccount,
      amount: total ?? "0",
      businessDate,
      description: description.trim() || null,
      ...(isFuel && {
        vehicle: vehicle ? `${vehicle.plate_display} · ${vehicle.description}` : "",
        employee: (employees.data ?? []).find((e) => e.id === fuel.employee)?.full_name ?? "",
        liters: liters ?? undefined,
        price: price ?? undefined,
        odometer: fuel.odometer.trim() || undefined,
      }),
    };
  };

  const confirm = () =>
    action.submit((requestId) => ({
      method: "POST",
      path: "/expenses",
      body: {
        client_request_id: requestId,
        business_date: businessDate,
        category_id: categoryId,
        paid_from_account_id: account,
        description: description.trim() || null,
        ...(isFuel ? { fuel: fuelBody(fuel) } : { amount: parseAmount(amount) }),
      },
      summary: `Gasto de ${category?.name ?? ""} por ${formatMoney(total ?? "0")} desde ${accountName(account ?? 0) ?? ""}`,
    }));

  const title = "Registrar gasto";
  const intro = "Un gasto pagado desde una caja o el banco. Sale de esa cuenta y entra en Gastos operativos.";

  if (action.stored) {
    return (
      <Dialog title={title} description={intro} onClose={onClose}>
        <PendingAttempt
          action={action}
          onClose={onClose}
          errorView={(e) => expenseErrorView(e, accountName)}
          where="el listado de gastos directos"
        />
      </Dialog>
    );
  }

  if (confirming) {
    return (
      <Dialog title="Confirmá el gasto" description="Revisá el movimiento antes de registrarlo." onClose={onClose}>
        <div className="space-y-4">
          <ExpenseConfirmation preview={preview()} />
          {isFuel && (
            <p className="text-xs text-muted-foreground">
              El importe lo calcula el sistema al guardar: litros × precio por litro, redondeado al centavo.
            </p>
          )}
          {error && (
            <div
              role="alert"
              className="flex gap-2 rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger"
            >
              <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
              <p>{error.message}</p>
            </div>
          )}
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" disabled={pending} onClick={() => setConfirming(false)}>
              Volver a editar
            </Button>
            {error?.conflict ? (
              <Button variant="secondary" onClick={action.abandonStored}>
                Empezar de nuevo
              </Button>
            ) : (
              <Button disabled={pending} onClick={confirm}>
                {pending ? "Registrando…" : "Confirmar y registrar"}
              </Button>
            )}
          </div>
        </div>
      </Dialog>
    );
  }

  return (
    <Dialog title={title} description={intro} onClose={onClose}>
      <form className="space-y-4" onSubmit={review} noValidate>
        <SelectField
          label="Cuenta pagadora"
          value={account}
          onChange={setAccount}
          placeholder="Elegí la caja o el banco…"
          options={payable.map((a) => ({ id: a.id, name: `${a.name} · saldo ${formatMoney(a.balance)}` }))}
        />
        <div className="space-y-1.5">
          <Label htmlFor={dateId}>Fecha del gasto</Label>
          <Input
            id={dateId}
            type="date"
            max={todayIso()}
            className="w-44 tabular-nums"
            value={businessDate}
            onChange={(event) => setBusinessDate(event.target.value)}
          />
        </div>
        <SelectField
          label="Categoría"
          value={categoryId}
          onChange={setCategoryId}
          placeholder={categories.isPending ? "Cargando categorías…" : "Elegí una categoría…"}
          options={categories.data ?? []}
        />
        {category &&
          (isFuel ? (
            <FuelFields form={fuel} onChange={setFuel} />
          ) : (
            <AmountField value={amount} onChange={setAmount} />
          ))}
        <TextField label="Descripción (opcional)" value={description} onChange={setDescription} multiline />
        <FormError message={problem} />
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button type="submit">Revisar gasto</Button>
        </div>
      </form>
    </Dialog>
  );
}
