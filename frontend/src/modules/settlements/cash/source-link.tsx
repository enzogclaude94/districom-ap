import Link from "next/link";

import { sourceHref, sourceLabel } from "./labels";

/** "Pendiente de cobro #12" as a link to the pending; plain text for manual operations and other documents. */
export function SourceLink({ sourceType, sourceId }: { sourceType: string | null; sourceId: number | null }) {
  const label = sourceLabel(sourceType, sourceId);
  const href = sourceHref(sourceType, sourceId);
  return href ? (
    <Link href={href} className="text-primary hover:underline">
      {label}
    </Link>
  ) : (
    <>{label}</>
  );
}
