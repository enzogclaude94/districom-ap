/**
 * Reads an amount typed the Argentine way ("1.500,50", "1500,5", "1500.50", "$ 2.000") into the decimal
 * string the API expects ("1500.50"), or null when it is not a valid positive amount with up to two
 * decimals. A dot followed by exactly three digits is a thousands separator ("1.500" = 1500).
 */
export function parseAmount(input: string): string | null {
  let text = input.replace(/[\s$]/g, "");
  if (!text) return null;
  if (text.includes(",")) {
    text = text.replace(/\./g, "").replace(",", ".");
  } else if (/^\d{1,3}(\.\d{3})+$/.test(text)) {
    text = text.replace(/\./g, "");
  }
  const match = /^(\d{1,12})(?:\.(\d{1,2}))?$/.exec(text);
  if (!match) return null;
  const integer = match[1].replace(/^0+(?=\d)/, "");
  const cents = (match[2] ?? "").padEnd(2, "0");
  if (/^0+$/.test(integer + cents)) return null;
  return `${integer}.${cents}`;
}
