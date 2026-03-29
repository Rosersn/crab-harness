import { getBackendBaseURL } from "@/core/config";

const AUTH_RETRY_EXCLUDED_PATHS = new Set([
  "/api/auth/login",
  "/api/auth/logout",
  "/api/auth/refresh",
  "/api/auth/register",
]);

const REQUEST_BASE_URL =
  typeof window !== "undefined" ? window.location.origin : "http://localhost";

let refreshPromise: Promise<boolean> | null = null;

function withCredentials(init?: RequestInit): RequestInit {
  return {
    ...init,
    credentials: init?.credentials ?? "include",
  };
}

function normalizeRequest(
  input: RequestInfo | URL,
  init?: RequestInit,
): Request {
  return new Request(input, withCredentials(init));
}

function getRequestPath(request: Request): string {
  return new URL(request.url, REQUEST_BASE_URL).pathname;
}

async function refreshSession(): Promise<boolean> {
  if (refreshPromise) {
    return refreshPromise;
  }

  refreshPromise = fetch(
    `${getBackendBaseURL()}/api/auth/refresh`,
    withCredentials({
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: "{}",
    }),
  )
    .then((response) => response.ok)
    .catch(() => false)
    .finally(() => {
      refreshPromise = null;
    });

  return refreshPromise;
}

export async function fetchWithAuth(
  input: RequestInfo | URL,
  init?: RequestInit,
  {
    retryOnUnauthorized = true,
  }: {
    retryOnUnauthorized?: boolean;
  } = {},
): Promise<Response> {
  const request = normalizeRequest(input, init);
  const response = await fetch(request.clone());

  if (!retryOnUnauthorized || response.status !== 401) {
    return response;
  }

  if (AUTH_RETRY_EXCLUDED_PATHS.has(getRequestPath(request))) {
    return response;
  }

  const refreshed = await refreshSession();
  if (!refreshed) {
    return response;
  }

  return fetch(request.clone());
}

export async function readErrorDetail(
  response: Response,
  fallback: string,
): Promise<string> {
  const error = await response.json().catch(() => ({ detail: fallback }));
  if (typeof error.detail === "string" && error.detail.trim().length > 0) {
    return error.detail;
  }
  return fallback;
}

export async function fetchJson<T>(
  input: RequestInfo | URL,
  init?: RequestInit,
  options?: {
    retryOnUnauthorized?: boolean;
    fallbackMessage?: string;
  },
): Promise<T> {
  const response = await fetchWithAuth(input, init, {
    retryOnUnauthorized: options?.retryOnUnauthorized,
  });

  if (!response.ok) {
    throw new Error(
      await readErrorDetail(
        response,
        options?.fallbackMessage ?? response.statusText,
      ),
    );
  }

  return response.json() as Promise<T>;
}
