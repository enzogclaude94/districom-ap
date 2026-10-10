"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Plus, Trash2, UserPlus } from "lucide-react";
import { useId, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ThirdPartyForm } from "@/features/third-parties/third-party-form";
import { thirdPartiesApi, thirdPartiesQueryKey, type ThirdParty } from "@/lib/api/third-parties";
import { formatDate, formatMoney, todayIso } from "@/lib/format";
import { parseAmount } from "@/modules/settlements/cash/amount";
import { cashQueryKey, type Account } from "@/modules/settlements/cash/api";
import { deliveryApi, deliveryQueryKey, FUEL_CODE } from "@/modules/settlements/delivery/api";
import { AmountField, FormError, PendingAttempt, selectClass, TextField } from "@/modules/settlements/delivery/form";
import { useWriteAction, type WriteAction } from "@/modules/settlements/delivery/use-delivery-action";

import { paymentApi, paymentQueryKey, type PaymentConcept, type PaymentDetail, type PaymentMethod } from "./api";
import { paymentErrorView } from "./errors";
import { CONCEPT_LABELS, ENABLED_METHODS, METHOD_LABELS, OPERATOR_CONCEPTS } from "./labels";

/** Technical account of each concept's counter-leg (by code), with its name as fallback. */
const COUNTER_ACCOUNTS: Record<PaymentConcept, { code: string; name: string }> = {
  MERCHANDISE: { code: "merchandise_payments", name: "Pagos por mercadería" },
  EXPENSE: { code: "expense", name: "Gastos operativos" },
  REFUND: { code: "third_party_refunds", name: "Devoluciones a terceros" },
  OTHER: { code: "other_payments", name: "Otros pagos" },
};

const METHOD_KIND: Record<PaymentMethod, Account["kind"] | null> = { CASH: "CASH_BOX", TRANSFER: "BANK", CHEQUE: null };

export interface ItemForm {
  key: number;
  method: PaymentMethod;
  accountId: number | null;
  amount: string;
  reference: string;
}

/** Amount in cents from the API decimal string ("1500.50" → 150050). Only for the form's own arithmetic. */
export const toCents = (amount: string) => {
  const [integer, fraction = ""] = amount.split(".");
  return Number(integer) * 100 + Number(fraction.padEnd(2, "0").slice(0, 2));
};

export const fromCents = (cents: number) =>
  `${Math.trunc(cents / 100)}.${String(Math.abs(cents) % 100).padStart(2, "0")}`;

/**
 * Total, applied, missing and over of the payment being typed. The backend sums the items again and refuses
 * a total that differs (`payment_amount_mismatch`); this only keeps the confirmation closed until they match.
 */
export function paymentBalance(total: string, items: Pick<ItemForm, "amount">[]) {
  const totalCents = parseAmount(total) ? toCents(parseAmount(total)!) : null;
  const applied = items.reduce(
    (sum, item) => sum + (parseAmount(item.amount) ? toCents(parseAmount(item.amount)!) : 0),
    0,
  );
  const gap = totalCents === null ? null : totalCents - applied;
  return {
    total: totalCents === null ? null : fromCents(totalCents),
    applied: fromCents(applied),
    missing: gap !== null && gap > 0 ? fromCents(gap) : null,
    over: gap !== null && gap < 0 ? fromCents(-gap) : null,
    matches: gap === 0 && applied > 0,
  };
}

export function BalanceBar({ balance }: { balance: ReturnType<typeof paymentBalance> }) {
  const cell = (label: string, value: string | null, tone?: string) => (
    <div className="space-y-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={`font-medium tabular-nums ${tone ?? ""}`}>{value ? formatMoney(value) : "—"}</dd>
    </div>
  );
  return (
    <dl
      className="grid grid-cols-2 gap-3 rounded-md border border-border px-3 py-2 text-sm sm:grid-cols-4"
      data-testid="payment-balance"
      aria-live="polite"
    >
      {cell("Total", balance.total)}
      {cell("Aplicado", balance.applied)}
      {cell("Falta", balance.missing, balance.missing ? "text-warning" : undefined)}
      {cell("Sobra", balance.over, balance.over ? "text-danger" : undefined)}
    </dl>
  );
}

