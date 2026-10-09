"use client";

import { cashQueryKey } from "@/modules/settlements/cash/api";
import { deliveryQueryKey } from "@/modules/settlements/delivery/api";
import { useWriteAction } from "@/modules/settlements/delivery/use-delivery-action";
import { pendingQueryKey } from "@/modules/settlements/pending/api";

import { chequeApi, chequeQueryKey } from "./api";
import { chequeErrorView } from "./errors";

/**
 * One write of the cheque cycle (deposit, crediting, rejection, return, reversals), with the same
 * idempotency as every other Rendiciones form (ADR 008): one `client_request_id` per opening of the form,
 * kept for every retry, and an attempt with an unknown outcome kept until it is confirmed or explicitly
 * abandoned. Cheques, Cajas, pendings and settlements are read again after every answer.
 */
export function useChequeAction<T>(slot: string, onDone: (result: T) => void) {
  return useWriteAction<T>({
    slot: `cheque:${slot}`,
    write: (request) => chequeApi.write<T>(request.path, request.body),
    invalidate: [chequeQueryKey, cashQueryKey, pendingQueryKey, deliveryQueryKey],
    isUncertain: (error) => Boolean(chequeErrorView(error).uncertain),
    onDone,
  });
}

export const chequeErrors = (error: unknown) => chequeErrorView(error);
