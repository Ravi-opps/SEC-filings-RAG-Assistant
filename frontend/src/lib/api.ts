import type {
  AuthResponse,
  AuthUser,
  IngestResponse,
  QueryResponse,
} from "./types";

const BASE_PATH = "/api/backend";
const TOKEN_KEY = "filing-room:token:v1";
const USER_KEY = "filing-room:user:v1";

export const AUTH_EVENT = "filing-room:auth";

export function readToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function readStoredUser(): AuthUser | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(USER_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as AuthUser;
    if (typeof parsed?.id !== "number" || typeof parsed?.username !== "string") {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export function storeSession(session: AuthResponse): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(TOKEN_KEY, session.access_token);
    window.localStorage.setItem(USER_KEY, JSON.stringify(session.user));
  } catch {
    // Storage may be disabled; the session still works for this page.
  }
}

export function clearSession(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(USER_KEY);
  } catch {
    // Ignore storage failures.
  }
  window.dispatchEvent(new Event(AUTH_EVENT));
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function readDetail(payload: unknown): string | null {
  if (!payload || typeof payload !== "object") return null;
  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (item && typeof item === "object" && "msg" in item) {
          return String((item as { msg: unknown }).msg);
        }
        return null;
      })
      .filter(Boolean);
    if (parts.length) return parts.join("; ");
  }
  return null;
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  timeoutMs = 30_000,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const externalSignal = init.signal;
  const onAbort = () => controller.abort();
  externalSignal?.addEventListener("abort", onAbort, { once: true });

  try {
    const headers = new Headers(init.headers);
    const token = readToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);

    const response = await fetch(`${BASE_PATH}${path}`, {
      ...init,
      headers,
      signal: controller.signal,
      cache: "no-store",
    });

    const text = await response.text();
    let payload: unknown = null;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        payload = text;
      }
    }

    if (!response.ok) {
      if (response.status === 401 && token && !path.startsWith("/auth/")) {
        clearSession();
      }
      const detail =
        readDetail(payload) ??
        (response.status === 503
          ? "The backend is unavailable. Start the FastAPI service on port 8000."
          : `Request failed with status ${response.status}.`);
      throw new ApiError(detail, response.status);
    }

    return payload as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError(
        "The request timed out. The retrieval pipeline may still be working.",
      );
    }
    throw new ApiError(
      "Could not reach the retrieval backend. Make sure FastAPI is running on port 8000.",
    );
  } finally {
    clearTimeout(timer);
    externalSignal?.removeEventListener("abort", onAbort);
  }
}

export function askQuestion(
  prompt: string,
  conversationId: string,
): Promise<QueryResponse> {
  return request<QueryResponse>(
    "/query",
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Conversation-Id": conversationId,
      },
      body: JSON.stringify({ "user prompt": prompt }),
    },
    180_000,
  );
}

export function ingestFiling(input: {
  ticker: string;
  formType: string;
  reportYear?: number;
}): Promise<IngestResponse> {
  const params = new URLSearchParams({ form_type: input.formType });
  if (input.reportYear) params.set("report_year", String(input.reportYear));

  return request<IngestResponse>(
    `/ingest/${encodeURIComponent(input.ticker)}?${params.toString()}`,
    { method: "POST" },
    45_000,
  );
}

export function checkBackend(): Promise<{ service: string }> {
  return request<{ service: string }>("/", {}, 6_000);
}

export function signup(
  username: string,
  password: string,
): Promise<AuthResponse> {
  return request<AuthResponse>(
    "/auth/signup",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    },
    15_000,
  );
}

export function login(
  username: string,
  password: string,
): Promise<AuthResponse> {
  return request<AuthResponse>(
    "/auth/login",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    },
    15_000,
  );
}

export function fetchCurrentUser(): Promise<AuthUser> {
  return request<AuthUser>("/auth/me", {}, 10_000);
}