/** The ledger legs the confirmation lists, before anything is sent. */
export interface PaymentPreview {
  title: string;
  legs: { account: string; amount: string }[];
  counter: { account: string; amount: string };
  businessDate: string;
  category?: string;
  reference?: string;
  description?: string;
}

export function PaymentConfirmation({ preview }: { preview: PaymentPreview }) {
  const rows: [string, string][] = [
    ...preview.legs.map((leg): [string, string] => [leg.account, formatMoney(`-${leg.amount}`)]),
    [preview.counter.account, formatMoney(preview.counter.amount, { signed: true })],
    ["Fecha", formatDate(preview.businessDate)],
  ];
  if (preview.category) rows.push(["Categoría", preview.category]);
  if (preview.reference) rows.push(["Referencia", preview.reference]);
  if (preview.description) rows.push(["Descripción", preview.description]);
  return (
    <div className="space-y-2" data-testid="payment-confirmation">
      <p className="font-medium">{preview.title}</p>
      <dl className="divide-y divide-border rounded-md border border-border text-sm">
        {rows.map(([label, value], index) => (
          <div key={`${label}-${index}`} className="flex flex-wrap justify-between gap-x-4 gap-y-0.5 px-3 py-2">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="font-medium break-words tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/** One write on a payment: create, reverse or correct it, never duplicated (ADR 008). */
export function usePaymentAction(slot: string, onDone: (payment: PaymentDetail) => void) {
  return useWriteAction<PaymentDetail>({
    slot,
    write: (request) => paymentApi.write(request.path, request.body),
    invalidate: [paymentQueryKey, cashQueryKey, thirdPartiesQueryKey],
    isUncertain: (error) => Boolean(paymentErrorView(error).uncertain),
    onDone,
  });
}

function ItemRow({
  item,
  index,
  accounts,
  removable,
  onChange,
  onRemove,
}: {
  item: ItemForm;
  index: number;
  accounts: Account[];
  removable: boolean;
  onChange: (item: ItemForm) => void;
  onRemove: () => void;
}) {
  const ids = { method: useId(), account: useId() };
  const kind = METHOD_KIND[item.method];
  const options = accounts.filter((a) => a.active && a.kind === kind);
  return (
    <fieldset className="space-y-3 rounded-md border border-border p-3" data-testid={`payment-item-${index}`}>
      <legend className="px-1 text-sm font-medium">Medio {index + 1}</legend>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor={ids.method}>Medio de pago</Label>
          <select
            id={ids.method}
            className={selectClass}
            value={item.method}
            onChange={(e) => {
              const method = e.target.value as PaymentMethod;
              const next = accounts.filter((a) => a.active && a.kind === METHOD_KIND[method]);
              onChange({ ...item, method, accountId: next.length === 1 ? next[0].id : null });
            }}
          >
            {ENABLED_METHODS.map((method) => (
              <option key={method} value={method}>
                {METHOD_LABELS[method]}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={ids.account}>{item.method === "CASH" ? "Caja" : "Banco"}</Label>
          <select
            id={ids.account}
            className={selectClass}
            value={item.accountId ?? ""}
            onChange={(e) => onChange({ ...item, accountId: e.target.value ? Number(e.target.value) : null })}
          >
            <option value="">{item.method === "CASH" ? "Elegí la caja…" : "Elegí el banco…"}</option>
            {options.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name} · saldo {formatMoney(a.balance)}
              </option>
            ))}
          </select>
        </div>
      </div>
      <AmountField
        label={`Importe del medio ${index + 1}`}
        value={item.amount}
        onChange={(amount) => onChange({ ...item, amount })}
      />
      <TextField
        label={`Referencia del medio ${index + 1} (opcional)`}
        value={item.reference}
        onChange={(reference) => onChange({ ...item, reference })}
        maxLength={100}
        hint={item.method === "TRANSFER" ? "Por ejemplo el número de la transferencia." : undefined}
      />
      {removable && (
        <div className="flex justify-end">
          <Button variant="ghost" size="sm" onClick={onRemove} aria-label={`Quitar medio ${index + 1}`}>
            <Trash2 aria-hidden />
            Quitar
          </Button>
        </div>
      )}
    </fieldset>
  );
}

/**
 * «Registrar pago» (R6-1): a payment made to a third party with one or several means (cash from a cash box,
 * transfers from a bank). It records a payment made, never a debt: there is no balance per third party.
 * Three steps: the form (the third party can be registered on the spot), then an explicit confirmation
 * listing every leg of the ledger, so money never leaves from one accidental click. The confirmation stays
 * closed until the means add up exactly to the total.
 */
export function PaymentDialog({
  accounts,
  isAdmin,
  initialThirdParty,
  onClose,
  onDone,
}: {
  accounts: Account[];
  isAdmin: boolean;
  initialThirdParty?: number;
  onClose: () => void;
  onDone: (payment: PaymentDetail) => void;
}) {
  const ids = { thirdParty: useId(), date: useId(), concept: useId(), category: useId() };
  const queryClient = useQueryClient();
  const thirdParties = useQuery({
    queryKey: [...thirdPartiesQueryKey, "list", "active", ""],
    queryFn: () => thirdPartiesApi.list(false),
  });
  const categories = useQuery({ queryKey: [...deliveryQueryKey, "categories"], queryFn: deliveryApi.categories });

  const newItem = (key: number, method: PaymentMethod = "CASH"): ItemForm => {
    const options = accounts.filter((a) => a.active && a.kind === METHOD_KIND[method]);
    return { key, method, accountId: options.length === 1 ? options[0].id : null, amount: "", reference: "" };
  };

  const [step, setStep] = useState<"form" | "third-party" | "confirm">("form");
  const [created, setCreated] = useState<ThirdParty | null>(null);
  const [thirdPartyId, setThirdPartyId] = useState<number | null>(initialThirdParty ?? null);
  const [businessDate, setBusinessDate] = useState(todayIso);
  const [concept, setConcept] = useState<PaymentConcept | "">("");
  const [categoryId, setCategoryId] = useState<number | null>(null);
  const [total, setTotal] = useState("");
  const [reference, setReference] = useState("");
  const [description, setDescription] = useState("");
  const [items, setItems] = useState<ItemForm[]>(() => [newItem(0)]);
  const [problem, setProblem] = useState<string | null>(null);

  const action: WriteAction = usePaymentAction("payment-create", (payment) => {
    onDone(payment);
    onClose();
  });
  const pending = action.mutation.isPending;
  const accountName = (id: number) => accounts.find((a) => a.id === id)?.name;
  const error = action.mutation.error ? paymentErrorView(action.mutation.error, accountName, businessDate) : null;

  const options = [...(thirdParties.data ?? [])];
  if (created && !options.some((t) => t.id === created.id)) options.push(created);
  options.sort((a, b) => a.name.localeCompare(b.name, "es"));
  const thirdParty = options.find((t) => t.id === thirdPartyId);
  const expenseCategories = (categories.data ?? []).filter((c) => c.code !== FUEL_CODE);
  const category = expenseCategories.find((c) => c.id === categoryId);
  const concepts = isAdmin ? [...OPERATOR_CONCEPTS, "OTHER" as const] : OPERATOR_CONCEPTS;
  const balance = paymentBalance(total, items);

  const validate = () => {
    if (thirdPartyId === null) return "Elegí el tercero o registralo.";
    if (!businessDate) return "Indicá la fecha del pago.";
    if (businessDate > todayIso()) return "La fecha del pago no puede ser futura.";
    if (!concept) return "Elegí el concepto.";
    if (concept === "EXPENSE" && !category) return "Elegí la categoría del gasto.";
    if (concept === "OTHER" && !description.trim()) return "En «Otro pago» la descripción es obligatoria.";
    if (!parseAmount(total)) return "Ingresá el total del pago, mayor a cero, con hasta dos decimales.";
    for (const [index, item] of items.entries()) {
      if (item.accountId === null) return `Elegí la cuenta del medio ${index + 1}.`;
      if (!parseAmount(item.amount)) return `Ingresá el importe del medio ${index + 1}.`;
    }
    const used = items.map((item) => item.accountId);
    if (new Set(used).size !== used.length)
      return "Cada caja o banco va una sola vez: sumá su importe en un solo medio.";
    if (!balance.matches) return "Los medios tienen que sumar exactamente el total.";
    return null;
  };

  const review = (event: FormEvent) => {
    event.preventDefault();
    const local = validate();
    setProblem(local);
    if (!local) {
      action.mutation.reset();
      setStep("confirm");
    }
  };

  const counter = concept ? COUNTER_ACCOUNTS[concept] : null;
  const preview = (): PaymentPreview => ({
    title: `${concept ? CONCEPT_LABELS[concept] : ""} a ${thirdParty?.name ?? ""}`,
    legs: items.map((item) => ({
      account: accountName(item.accountId ?? 0) ?? "",
      amount: parseAmount(item.amount) ?? "0",
    })),
    counter: {
      account: accounts.find((a) => a.code === counter?.code)?.name ?? counter?.name ?? "",
      amount: balance.total ?? "0",
    },
    businessDate,
    category: concept === "EXPENSE" ? category?.name : undefined,
    reference: reference.trim() || undefined,
    description: description.trim() || undefined,
  });

  const confirm = () =>
    action.submit((requestId) => ({
      method: "POST",
      path: "/payments",
      body: {
        client_request_id: requestId,
        business_date: businessDate,
        third_party_id: thirdPartyId,
        concept,
        expense_category_id: concept === "EXPENSE" ? categoryId : null,
        amount: balance.total,
        reference: reference.trim() || null,
        description: description.trim() || null,
        items: items.map((item) => ({
          method: item.method,
          account_id: item.accountId,
          amount: parseAmount(item.amount),
          reference: item.reference.trim() || null,
        })),
      },
      summary: `${concept ? CONCEPT_LABELS[concept] : "Pago"} a ${thirdParty?.name ?? ""} por ${formatMoney(balance.total ?? "0")}`,
    }));

  const title = "Registrar pago";
  const intro = "Un pago hecho a un tercero, en efectivo o por transferencia. Registra lo pagado, no deudas.";

  if (action.stored) {
    return (
      <Dialog title={title} description={intro} onClose={onClose}>
        <PendingAttempt
          action={action}
          onClose={onClose}
          errorView={(e) => paymentErrorView(e, accountName)}
          where="el listado de pagos"
        />
      </Dialog>
    );
  }

  if (step === "third-party") {
    return (
      <Dialog
        title="Nuevo tercero"
        description="Queda registrado en el maestro de Terceros de DistriCom. Solo un administrador lo corrige o lo desactiva."
        onClose={onClose}
      >
        <ThirdPartyForm
          submitLabel="Registrar y usar"
          save={paymentApi.createThirdParty}
          onSaved={(saved) => {
            setCreated(saved);
            setThirdPartyId(saved.id);
            void queryClient.invalidateQueries({ queryKey: thirdPartiesQueryKey });
            setStep("form");
          }}
          onUseExisting={(candidate) => {
            setThirdPartyId(candidate.third_party_id);
            setStep("form");
          }}
          onCancel={() => setStep("form")}
        />
      </Dialog>
    );
  }

  if (step === "confirm") {
    return (
      <Dialog title="Confirmá el pago" description="Revisá el movimiento antes de registrarlo." onClose={onClose}>
        <div className="space-y-4">
          <PaymentConfirmation preview={preview()} />
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
            <Button variant="ghost" disabled={pending} onClick={() => setStep("form")}>
              Volver a editar
            </Button>
            {error?.conflict ? (
              <Button variant="secondary" onClick={action.abandonStored}>
                Empezar de nuevo
              </Button>
            ) : (
              <Button disabled={pending} onClick={confirm}>
                {pending ? "Registrando…" : "Confirmar pago"}
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
        <div className="space-y-1.5">
          <Label htmlFor={ids.thirdParty}>Tercero</Label>
          <div className="flex flex-wrap gap-2">
            <select
              id={ids.thirdParty}
              className={`${selectClass} min-w-0 flex-1 basis-48`}
              value={thirdPartyId ?? ""}
              onChange={(e) => setThirdPartyId(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">{thirdParties.isPending ? "Cargando terceros…" : "Elegí el tercero…"}</option>
              {options.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                  {t.tax_id_display ? ` · ${t.tax_id_display}` : ""}
                </option>
              ))}
            </select>
            <Button variant="secondary" onClick={() => setStep("third-party")}>
              <UserPlus aria-hidden />
              Nuevo tercero
            </Button>
          </div>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor={ids.date}>Fecha del pago</Label>
            <Input
              id={ids.date}
              type="date"
              max={todayIso()}
              className="tabular-nums"
              value={businessDate}
              onChange={(e) => setBusinessDate(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.concept}>Concepto</Label>
            <select
              id={ids.concept}
              className={selectClass}
              value={concept}
              onChange={(e) => setConcept(e.target.value as PaymentConcept | "")}
            >
              <option value="">Elegí el concepto…</option>
              {concepts.map((value) => (
                <option key={value} value={value}>
                  {CONCEPT_LABELS[value]}
                </option>
              ))}
            </select>
          </div>
        </div>
        {concept === "EXPENSE" && (
          <div className="space-y-1.5">
            <Label htmlFor={ids.category}>Categoría del gasto</Label>
            <select
              id={ids.category}
              className={selectClass}
              value={categoryId ?? ""}
              onChange={(e) => setCategoryId(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">{categories.isPending ? "Cargando categorías…" : "Elegí una categoría…"}</option>
              {expenseCategories.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
            <p className="text-xs text-muted-foreground">
              El combustible no se paga acá: se carga en Combustible o como gasto.
            </p>
          </div>
        )}
        <AmountField label="Total del pago" value={total} onChange={setTotal} />
        <div className="space-y-3">
          {items.map((item, index) => (
            <ItemRow
              key={item.key}
              item={item}
              index={index}
              accounts={accounts}
              removable={items.length > 1}
              onChange={(next) => setItems(items.map((i) => (i.key === item.key ? next : i)))}
              onRemove={() => setItems(items.filter((i) => i.key !== item.key))}
            />
          ))}
          <Button
            variant="secondary"
            size="sm"
            disabled={items.length >= 20}
            onClick={() =>
              setItems([
                ...items,
                newItem(
                  Math.max(...items.map((i) => i.key)) + 1,
                  items.some((i) => i.method === "CASH") ? "TRANSFER" : "CASH",
                ),
              ])
            }
          >
            <Plus aria-hidden />
            Agregar medio de pago
          </Button>
        </div>
        <BalanceBar balance={balance} />
        <TextField
          label="Referencia (opcional)"
          value={reference}
          onChange={setReference}
          maxLength={100}
          hint="Por ejemplo el número de factura o de recibo."
        />
        <TextField
          label={concept === "OTHER" ? "Descripción" : "Descripción (opcional)"}
          value={description}
          onChange={setDescription}
          multiline
        />
        <FormError message={problem} />
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button type="submit">Revisar pago</Button>
        </div>
      </form>
    </Dialog>
  );
}
