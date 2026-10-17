import { parseAmount } from "@/modules/settlements/cash/amount";

/**
 * Form helpers of a loan's schedule (R8-3). They only drive the form (defaults, a running total so the admin sees
 * whether the edited installments still add up); the backend validates the schedule and computes the default one.
 */

/** The same day `months` later, or the last day of a shorter month (31/01 + 1 → 28/02). ISO in, ISO out. */
export function addMonths(iso: string, months: number): string {
  const [year, month, day] = iso.split("-").map(Number);
  const index = month - 1 + months;
  const targetYear = year + Math.floor(index / 12);
  const targetMonth = (((index % 12) + 12) % 12) + 1;
  const last = new Date(Date.UTC(targetYear, targetMonth, 0)).getUTCDate();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${targetYear}-${pad(targetMonth)}-${pad(Math.min(day, last))}`;
}

/** An API amount ("100000.00") as the form shows it for editing ("100000,00"). */
export const editableAmount = (amount: string) => amount.replace(".", ",");

const cents = (amount: string) => BigInt(amount.replace(".", ""));

/** The sum of typed amounts, in cents, as an API decimal string; null when one of them is not a valid amount. */
export function sumAmounts(typed: string[]): string | null {
  let total = BigInt(0);
  for (const value of typed) {
    const parsed = parseAmount(value);
    if (parsed === null) return null;
    total += cents(parsed);
  }
  const text = total.toString().padStart(3, "0");
  return `${text.slice(0, -2)}.${text.slice(-2)}`;
}

export const sameAmount = (a: string, b: string) => cents(a) === cents(b);
