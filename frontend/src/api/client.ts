export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : "API error");
    this.status = status;
    this.detail = detail;
  }
}

let unauthorizedHandler: (() => void) | null = null;
let lastUnauthorizedHandled = 0;

export function setUnauthorizedHandler(fn: () => void) {
  unauthorizedHandler = fn;
}

const SKIP_401_REDIRECT = new Set([
  "/api/auth/login",
  "/api/auth/me",
]);

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    if (response.status === 401 && unauthorizedHandler && !SKIP_401_REDIRECT.has(path.split("?")[0])) {
      const now = Date.now();
      if (now - lastUnauthorizedHandled > 2000) {
        lastUnauthorizedHandled = now;
        unauthorizedHandler();
      }
    }
    throw new ApiError(response.status, detail?.detail ?? response.statusText);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
