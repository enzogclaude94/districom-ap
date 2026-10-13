import { parseAmount } from "@/modules/settlements/cash/amount";

/**
 * The Store Sys amount of a boca (R7-2): like any amount, but $0 is valid (a day without sales). Returns
 * "0.00" for a zero, the decimal string for any other valid amount, and null when empty or invalid; the
 * caller tells those apart with `input.trim()`.
 */
export function parseExpected(input: string): string | null {
  const amount = parseAmount(input);
  if (amount) return amount;
  return /^\s*\$?\s*0+(?:[.,]0{1,2})?\s*$/.test(input) ? "0.00" : null;
}
