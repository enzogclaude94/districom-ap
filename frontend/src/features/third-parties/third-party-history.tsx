import type { ThirdPartyEvent } from "@/lib/api/third-parties";
import { formatDateTime } from "@/lib/format";

const EVENT_LABELS: Record<string, string> = {
  created: "Alta",
  name_changed: "Nombre corregido",
  tax_id_changed: "CUIT corregido",
  notes_changed: "Notas corregidas",
  activated: "Activado",
  deactivated: "Desactivado",
};

const ORIGINS: Record<string, string> = {
  platform: "desde Terceros",
  "settlements.payment": "desde un pago de Rendiciones",
};

const shown = (value: unknown) => (value === null || value === undefined || value === "" ? "—" : String(value));

function cuit(value: unknown) {
  const digits = typeof value === "string" ? value : "";
  return /^\d{11}$/.test(digits) ? `${digits.slice(0, 2)}-${digits.slice(2, 10)}-${digits.slice(10)}` : shown(value);
}

function lines(event: ThirdPartyEvent): string[] {
  const before = event.before ?? {};
  const after = event.after ?? {};
  const result: string[] = [];
  switch (event.event_type) {
    case "created":
      result.push(`${shown(after.name)} · ${after.tax_id ? `CUIT ${cuit(after.tax_id)}` : "sin CUIT"}`);
      if (typeof after.origin === "string" && ORIGINS[after.origin]) result.push(`Registrado ${ORIGINS[after.origin]}`);
      break;
    case "name_changed":
      result.push(`«${shown(before.name)}» → «${shown(after.name)}»`);
      break;
    case "tax_id_changed":
      result.push(`${cuit(before.tax_id)} → ${cuit(after.tax_id)}`);
      break;
    case "notes_changed":
      result.push(`«${shown(before.notes)}» → «${shown(after.notes)}»`);
      break;
  }
  const confirmation = after.duplicate_confirmation as { reason?: unknown } | undefined;
  if (confirmation) result.push(`Confirmado como distinto de un homónimo: ${shown(confirmation.reason)}`);
  return result;
}

/** The changes of a third party's master data, newest as the backend orders them. */
export function ThirdPartyHistory({ history }: { history: ThirdPartyEvent[] }) {
  return (
    <ol className="space-y-3 text-sm">
      {history.map((event) => (
        <li key={event.id} className="border-l-2 border-border pl-3">
          <p className="font-medium">{EVENT_LABELS[event.event_type] ?? event.event_type}</p>
          {lines(event).map((line) => (
            <p key={line} className="break-words">
              {line}
            </p>
          ))}
          <p className="text-xs text-muted-foreground">
            {formatDateTime(event.occurred_at)} · {event.user_name ?? "Sistema"}
          </p>
        </li>
      ))}
    </ol>
  );
}
