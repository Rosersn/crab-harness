import { getBackendBaseURL } from "@/core/config";
import { fetchJson } from "@/core/http/fetch";

export interface AuthUser {
  user_id: string;
  tenant_id: string;
  email: string;
  role: string;
}

interface AuthTokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface RegisterRequest extends LoginRequest {
  tenant_name?: string;
}

export async function loadCurrentUser(): Promise<AuthUser> {
  return fetchJson<AuthUser>(`${getBackendBaseURL()}/api/auth/me`, undefined, {
    fallbackMessage: "Failed to load the current session",
  });
}

export async function login(request: LoginRequest): Promise<AuthTokenResponse> {
  return fetchJson<AuthTokenResponse>(
    `${getBackendBaseURL()}/api/auth/login`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
    },
    {
      retryOnUnauthorized: false,
      fallbackMessage: "Login failed",
    },
  );
}

export async function register(
  request: RegisterRequest,
): Promise<AuthTokenResponse> {
  return fetchJson<AuthTokenResponse>(
    `${getBackendBaseURL()}/api/auth/register`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
    },
    {
      retryOnUnauthorized: false,
      fallbackMessage: "Registration failed",
    },
  );
}

export async function logout(): Promise<void> {
  await fetchJson<{ success: boolean }>(
    `${getBackendBaseURL()}/api/auth/logout`,
    {
      method: "POST",
    },
    {
      retryOnUnauthorized: false,
      fallbackMessage: "Logout failed",
    },
  );
}
