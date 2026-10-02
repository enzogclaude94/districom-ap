/** Shape of every backend error response (ARCHITECTURE.md, CLAUDE.md §4). */
export interface ApiErrorBody {
  code: string;
  message: string;
  details: unknown;
  request_id: string | null;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly details: unknown;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.name = "ApiError";
    this.status = status;
    this.code = body.code;
    this.requestId = body.request_id;
    this.details = body.details;
  }
}

const FALLBACK_MESSAGES: Record<number, string> = {
  0: "No se pudo conectar con el servidor. Revisá tu conexión.",
  500: "Ocurrió un error inesperado.",
  502: "El servidor no está disponible en este momento.",
  503: "El servidor no está disponible en este momento.",
};

function isApiErrorBody(value: unknown): value is ApiErrorBody {
  return typeof value === "object" && value !== null && "code" in value && "message" in value;
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  // Required by the backend CSRF guard on mutating requests.
  headers.set("X-Requested-With", "districom-web");
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, { ...init, headers, credentials: "same-origin" });
  } catch {
    throw new ApiError(0, { code: "network_error", message: FALLBACK_MESSAGES[0], details: null, request_id: null });
  }

  if (response.status === 204) return undefined as T;
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    if (isApiErrorBody(body)) throw new ApiError(response.status, body);
    const message = FALLBACK_MESSAGES[response.status] ?? FALLBACK_MESSAGES[500];
    throw new ApiError(response.status, {
      code: "http_error",
      message,
      details: null,
      request_id: response.headers.get("X-Request-ID"),
    });
  }
  return body as T;
}
