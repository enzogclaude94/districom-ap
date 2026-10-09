/**
 * Fuel quantities typed the Argentine way. Liters take up to three decimals and the price per liter up to
 * four, as the backend stores them. The estimate is exact (integer arithmetic, never floats) and only a
 * preview: the official amount is the one the backend computes and returns.
 */

/** "45,678" or "1.234,5678" into "45.678" / "1234.5678", or null when not a positive number with at most `places` decimals. */
export function parseDecimal(input: string, places: number): string | null {
  let text = input.replace(/[\s$]/g, "");
  if (!text) return null;
  if (text.includes(",")) {
    text = text.replace(/\./g, "").replace(",", ".");
  } else if (/^\d{1,3}(\.\d{3})+$/.test(text)) {
    text = text.replace(/\./g, "");
  }
  const match = new RegExp(`^(\\d{1,10})(?:\\.(\\d{1,${places}}))?$`).exec(text);
  if (!match) return null;
  const integer = match[1].replace(/^0+(?=\d)/, "");
  const fraction = (match[2] ?? "").padEnd(places, "0");
  if (/^0+$/.test(integer + fraction)) return null;
  return `${integer}.${fraction}`;
}

const scaled = (value: string, places: number) => {
  const [integer, fraction = ""] = value.split(".");
  return BigInt(integer + fraction.padEnd(places, "0").slice(0, places));
};

/** round(liters × price, 2), half up, as a decimal string ("0.125" × "1.0000" = "0.13"). */
export function estimateAmount(liters: string, price: string): string {
  const product = scaled(liters, 3) * scaled(price, 4); // 7 decimals
  const cents = (product + BigInt(50_000)) / BigInt(100_000);
  const text = cents.toString().padStart(3, "0");
  return `${text.slice(0, -2)}.${text.slice(-2)}`;
}

/** "45.678" → "45,678"; trailing zeros of the decimals are dropped ("10.000" → "10"). */
export function formatQuantity(value: string): string {
  const [integer, fraction = ""] = value.split(".");
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  const decimals = fraction.replace(/0+$/, "");
  return decimals ? `${grouped},${decimals}` : grouped;
}

/** Odometer in whole kilometers, or null when empty; undefined when it is not a valid reading. */
export function parseOdometer(input: string): number | null | undefined {
  const text = input.replace(/[\s.]/g, "");
  if (!text) return null;
  if (!/^\d{1,9}$/.test(text)) return undefined;
  return Number(text);
}
