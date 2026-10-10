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
import { chequeApi, chequeQueryKey, type Cheque } from "@/modules/settlements/cheques/api";
import { KIND_LABELS, PAYMENT_TYPE_LABELS } from "@/modules/settlements/cheques/labels";
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
  /** CHEQUE: the cheque of the portfolio; its amount is the cheque's, never typed. */
  chequeId: number | null;
  amount: string;
  reference: string;
}

/** The cheques that can be delivered: those in the portfolio now (the backend checks it again, on the date). */
const PORTFOLIO_FILTERS = { in_portfolio: true, limit: 200, offset: 0 } as const;
export const portfolioQueryKey = [...chequeQueryKey, "list", PORTFOLIO_FILTERS] as const;

/** "N.º 123 · Banco Galicia · Físico diferido · $ 200.000,00". */
export const chequeOption = (cheque: Cheque) =>
  `N.º ${cheque.cheque_number} · ${cheque.bank_name} · ${KIND_LABELS[cheque.kind]} ${PAYMENT_TYPE_LABELS[cheque.payment_type].toLowerCase()} · ${formatMoney(cheque.amount)}`;

/**
 * The amount each item puts in the payment: what was typed for cash and transfers, the whole amount of the
 * cheque for a cheque (never a fraction, never typed).
 */
export function itemAmounts(items: ItemForm[], cheques: Pick<Cheque, "id" | "amount">[]): Pick<ItemForm, "amount">[] {
  return items.map((item) =>
    item.method === "CHEQUE"
      ? { amount: cheques.find((c) => c.id === item.chequeId)?.amount ?? "" }
      : { amount: item.amount },
  );
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
    invalidate: [paymentQueryKey, cashQueryKey, thirdPartiesQueryKey, chequeQueryKey],
    isUncertain: (error) => Boolean(paymentErrorView(error).uncertain),
    onDone,
  });
}

function ChequeFields({
  item,
  index,
  cheques,
  loading,
  taken,
  onChange,
}: {
  item: ItemForm;
  index: number;
  cheques: Cheque[];
  loading: boolean;
  taken: Set<number>;
  onChange: (item: ItemForm) => void;
}) {
  const ids = { cheque: useId(), amount: useId() };
  const cheque = cheques.find((c) => c.id === item.chequeId);
  const options = cheques.filter((c) => c.id === item.chequeId || !taken.has(c.id));
  return (
    <>
      <div className="space-y-1.5">
        <Label htmlFor={ids.cheque}>Cheque de la cartera</Label>
        <select
          id={ids.cheque}
          className={selectClass}
          value={item.chequeId ?? ""}
          onChange={(e) => onChange({ ...item, chequeId: e.target.value ? Number(e.target.value) : null })}
        >
          <option value="">
            {loading ? "Cargando cheques…" : options.length === 0 ? "No hay cheques en cartera" : "Elegí el cheque…"}
          </option>
          {options.map((c) => (
            <option key={c.id} value={c.id}>
              {chequeOption(c)}
            </option>
          ))}
        </select>
      </div>
      {cheque && (
        <dl
          className="grid grid-cols-2 gap-2 rounded-md bg-muted/40 px-3 py-2 text-xs sm:grid-cols-3"
          data-testid={`payment-cheque-${index}`}
        >
          <div>
            <dt className="text-muted-foreground">Librador</dt>
            <dd className="break-words">{cheque.drawer_name}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Lo entregó</dt>
            <dd className="break-words">{cheque.delivered_by_name}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Pagadero desde</dt>
            <dd className="tabular-nums">{formatDate(cheque.payable_from)}</dd>
          </div>
        </dl>
      )}
      <div className="space-y-1.5">
        <Label htmlFor={ids.amount}>{`Importe del medio ${index + 1}`}</Label>
        <Input
          id={ids.amount}
          readOnly
          aria-readonly
          className="tabular-nums"
          value={cheque ? formatMoney(cheque.amount) : ""}
          placeholder="El del cheque"
          aria-describedby={`${ids.amount}-hint`}
        />
        <p id={`${ids.amount}-hint`} className="text-xs text-muted-foreground">
          Es el importe del cheque: se entrega entero, sin fracciones ni vuelto.
        </p>
      </div>
    </>
  );
}

