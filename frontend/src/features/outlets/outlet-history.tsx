import type { OutletEvent } from "@/lib/api/outlets";
import { formatDateTime } from "@/lib/format";

const EVENT_LABELS: Record<string, string> = {
  created: "Alta",
  name_changed: "Nombre corregido",
  description_changed: "Descripción corregida",
  deactivated: "Desactivada",
  reactivated: "Reactivada",
};

function change(event: OutletEvent): string {
  const field = (side: Record<string, unknown> | null, key: string) => String(side?.[key] ?? "—");
  switch (event.event_type) {
    case "created":
      return `${field(event.after, "code")} · ${field(event.after, "name")}`;
    case "name_changed":
      return `«${field(event.before, "name")}» → «${field(event.after, "name")}»`;
    case "description_changed":
      return `«${field(event.before, "description")}» → «${field(event.after, "description")}»`;
    default:
      return "";
  }
}

/** The changes of a boca's master data (name, description, active). The code never appears: it never changes. */
export function OutletHistory({ history }: { history: OutletEvent[] }) {
  return (
    <ol className="space-y-3 text-sm">
      {history.map((event) => (
        <li key={event.id} className="border-l-2 border-border pl-3">
          <p className="font-medium">{EVENT_LABELS[event.event_type] ?? event.event_type}</p>
          {change(event) && <p className="break-words">{change(event)}</p>}
          <p className="text-xs text-muted-foreground">
            {formatDateTime(event.occurred_at)} · {event.user_name ?? "Sistema"}
          </p>
        </li>
      ))}
    </ol>
  );
}
