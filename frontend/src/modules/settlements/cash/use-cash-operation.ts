"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { authApi, currentUserQueryKey } from "@/lib/api/auth";
import { cashApi, cashQueryKey, type Operation } from "@/modules/settlements/cash/api";

import { cashErrorView } from "./errors";
import { clearPending, loadPending, savePending, type PendingOperation } from "./pending-operations";
import { newRequestId } from "./request-id";

export interface CashRequest {
  path: string;
  body: Record<string, unknown> & { client_request_id: string };
  summary: string;
}

/**
 * One financial action of the user, with its `client_request_id` (ADR 008).
 *
 * The id is created when the form opens and kept for every retry of that form. When the outcome of a
 * request is unknown (network error, timeout, 5xx) the exact request is also kept in `sessionStorage`
 * under the form's `slot`: reopening the form offers to retry it with the same id, and only an explicit
 * abandon (after a warning) starts a new one. A confirmed answer, success or a definitive rejection,
 * clears it. Balances and movements are always re-read from the API.
 */
export function useCashOperation(slot: string, onDone: (operation: Operation) => void) {
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const userId = me.data?.id ?? null;
  // Read once, when the form opens: a pending attempt from before decides the request id.
  const [stored, setStored] = useState<PendingOperation | null>(() => loadPending(slot, userId));
  const [requestId, setRequestId] = useState(() => stored?.body.client_request_id ?? newRequestId());
  // Blocks a second click before the pending state re-renders the button (even then the id would dedupe it).
  const inFlight = useRef(false);

  const mutation = useMutation({
    mutationFn: (request: CashRequest) => cashApi.send(request.path, request.body),
    onSuccess: (operation) => {
      clearPending(slot);
      setStored(null);
      void queryClient.invalidateQueries({ queryKey: cashQueryKey });
      onDone(operation);
    },
    onError: (error, request) => {
      if (cashErrorView(error).uncertain) {
        savePending({ slot, userId, ...request, savedAt: new Date().toISOString() });
        // It may have been recorded: show the real balances while the user decides to retry.
        void queryClient.invalidateQueries({ queryKey: cashQueryKey });
      } else {
        // A definitive answer: nothing was recorded under this id with this content.
        clearPending(slot);
        setStored(null);
      }
    },
  });

  const send = (request: CashRequest) => {
    if (inFlight.current) return;
    inFlight.current = true;
    mutation.mutate(request, {
      onSettled: () => {
        inFlight.current = false;
      },
    });
  };

  return {
    requestId,
    mutation,
    /** Attempt from an earlier opening of this form whose outcome is still unknown. */
    stored,
    submit: (build: (requestId: string) => CashRequest) => send(build(requestId)),
    /** Resends the stored attempt exactly as it was (same id, same body). */
    retryStored: () => {
      if (stored) send({ path: stored.path, body: stored.body, summary: stored.summary });
    },
    /** Gives up the stored attempt after the user was warned; the next submit uses a new id. */
    abandonStored: () => {
      clearPending(slot);
      setStored(null);
      setRequestId(newRequestId());
      mutation.reset();
    },
    restart: () => {
      clearPending(slot);
      setStored(null);
      setRequestId(newRequestId());
      mutation.reset();
    },
  };
}
