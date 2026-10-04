/** Display formats for business values (es-AR). Business dates travel as ISO `YYYY-MM-DD`. */

export function formatDate(iso: string | null | undefined) {
  if (!iso) return "—";
  const [year, month, day] = iso.split("-");
  return `${day}/${month}/${year}`;
}

/** Today as ISO date in the browser's calendar, for form defaults (the backend validates). */
export function todayIso() {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

/**
 * Argentine money format for a decimal string from the API ("-1234.5" → "-$ 1.234,50").
 * Works on the string, never on a float, so large amounts keep every cent.
 */
export function formatMoney(value: string, { signed = false }: { signed?: boolean } = {}) {
  const trimmed = value.trim();
  const negative = trimmed.startsWith("-");
  const [integer = "0", fraction = ""] = trimmed.replace(/^[-+]/, "").split(".");
  const grouped = (integer.replace(/^0+(?=\d)/, "") || "0").replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  const cents = fraction.padEnd(2, "0").slice(0, 2);
  const zero = /^0*$/.test(integer + cents);
  const sign = negative && !zero ? "-" : signed && !zero ? "+" : "";
  return `${sign}$ ${grouped},${cents}`;
}

/** Instant (timestamptz) as local date and time: "04/10/2026 14:05". */
export function formatDateTime(iso: string) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(date.getDate())}/${pad(date.getMonth() + 1)}/${date.getFullYear()} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
