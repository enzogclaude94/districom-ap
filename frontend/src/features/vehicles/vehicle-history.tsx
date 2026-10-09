import type { VehicleEvent } from "@/lib/api/vehicles";
import { formatDateTime } from "@/lib/format";

const EVENT_LABELS: Record<string, string> = {
  created: "Alta",
  plate_changed: "Patente corregida",
  description_changed: "Descripción corregida",
  activated: "Activado",
  deactivated: "Desactivado",
};

function change(event: VehicleEvent): string {
  const field = (side: Record<string, unknown> | null, key: string) => String(side?.[key] ?? "—");
  switch (event.event_type) {
    case "created":
      return `${field(event.after, "plate")} · ${field(event.after, "description")}`;
    case "plate_changed":
      return `${field(event.before, "plate")} → ${field(event.after, "plate")}`;
    case "description_changed":
      return `«${field(event.before, "description")}» → «${field(event.after, "description")}»`;
    default:
      return "";
  }
}

/** The changes of a vehicle's master data (plate, description, active), newest as the backend orders them. */
export function VehicleHistory({ history }: { history: VehicleEvent[] }) {
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
