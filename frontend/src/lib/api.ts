import type { IngestResponse, QueryResponse } from "./types";

const BASE_PATH = "/api/backend";

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
    const response = await fetch(`${BASE_PATH}${path}`, {
      ...init,
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
