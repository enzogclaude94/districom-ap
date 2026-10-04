/**
 * Financial requests whose outcome is unknown (network error, timeout, 5xx), kept in `sessionStorage`
 * until they are confirmed or explicitly abandoned, so reopening the form retries with the SAME
 * `client_request_id` and the backend answers with the operation it may already have recorded (ADR 008).
 *
 * One entry per form ("slot": `cash-transfer`, `reverse:42`…) and per user, so different operations never
 * mix. The entry holds exactly what was sent (endpoint and body: account ids, amount, date, description)
 * plus a readable summary; no session data or personal information.
 */

const PREFIX = "districom:settlements:pending:";

export interface PendingOperation {
  slot: string;
  userId: number | null;
  path: string;
  body: Record<string, unknown> & { client_request_id: string };
  summary: string;
  savedAt: string;
}

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

function isPending(value: unknown): value is PendingOperation {
  if (typeof value !== "object" || value === null) return false;
  const entry = value as Partial<PendingOperation>;
  return (
    typeof entry.slot === "string" &&
    typeof entry.path === "string" &&
    typeof entry.summary === "string" &&
    typeof entry.body === "object" &&
    entry.body !== null &&
    typeof entry.body.client_request_id === "string"
  );
}

export function savePending(entry: PendingOperation) {
  try {
    storage()?.setItem(PREFIX + entry.slot, JSON.stringify(entry));
  } catch {
    // Storage full or blocked: the open form still keeps the id for its own retries.
  }
}

export function loadPending(slot: string, userId: number | null): PendingOperation | null {
  try {
    const raw = storage()?.getItem(PREFIX + slot);
    if (!raw) return null;
    const entry: unknown = JSON.parse(raw);
    if (!isPending(entry) || entry.slot !== slot) return null;
    // Another user's attempt in this tab is not this user's to retry (the backend would refuse it anyway).
    return entry.userId === userId ? entry : null;
  } catch {
    return null;
  }
}

export function clearPending(slot: string) {
  try {
    storage()?.removeItem(PREFIX + slot);
  } catch {
    // Nothing to clear.
  }
}

export function listPending(userId: number | null): PendingOperation[] {
  const store = storage();
  if (!store) return [];
  const entries: PendingOperation[] = [];
  for (let i = 0; i < store.length; i += 1) {
    const key = store.key(i);
    if (!key?.startsWith(PREFIX)) continue;
    const entry = loadPending(key.slice(PREFIX.length), userId);
    if (entry) entries.push(entry);
  }
  return entries.sort((a, b) => a.savedAt.localeCompare(b.savedAt));
}
