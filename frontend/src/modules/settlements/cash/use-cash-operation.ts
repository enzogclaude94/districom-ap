"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { cashQueryKey, type Operation } from "@/modules/settlements/cash/api";

import { cashErrorView } from "./errors";
import { newRequestId } from "./request-id";

/**
 * One financial action of the user, with its `client_request_id` (ADR 008).
 *
 * The id is created when the form opens and kept for every retry of that form, including after a timeout
 * or a network error whose outcome is unknown: the backend answers a repeated request with the operation
 * it already recorded instead of recording it twice. Only `restart()` (after an `idempotency_conflict`) or
 * opening the form again creates a new id. Balances and movements are always re-read from the API.
 */
export function useCashOperation(
  send: (requestId: string) => Promise<Operation>,
  onDone: (operation: Operation) => void,
) {
  const queryClient = useQueryClient();
  const [requestId, setRequestId] = useState(newRequestId);
  // Blocks a second click before the pending state re-renders the button (even then the id would dedupe it).
  const inFlight = useRef(false);
  const mutation = useMutation({
    mutationFn: () => send(requestId),
    onSuccess: (operation) => {
      void queryClient.invalidateQueries({ queryKey: cashQueryKey });
      onDone(operation);
    },
    onError: (error) => {
      // It may have been recorded: show the real balances while the user decides to retry.
      if (cashErrorView(error).uncertain) void queryClient.invalidateQueries({ queryKey: cashQueryKey });
    },
  });
  return {
    requestId,
    mutation,
    submit: () => {
      if (inFlight.current) return;
      inFlight.current = true;
      mutation.mutate(undefined, {
        onSettled: () => {
          inFlight.current = false;
        },
      });
    },
    restart: () => {
      setRequestId(newRequestId());
      mutation.reset();
    },
  };
}
