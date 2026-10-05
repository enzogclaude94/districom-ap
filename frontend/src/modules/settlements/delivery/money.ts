/**
 * Exact arithmetic on decimal strings from the API, for what the screen shows while the user types (a
 * count subtotal, how much of a difference is still unassigned). Works in integer cents, never with
 * fractional floats: amounts have at most 12 integer digits, so cents stay far below 2^53. Official totals
 * always come from the backend.
 */

export function toCents(value: string): number {
  const trimmed = value.trim();
  const negative = trimmed.startsWith("-");
  const [integer = "0", fraction = ""] = trimmed.replace(/^[-+]/, "").split(".");
  const cents = Number(integer || "0") * 100 + Number(fraction.padEnd(2, "0").slice(0, 2) || "0");
  return negative ? -cents : cents;
}

export function fromCents(cents: number): string {
  const negative = cents < 0;
  const abs = Math.abs(cents);
  const text = `${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, "0")}`;
  return negative ? `-${text}` : text;
}

/** Absolute value of a decimal string ("-500.00" → "500.00"). */
export function abs(value: string): string {
  return value.startsWith("-") ? value.slice(1) : value;
}