function ItemRow({
  item,
  index,
  accounts,
  cheques,
  chequesLoading,
  takenCheques,
  removable,
  onChange,
  onRemove,
}: {
  item: ItemForm;
  index: number;
  accounts: Account[];
  cheques: Cheque[];
  chequesLoading: boolean;
  takenCheques: Set<number>;
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
              onChange({
                ...item,
                method,
                accountId: method !== "CHEQUE" && next.length === 1 ? next[0].id : null,
                chequeId: null,
                amount: method === "CHEQUE" ? "" : item.amount,
              });
            }}
          >
            {ENABLED_METHODS.map((method) => (
              <option key={method} value={method}>
                {METHOD_LABELS[method]}
              </option>
            ))}
          </select>
        </div>
        {item.method !== "CHEQUE" && (
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
        )}
      </div>
      {item.method === "CHEQUE" ? (
        <ChequeFields
          item={item}
          index={index}
          cheques={cheques}
          loading={chequesLoading}
          taken={takenCheques}
          onChange={onChange}
        />
      ) : (
        <AmountField
          label={`Importe del medio ${index + 1}`}
          value={item.amount}
          onChange={(amount) => onChange({ ...item, amount })}
        />
      )}
      <TextField
        label={`Referencia del medio ${index + 1} (opcional)`}
        value={item.reference}
        onChange={(reference) => onChange({ ...item, reference })}
        maxLength={100}
        hint={
          item.method === "TRANSFER"
            ? "Por ejemplo el número de la transferencia."
            : item.method === "CHEQUE"
              ? "Por ejemplo el recibo que firmó el tercero."
              : undefined
        }
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
 * «Registrar pago» (R6-1/R6-2): a payment made to a third party with one or several means (cash from a cash
 * box, transfers from a bank, cheques of the portfolio delivered whole). It records a payment made, never a
 * debt: there is no balance per third party. «Entregar» on a cheque opens it with that cheque preloaded.
 * Three steps: the form (the third party can be registered on the spot), then an explicit confirmation
 * listing every leg of the ledger, so money never leaves from one accidental click. The confirmation stays
 * closed until the means add up exactly to the total.
 */
export function PaymentDialog({
  accounts,
  isAdmin,
  initialThirdParty,
  initialCheque,
  onClose,
  onDone,
}: {
  accounts: Account[];
  isAdmin: boolean;
  initialThirdParty?: number;
  initialCheque?: number;
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
  const portfolio = useQuery({ queryKey: portfolioQueryKey, queryFn: () => chequeApi.list(PORTFOLIO_FILTERS) });

  const newItem = (key: number, method: PaymentMethod = "CASH", chequeId: number | null = null): ItemForm => {
    const options = accounts.filter((a) => a.active && a.kind === METHOD_KIND[method]);
    return {
      key,
      method,
      accountId: method !== "CHEQUE" && options.length === 1 ? options[0].id : null,
      chequeId,
      amount: "",
      reference: "",
    };
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
  const [items, setItems] = useState<ItemForm[]>(() => [
    initialCheque ? newItem(0, "CHEQUE", initialCheque) : newItem(0),
  ]);
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
  const cheques = portfolio.data?.items ?? [];
  const chequeOf = (item: ItemForm) => cheques.find((c) => c.id === item.chequeId);
  const balance = paymentBalance(total, itemAmounts(items, cheques));
  const takenCheques = new Set(
    items.flatMap((item) => (item.method === "CHEQUE" && item.chequeId ? [item.chequeId] : [])),
  );
  const portfolioAccount = accounts.find((a) => a.kind === "CHEQUE_PORTFOLIO")?.name ?? "Cartera de cheques";

  const validate = () => {
    if (thirdPartyId === null) return "Elegí el tercero o registralo.";
    if (!businessDate) return "Indicá la fecha del pago.";
    if (businessDate > todayIso()) return "La fecha del pago no puede ser futura.";
    if (!concept) return "Elegí el concepto.";
    if (concept === "EXPENSE" && !category) return "Elegí la categoría del gasto.";
    if (concept === "OTHER" && !description.trim()) return "En «Otro pago» la descripción es obligatoria.";
    if (!parseAmount(total)) return "Ingresá el total del pago, mayor a cero, con hasta dos decimales.";
    for (const [index, item] of items.entries()) {
      if (item.method === "CHEQUE") {
        if (item.chequeId === null) return `Elegí el cheque del medio ${index + 1}.`;
        if (!chequeOf(item)) return `El cheque del medio ${index + 1} ya no está en cartera: elegí otro.`;
        continue;
      }
      if (item.accountId === null) return `Elegí la cuenta del medio ${index + 1}.`;
      if (!parseAmount(item.amount)) return `Ingresá el importe del medio ${index + 1}.`;
    }
    const used = items.filter((item) => item.method !== "CHEQUE").map((item) => item.accountId);
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
    legs: items.map((item) => {
      const cheque = chequeOf(item);
      if (item.method === "CHEQUE")
        return {
          account: `Cheque n.º ${cheque?.cheque_number ?? ""} desde ${portfolioAccount}`,
          amount: cheque?.amount ?? "0",
        };
      return { account: accountName(item.accountId ?? 0) ?? "", amount: parseAmount(item.amount) ?? "0" };
    }),
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
        items: items.map((item) =>
          item.method === "CHEQUE"
            ? {
                method: item.method,
                cheque_id: item.chequeId,
                amount: chequeOf(item)?.amount,
                reference: item.reference.trim() || null,
              }
            : {
                method: item.method,
                account_id: item.accountId,
                amount: parseAmount(item.amount),
                reference: item.reference.trim() || null,
              },
        ),
      },
      summary: `${concept ? CONCEPT_LABELS[concept] : "Pago"} a ${thirdParty?.name ?? ""} por ${formatMoney(balance.total ?? "0")}`,
    }));

  const title = "Registrar pago";
  const intro =
    "Un pago hecho a un tercero, en efectivo, por transferencia o con cheques de la cartera. Registra lo pagado, no deudas.";

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
              cheques={cheques}
              chequesLoading={portfolio.isPending}
              takenCheques={takenCheques}
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
