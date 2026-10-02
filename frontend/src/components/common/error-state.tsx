import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api/client";

interface ErrorStateProps {
  error: unknown;
  onRetry?: () => void;
}

/** User-safe error message plus the request id so support can trace it in logs. */
export function ErrorState({ error, onRetry }: ErrorStateProps) {
  const message = error instanceof ApiError ? error.message : "Ocurrió un error inesperado.";
  const requestId = error instanceof ApiError ? error.requestId : null;
  return (
    <div
      role="alert"
      className="flex flex-col items-center gap-3 rounded-lg border border-danger/30 bg-danger-soft px-6 py-10 text-center"
    >
      <AlertTriangle className="size-6 text-danger" aria-hidden />
      <p className="font-medium text-danger">{message}</p>
      {requestId && <p className="font-mono text-xs text-muted-foreground">Código de referencia: {requestId}</p>}
      {onRetry && (
        <Button variant="secondary" size="sm" onClick={onRetry}>
          Reintentar
        </Button>
      )}
    </div>
  );
}
