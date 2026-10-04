"use client";

import { AlertTriangle, ShieldCheck } from "lucide-react";
import { useId, useState, type FormEvent, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  cashApi,
  type Account,
  type AccountKind,
  type Operation,
  type OperationKind,
} from "@/modules/settlements/cash/api";
import { formatMoney, todayIso } from "@/lib/format";

import { parseAmount } from "./amount";
import { cashErrorView } from "./errors";
import { useCashOperation } from "./use-cash-operation";

export type CashAction = OperationKind;

interface FormProps {
  accounts: Account[];
  onClose: () => void;
  onDone: (operation: Operation) => void;
}

interface Base {
  client_request_id: string;
  business_date: string;
  description: string | null;
}

const selectClass =
  "flex h-9 w-full rounded-md border border-border bg-surface px-3 text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none";

const textareaClass =
  "flex min-h-20 w-full rounded-md border border-border bg-surface px-3 py-2 text-sm placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none aria-invalid:border-danger";

function usable(accounts: Account[], kinds: AccountKind[]) {
  return accounts.filter((account) => account.active && kinds.includes(account.kind));
}

function AccountSelect({
  label,
  accounts,
  value,
  onChange,
}: {
  label: string;
  accounts: Account[];
  value: number | null;
  onChange: (id: number | null) => void;
}) {
  const id = useId();
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <select
        id={id}
        className={selectClass}
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value ? Number(event.target.value) : null)}
      >
        <option value="">Elegí una cuenta…</option>
        {accounts.map((account) => (
          <option key={account.id} value={account.id}>
            {account.name} · saldo {formatMoney(account.balance)}
          </option>
        ))}
      </select>
    </div>
  );
}

function AmountField({
  value,
  onChange,
  label = "Importe",
}: {
  value: string;
  onChange: (v: string) => void;
  label?: string;
}) {
  const id = useId();
  const parsed = parseAmount(value);
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        inputMode="decimal"
        autoComplete="off"
        placeholder="0,00"
        className="tabular-nums"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-describedby={`${id}-hint`}
      />
      <p id={`${id}-hint`} className="text-xs text-muted-foreground tabular-nums">
        {value && parsed ? `Se registran ${formatMoney(parsed)}` : "Por ejemplo 15.000,50"}
      </p>
    </div>
  );
}

function AdminNotice({ children }: { children: ReactNode }) {
  return (
    <div className="flex gap-2 rounded-md border border-border bg-muted px-3 py-2 text-sm">
      <ShieldCheck className="mt-0.5 size-4 shrink-0 text-primary-deep" aria-hidden />
      <div className="space-y-1">{children}</div>
    </div>
  );
}

interface OperationDialogProps {
  title: string;
  description?: ReactNode;
  submitLabel: string;
  /** "reason": mandatory reason (adjustments); "optional": free description. */
  descriptionMode?: "optional" | "reason";
  notice?: ReactNode;
  accounts: Account[];
  onClose: () => void;
  onDone: (operation: Operation) => void;
  /** Form-level check before sending; returns the message to show or null. */
  validate: () => string | null;
  send: (base: Base) => Promise<Operation>;
  children: ReactNode;
}

