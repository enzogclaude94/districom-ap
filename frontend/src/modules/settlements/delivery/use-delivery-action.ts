"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { authApi, currentUserQueryKey } from "@/lib/api/auth";
import { cashQueryKey } from "@/modules/settlements/cash/api";
import { chequeQueryKey } from "@/modules/settlements/cheques/api";
import { newRequestId } from "@/modules/settlements/cash/request-id";
import { pendingQueryKey } from "@/modules/settlements/pending/api";

import { deliveryApi, deliveryQueryKey, outletSettlementQueryKey, type Settlement } from "./api";
import { deliveryErrorView } from "./errors";

export type Method = "POST" | "PUT" | "PATCH";

export interface DeliveryRequest {
  method: Method;
  path: string;
  body: Record<string, unknown>;
  /** Readable line shown if the outcome is unknown: "Agregado de $ 1.000,00 a Caja principal". */
  summary: string;
}

export interface StoredRequest extends DeliveryRequest {
  slot: string;
  userId: number | null;
  savedAt: string;
}

/**
 * Requests whose outcome is unknown (network error, timeout, 5xx), kept in `sessionStorage` until they are
 * confirmed or explicitly abandoned. Separate from the Cajas ones: a delivery attempt is retried from its
 * settlement and a pending collection's from its pending, never from the Cajas board.
 */
const PREFIX = "districom:settlements:delivery-pending:";

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

function isStored(value: unknown): value is StoredRequest {
  if (typeof value !== "object" || value === null) return false;
  const entry = value as Partial<StoredRequest>;
  return (
    typeof entry.slot === "string" &&
    typeof entry.path === "string" &&
    typeof entry.method === "string" &&
    typeof entry.summary === "string" &&
    typeof entry.body === "object" &&
    entry.body !== null
  );
}

export function loadStored(slot: string, userId: number | null): StoredRequest | null {
  try {
    const raw = storage()?.getItem(PREFIX + slot);
    if (!raw) return null;
    const entry: unknown = JSON.parse(raw);
    if (!isStored(entry) || entry.slot !== slot) return null;
    return entry.userId === userId ? entry : null;
  } catch {
    return null;
  }
}

export function saveStored(entry: StoredRequest) {
  try {
    storage()?.setItem(PREFIX + entry.slot, JSON.stringify(entry));
  } catch {
    // Storage full or blocked: the open form still keeps the request for its own retries.
  }
}

export function clearStored(slot: string) {
  try {
    storage()?.removeItem(PREFIX + slot);
  } catch {
    // Nothing to clear.
  }
}

/**
 * One write whose outcome must never be duplicated (ADR 008), parameterized by what it writes and reads.
 *
 * As in Cajas: the `client_request_id` of a creation is generated when the form opens and kept for every
 * retry of that form; a request whose outcome is unknown is kept under the form's `slot` and reopening the
 * form offers to resend exactly the same request (the backend answers with what it already recorded) or,
 * after a warning, to abandon it. A second click while a request is in flight is ignored. After every
 * answer the given queries are re-read from the API.
 */
export function useWriteAction<T>(options: {
  slot: string;
  write: (request: DeliveryRequest) => Promise<T>;
  invalidate: readonly (readonly unknown[])[];
  isUncertain: (error: unknown) => boolean;
  onDone: (result: T) => void;
}) {
  const { slot, write, invalidate, isUncertain, onDone } = options;
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const userId = me.data?.id ?? null;
  const [stored, setStored] = useState<StoredRequest | null>(() => loadStored(slot, userId));
  const [requestId, setRequestId] = useState(() => {
    const id = stored?.body.client_request_id;
    return typeof id === "string" ? id : newRequestId();
  });
  const inFlight = useRef(false);

  const refresh = () => {
    for (const queryKey of invalidate) void queryClient.invalidateQueries({ queryKey });
  };

  const mutation = useMutation({
    mutationFn: write,
    onSuccess: (result) => {
      clearStored(slot);
      setStored(null);
      refresh();
      onDone(result);
    },
    onError: (error, request) => {
      if (isUncertain(error)) {
        const entry = { slot, userId, ...request, savedAt: new Date().toISOString() };
        saveStored(entry);
        setStored(entry);
      } else {
        clearStored(slot);
        setStored(null);
      }
      refresh();
    },
  });

  const send = (request: DeliveryRequest) => {
    if (inFlight.current) return;
    inFlight.current = true;
    mutation.mutate(request, {
      onSettled: () => {
        inFlight.current = false;
      },
    });
  };

  const forget = () => {
    clearStored(slot);
    setStored(null);
    setRequestId(newRequestId());
    mutation.reset();
  };

  return {
    requestId,
    mutation: mutation as { isPending: boolean; error: Error | null; reset: () => void },
    stored,
    submit: (build: (requestId: string) => DeliveryRequest) => send(build(requestId)),
    /** Resends the stored attempt exactly as it was (same id, same body). */
    retryStored: () => {
      if (stored) send({ method: stored.method, path: stored.path, body: stored.body, summary: stored.summary });
    },
    /** Gives up the stored attempt after the user was warned; the next submit uses a new id. */
    abandonStored: forget,
  };
}

/** A write action of any document: what the shared dialogs (`ActionDialog`, `PendingAttempt`) need. */
export type WriteAction = ReturnType<typeof useWriteAction<unknown>>;

/** One write on a settlement (create it, add cash, declare a transfer, close, reopen…). */
export function useDeliveryAction(slot: string, onDone: (settlement: Settlement) => void) {
  return useWriteAction<Settlement>({
    slot,
    write: (request) => deliveryApi.write(request.method, request.path, request.body),
    invalidate: [deliveryQueryKey, outletSettlementQueryKey, cashQueryKey, pendingQueryKey, chequeQueryKey],
    isUncertain: (error) => Boolean(deliveryErrorView(error).uncertain),
    onDone,
  });
}

export type DeliveryAction = WriteAction;
