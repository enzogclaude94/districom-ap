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
