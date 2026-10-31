import type {
  AuthResponse,
  AuthUser,
  IngestResponse,
  IngestStatusResponse,
  QueryResponse,
  Source,
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

export interface QueryStreamHandlers {
  onStatus?: (stage: string) => void;
  onSources?: (sources: Source[]) => void;
  onDelta?: (text: string) => void;
}

export async function askQuestionStream(
  prompt: string,
  conversationId: string,
  handlers: QueryStreamHandlers = {},
): Promise<QueryResponse> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 240_000);
  const token = readToken();

  try {
    const headers = new Headers({
      "Content-Type": "application/json",
      "X-Conversation-Id": conversationId,
      Accept: "text/event-stream",
    });
    if (token) headers.set("Authorization", `Bearer ${token}`);

    const response = await fetch(`${BASE_PATH}/query/stream`, {
      method: "POST",
      headers,
      body: JSON.stringify({ "user prompt": prompt }),
      signal: controller.signal,
      cache: "no-store",
    });

    if (!response.ok) {
      const text = await response.text();
      let payload: unknown = text;
      try {
        payload = text ? JSON.parse(text) : null;
      } catch {
        payload = text;
      }
      if (response.status === 401 && token) clearSession();
      const detail =
        readDetail(payload) ??
        (response.status === 503
          ? "The backend is unavailable. Start the FastAPI service on port 8000."
          : `Request failed with status ${response.status}.`);
      throw new ApiError(detail, response.status);
    }

    if (!response.body) {
      throw new ApiError("Streaming is not supported by this browser.");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let final: QueryResponse | null = null;

    const handleFrame = (frame: string) => {
      let eventName = "message";
      const dataLines: string[] = [];
      for (const rawLine of frame.split("\n")) {
        const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
        if (!line || line.startsWith(":")) continue;
        if (line.startsWith("event:")) {
          eventName = line.slice("event:".length).trim();
        } else if (line.startsWith("data:")) {
          dataLines.push(line.slice("data:".length).replace(/^ /, ""));
        }
      }
      if (!dataLines.length) return;

      let payload: unknown = null;
      try {
        payload = JSON.parse(dataLines.join("\n"));
      } catch {
        return;
      }

      if (eventName === "status") {
        const stage = (payload as { stage?: unknown } | null)?.stage;
        if (typeof stage === "string") handlers.onStatus?.(stage);
        return;
      }
      if (eventName === "sources") {
        if (Array.isArray(payload)) handlers.onSources?.(payload as Source[]);
        return;
      }
      if (eventName === "delta") {
        const text = (payload as { text?: unknown } | null)?.text;
        if (typeof text === "string") handlers.onDelta?.(text);
        return;
      }
      if (eventName === "error") {
        const detail = (payload as { detail?: unknown } | null)?.detail;
        const status = (payload as { status?: unknown } | null)?.status;
        throw new ApiError(
          typeof detail === "string"
            ? detail
            : "The retrieval stream failed.",
          typeof status === "number" ? status : undefined,
        );
      }
      if (eventName === "done") {
        final = payload as QueryResponse;
      }
    };

    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let boundary = buffer.indexOf("\n\n");
        while (boundary !== -1) {
          const frame = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          handleFrame(frame);
          boundary = buffer.indexOf("\n\n");
        }
      }
      buffer += decoder.decode();
      if (buffer.trim()) handleFrame(buffer);
    } finally {
      await reader.cancel().catch(() => undefined);
    }

    if (!final) {
      throw new ApiError(
        "The response stream ended before the answer was complete.",
      );
    }
    return final;
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
    controller.abort();
  }
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

export function fetchIngestStatus(params: {
  ticker?: string;
  formType?: string;
  reportYear?: number;
  documentId?: string;
} = {}): Promise<IngestStatusResponse> {
  const query = new URLSearchParams();
  if (params.ticker) query.set("ticker", params.ticker);
  if (params.formType) query.set("form_type", params.formType);
  if (params.reportYear) {
    query.set("report_year", String(params.reportYear));
  }
  if (params.documentId) query.set("document_id", params.documentId);

  const queryString = query.toString();
  const suffix = queryString ? `?${queryString}` : "";
  return request<IngestStatusResponse>(
    `/ingest/status${suffix}`,
    {},
    120_000,
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