/** Shell of every financial form: business date, description, idempotent submit and error messages. */
function OperationDialog({
  title,
  description,
  submitLabel,
  descriptionMode = "optional",
  notice,
  accounts,
  onClose,
  onDone,
  validate,
  send,
  children,
}: OperationDialogProps) {
  const dateId = useId();
  const descriptionId = useId();
  const [businessDate, setBusinessDate] = useState(todayIso);
  const [text, setText] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const operation = useCashOperation(
    (requestId) =>
      send({ client_request_id: requestId, business_date: businessDate, description: text.trim() || null }),
    (done) => {
      onDone(done);
      onClose();
    },
  );
  const pending = operation.mutation.isPending;
  const names = (id: number) => accounts.find((account) => account.id === id)?.name;
  const error = operation.mutation.error ? cashErrorView(operation.mutation.error, names, businessDate) : null;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const local =
      validate() ??
      (!businessDate ? "Indicá la fecha de negocio." : null) ??
      (businessDate > todayIso() ? "La fecha de negocio no puede ser posterior a hoy." : null) ??
      (descriptionMode === "reason" && !text.trim() ? "Indicá el motivo." : null);
    setProblem(local);
    if (!local) operation.submit();
  };

  return (
    <Dialog title={title} description={description} onClose={onClose}>
      <form className="space-y-4" onSubmit={submit} noValidate>
        {notice}
        <fieldset disabled={pending} className="space-y-4">
          {children}
          <div className="space-y-1.5">
            <Label htmlFor={dateId}>Fecha de negocio</Label>
            <Input
              id={dateId}
              type="date"
              max={todayIso()}
              className="w-44 tabular-nums"
              value={businessDate}
              onChange={(event) => setBusinessDate(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={descriptionId}>{descriptionMode === "reason" ? "Motivo" : "Descripción (opcional)"}</Label>
            <textarea
              id={descriptionId}
              maxLength={500}
              className={textareaClass}
              value={text}
              aria-invalid={descriptionMode === "reason" && problem === "Indicá el motivo." ? true : undefined}
              onChange={(event) => setText(event.target.value)}
            />
          </div>
        </fieldset>

        {(problem || error) && (
          <div
            role="alert"
            className="flex gap-2 rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger"
          >
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
            <p>{problem ?? error?.message}</p>
          </div>
        )}

        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          {error?.conflict ? (
            <Button variant="secondary" onClick={operation.restart}>
              Empezar una operación nueva
            </Button>
          ) : (
            <Button type="submit" disabled={pending}>
              {pending ? "Registrando…" : error?.uncertain ? "Reintentar" : submitLabel}
            </Button>
          )}
        </div>
      </form>
    </Dialog>
  );
}

function required(...values: (number | null | string)[]) {
  return values.some((value) => value === null || value === "") ? "Completá todos los campos." : null;
}

function amountProblem(amount: string) {
  return parseAmount(amount) ? null : "Ingresá un importe mayor a cero, con hasta dos decimales.";
}

function TransferForm({ accounts, onClose, onDone }: FormProps) {
  const boxes = usable(accounts, ["CASH_BOX"]);
  const [from, setFrom] = useState<number | null>(boxes[0]?.id ?? null);
  const [to, setTo] = useState<number | null>(boxes[1]?.id ?? null);
  const [amount, setAmount] = useState("");
  return (
    <OperationDialog
      title="Mover entre cajas"
      description="El dinero sale de una caja y entra en otra por el mismo importe."
      submitLabel="Registrar traspaso"
      accounts={accounts}
      onClose={onClose}
      onDone={onDone}
      validate={() =>
        required(from, to) ?? (from === to ? "Elegí dos cajas distintas." : null) ?? amountProblem(amount)
      }
      send={(base) =>
        cashApi.post("cash-transfer", {
          ...base,
          from_account_id: from!,
          to_account_id: to!,
          amount: parseAmount(amount)!,
        })
      }
    >
      <AccountSelect label="Desde" accounts={boxes} value={from} onChange={setFrom} />
      <AccountSelect label="Hacia" accounts={boxes} value={to} onChange={setTo} />
      <AmountField value={amount} onChange={setAmount} />
    </OperationDialog>
  );
}

function BankForm({ accounts, onClose, onDone, direction }: FormProps & { direction: "deposit" | "withdrawal" }) {
  const boxes = usable(accounts, ["CASH_BOX"]);
  const banks = usable(accounts, ["BANK"]);
  const [cash, setCash] = useState<number | null>(boxes[0]?.id ?? null);
  const [bank, setBank] = useState<number | null>(banks[0]?.id ?? null);
  const [amount, setAmount] = useState("");
  const deposit = direction === "deposit";
  const cashSelect = (
    <AccountSelect
      label={deposit ? "Desde la caja" : "Hacia la caja"}
      accounts={boxes}
      value={cash}
      onChange={setCash}
    />
  );
  const bankSelect = (
    <AccountSelect
      label={deposit ? "Hacia el banco" : "Desde el banco"}
      accounts={banks}
      value={bank}
      onChange={setBank}
    />
  );
  return (
    <OperationDialog
      title={deposit ? "Depositar en banco" : "Extraer del banco"}
      description={
        deposit
          ? "Efectivo de una caja depositado en la cuenta bancaria."
          : "Efectivo extraído del banco que entra en una caja."
      }
      submitLabel={deposit ? "Registrar depósito" : "Registrar extracción"}
      accounts={accounts}
      onClose={onClose}
      onDone={onDone}
      validate={() => required(cash, bank) ?? amountProblem(amount)}
      send={(base) =>
        deposit
          ? cashApi.post("bank-deposit", {
              ...base,
              cash_account_id: cash!,
              bank_account_id: bank!,
              amount: parseAmount(amount)!,
            })
          : cashApi.post("bank-withdrawal", {
              ...base,
              bank_account_id: bank!,
              cash_account_id: cash!,
              amount: parseAmount(amount)!,
            })
      }
    >
      {deposit ? cashSelect : bankSelect}
      {deposit ? bankSelect : cashSelect}
      <AmountField value={amount} onChange={setAmount} />
    </OperationDialog>
  );
}

function OpeningBalanceForm({ accounts, onClose, onDone }: FormProps) {
  const targets = usable(accounts, ["CASH_BOX", "BANK"]);
  const [account, setAccount] = useState<number | null>(null);
  const [amount, setAmount] = useState("");
  return (
    <OperationDialog
      title="Cargar saldo inicial"
      submitLabel="Registrar saldo inicial"
      notice={
        <AdminNotice>
          <p className="font-medium">Operación administrativa</p>
          <p className="text-muted-foreground">
            Incorpora al sistema el dinero que ya existía en una caja o en el banco al empezar a usar Rendiciones. No es
            un ingreso del día. Queda registrada con tu usuario y se puede revertir.
          </p>
        </AdminNotice>
      }
      accounts={accounts}
      onClose={onClose}
      onDone={onDone}
      validate={() => required(account) ?? amountProblem(amount)}
      send={(base) => cashApi.post("opening-balance", { ...base, account_id: account!, amount: parseAmount(amount)! })}
    >
      <AccountSelect label="Cuenta" accounts={targets} value={account} onChange={setAccount} />
      <AmountField value={amount} onChange={setAmount} label="Saldo existente" />
    </OperationDialog>
  );
}

function OwnerForm({ accounts, onClose, onDone, direction }: FormProps & { direction: "contribution" | "withdrawal" }) {
  const owners = usable(accounts, ["OWNER_EQUITY"]);
  const targets = usable(accounts, ["CASH_BOX", "BANK"]);
  const [owner, setOwner] = useState<number | null>(owners.length === 1 ? owners[0].id : null);
  const [account, setAccount] = useState<number | null>(null);
  const [amount, setAmount] = useState("");
  const contribution = direction === "contribution";
  return (
    <OperationDialog
      title={contribution ? "Registrar aporte de socio" : "Registrar devolución a socio"}
      submitLabel={contribution ? "Registrar aporte" : "Registrar devolución"}
      notice={
        <AdminNotice>
          <p className="text-muted-foreground">
            {contribution
              ? "Dinero que un socio pone en una caja o en el banco. Queda acumulado en su cuenta de aportes."
              : "Dinero que se le devuelve a un socio desde una caja o el banco. Descuenta de su cuenta de aportes."}
          </p>
        </AdminNotice>
      }
      accounts={accounts}
      onClose={onClose}
      onDone={onDone}
      validate={() =>
        (owners.length === 0 ? "Primero creá la cuenta de aporte del socio." : null) ??
        required(owner, account) ??
        amountProblem(amount)
      }
      send={(base) =>
        cashApi.post(contribution ? "owner-contribution" : "owner-withdrawal", {
          ...base,
          owner_account_id: owner!,
          account_id: account!,
          amount: parseAmount(amount)!,
        })
      }
    >
      <AccountSelect label="Socio" accounts={owners} value={owner} onChange={setOwner} />
      <AccountSelect
        label={contribution ? "Ingresa en" : "Sale de"}
        accounts={targets}
        value={account}
        onChange={setAccount}
      />
      <AmountField value={amount} onChange={setAmount} />
    </OperationDialog>
  );
}

function AdjustmentForm({ accounts, onClose, onDone }: FormProps) {
  const targets = usable(accounts, ["CASH_BOX", "BANK"]);
  const [account, setAccount] = useState<number | null>(null);
  const [direction, setDirection] = useState<"surplus" | "shortage" | null>(null);
  const [amount, setAmount] = useState("");
  return (
    <OperationDialog
      title="Ajuste administrativo"
      submitLabel="Registrar ajuste"
      descriptionMode="reason"
      notice={
        <div className="flex gap-2 rounded-md border border-warning/40 bg-warning-soft px-3 py-2 text-sm">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
          <div className="space-y-1">
            <p className="font-medium">Corrección administrativa, no un movimiento de caja</p>
            <p>
              Modifica el saldo para corregir una diferencia comprobada. Requiere administrador y motivo, queda auditada
              con tu usuario y se puede revertir. No reemplaza registrar la operación que falta.
            </p>
          </div>
        </div>
      }
      accounts={accounts}
      onClose={onClose}
      onDone={onDone}
      validate={() => required(account, direction) ?? amountProblem(amount)}
      send={(base) =>
        cashApi.post("adjustment", {
          ...base,
          description: base.description ?? "",
          account_id: account!,
          amount: `${direction === "shortage" ? "-" : ""}${parseAmount(amount)!}`,
        })
      }
    >
      <AccountSelect label="Cuenta" accounts={targets} value={account} onChange={setAccount} />
      <fieldset className="space-y-1.5">
        <legend className="text-sm font-medium">Tipo de diferencia</legend>
        <div className="flex flex-wrap gap-4 text-sm">
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="direction"
              checked={direction === "surplus"}
              onChange={() => setDirection("surplus")}
            />
            Sobrante (suma al saldo)
          </label>
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="direction"
              checked={direction === "shortage"}
              onChange={() => setDirection("shortage")}
            />
            Faltante (resta del saldo)
          </label>
        </div>
      </fieldset>
      <AmountField value={amount} onChange={setAmount} />
    </OperationDialog>
  );
}

/** The form for one action. Rendering it opens the dialog (and creates its request id). */
export function OperationFormDialog({ action, ...props }: FormProps & { action: CashAction }) {
  switch (action) {
    case "cash-transfer":
      return <TransferForm {...props} />;
    case "bank-deposit":
      return <BankForm {...props} direction="deposit" />;
    case "bank-withdrawal":
      return <BankForm {...props} direction="withdrawal" />;
    case "opening-balance":
      return <OpeningBalanceForm {...props} />;
    case "owner-contribution":
      return <OwnerForm {...props} direction="contribution" />;
    case "owner-withdrawal":
      return <OwnerForm {...props} direction="withdrawal" />;
    case "adjustment":
      return <AdjustmentForm {...props} />;
  }
}
