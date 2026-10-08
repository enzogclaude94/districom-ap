"use client";

import { cashQueryKey } from "@/modules/settlements/cash/api";
import { chequeQueryKey } from "@/modules/settlements/cheques/api";
import { deliveryQueryKey } from "@/modules/settlements/delivery/api";
import { useWriteAction } from "@/modules/settlements/delivery/use-delivery-action";

import { pendingApi, pendingQueryKey, type PendingDetail } from "./api";
import { pendingErrorView } from "./errors";

/**
 * One write on a pending already in Cajas (receipt, resolution, reversals, responsible), with the same
 * idempotency as the settlement and Cajas forms (ADR 008): one `client_request_id` per opening of the
 * form, kept for every retry, and an attempt with an unknown outcome kept until it is confirmed or
 * explicitly abandoned. Pending, settlement and Cajas are re-read after every answer.
 */
export function usePendingAction(slot: string, onDone: (pending: PendingDetail) => void) {
  return useWriteAction<PendingDetail>({
    slot: `pending:${slot}`,
    write: (request) => pendingApi.write(request.method, request.path, request.body),
    invalidate: [pendingQueryKey, deliveryQueryKey, cashQueryKey, chequeQueryKey],
    isUncertain: (error) => Boolean(pendingErrorView(error).uncertain),
    onDone,
  });
}
