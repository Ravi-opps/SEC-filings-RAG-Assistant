"use client";

import { useEffect, useState } from "react";

import { ingestFiling } from "@/lib/api";
import {
  AlertIcon,
  CheckIcon,
  DatabaseIcon,
  XIcon,
} from "@/components/icons";
import type { BackendStatus } from "@/lib/use-backend-status";
import type { IngestResponse } from "@/lib/types";

const FORM_TYPES = ["10-K", "10-Q", "8-K"] as const;

export function IngestDialog({
  open,
  backendStatus,
  onClose,
  onQueued,
}: {
  open: boolean;
  backendStatus: BackendStatus;
  onClose: () => void;
  onQueued: () => void;
}) {
  const [ticker, setTicker] = useState("");
  const [formType, setFormType] = useState<string>("10-K");
  const [year, setYear] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<IngestResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onClose]);

  if (!open) return null;

  const reset = () => {
    setResult(null);
    setError(null);
    setSubmitting(false);
  };

  const submit = async () => {
    const normalized = ticker.trim().toUpperCase();
    if (!/^[A-Z][A-Z0-9.-]{0,9}$/.test(normalized)) {
      setError("Enter a valid ticker, for example AAPL or BRK-B.");
      return;
    }

    let reportYear: number | undefined;
    if (year.trim()) {
      reportYear = Number(year);
      if (
        !Number.isInteger(reportYear) ||
        reportYear < 1994 ||
        reportYear > 2100
      ) {
        setError("Report year must be between 1994 and 2100.");
        return;
      }
    }

    setSubmitting(true);
    setError(null);
    setResult(null);
    try {
      const response = await ingestFiling({
        ticker: normalized,
        formType,
        reportYear,
      });
      setResult(response);
      onQueued();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "The ingestion request could not be completed.",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        aria-label="Close ingest dialog"
        onClick={onClose}
        className="absolute inset-0 bg-black/70 backdrop-blur-[3px]"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Index a filing"
        className="relative w-full max-w-lg overflow-hidden rounded-2xl border border-white/10 bg-[#0b1017] shadow-[0_30px_100px_rgba(0,0,0,0.65)]"
      >
        <div className="flex items-start justify-between gap-4 border-b border-white/[0.08] px-6 py-5">
          <div>
            <p className="font-mono text-[9.5px] uppercase tracking-[0.2em] text-emerald-200/70">
              Ingestion queue
            </p>
            <h2 className="mt-1.5 font-serif text-2xl leading-none text-stone-100">
              Index a filing
            </h2>
            <p className="mt-2 max-w-sm text-[12px] leading-5 text-stone-500">
              Resolve a ticker and enqueue a filing. Workers download the
              document, build structure-aware chunks, and embed them for
              retrieval.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close ingest dialog"
            className="rounded-lg border border-white/10 p-2 text-stone-400 transition hover:border-white/20 hover:text-stone-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-300/40"
          >
            <XIcon className="h-4 w-4" />
          </button>
        </div>

        <div className="px-6 py-5">
          {result ? (
            <div
              className={`rounded-xl border p-4 ${
                result.cached
                  ? "border-sky-300/20 bg-sky-300/[0.05]"
                  : "border-emerald-300/20 bg-emerald-300/[0.05]"
              }`}
            >
              <div className="flex items-start gap-3">
                <div
                  className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border ${
                    result.cached
                      ? "border-sky-300/25 bg-sky-300/10 text-sky-200"
                      : "border-emerald-300/25 bg-emerald-300/10 text-emerald-200"
                  }`}
                >
                  {result.cached ? (
                    <DatabaseIcon className="h-4 w-4" />
                  ) : (
                    <CheckIcon className="h-4 w-4" />
                  )}
                </div>
                <div className="min-w-0">
                  <p className="text-[13.5px] font-medium text-stone-100">
                    {result.cached ? "Already resolved" : "Queued for ingestion"}
                  </p>
                  <p className="mt-1 font-mono text-[11px] leading-5 text-stone-400">
                    {result.ticker} · {result.company}
                  </p>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    <span className="rounded-md border border-white/10 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.12em] text-stone-400">
                      {result.form_type}
                    </span>
                    {result.year ? (
                      <span className="rounded-md border border-white/10 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.12em] text-stone-400">
                        {result.year}
                      </span>
                    ) : null}
                    <span className="rounded-md border border-white/10 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.12em] text-stone-400">
                      CIK {result.cik}
                    </span>
                  </div>
                  <p className="mt-3 text-[11.5px] leading-5 text-stone-500">
                    {result.cached
                      ? "Redis already holds this company record, so no new job was published. Clear the cache key if you need to re-resolve and enqueue it."
                      : "The loader and chunk workers are processing it. Once they finish, ask a question about this company and the new passages will be retrievable."}
                  </p>
                </div>
              </div>
            </div>
          ) : (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void submit();
              }}
              className="space-y-4"
            >
              <div>
                <label
                  htmlFor="ticker"
                  className="mb-1.5 block text-[11px] font-medium text-stone-400"
                >
                  Ticker
                </label>
                <input
                  id="ticker"
                  value={ticker}
                  onChange={(event) =>
                    setTicker(event.target.value.toUpperCase())
                  }
                  autoFocus
                  spellCheck={false}
                  autoComplete="off"
                  placeholder="AAPL"
                  className="h-11 w-full rounded-xl border border-white/10 bg-black/30 px-3.5 font-mono text-[13px] uppercase tracking-[0.08em] text-stone-100 outline-none transition placeholder:text-stone-700 focus:border-emerald-300/35 focus:ring-2 focus:ring-emerald-300/10"
                />
              </div>

              <div className="grid grid-cols-[1fr_128px] gap-3">
                <div>
                  <span className="mb-1.5 block text-[11px] font-medium text-stone-400">
                    Form
                  </span>
                  <div className="flex rounded-xl border border-white/10 bg-black/30 p-1">
                    {FORM_TYPES.map((option) => (
                      <button
                        key={option}
                        type="button"
                        onClick={() => setFormType(option)}
                        className={`h-8 flex-1 rounded-lg font-mono text-[11px] transition ${
                          formType === option
                            ? "bg-emerald-300/15 text-emerald-100 shadow-[inset_0_0_0_1px_rgba(110,231,183,0.2)]"
                            : "text-stone-500 hover:text-stone-300"
                        }`}
                      >
                        {option}
                      </button>
                    ))}
                  </div>
                </div>
                <div>
                  <label
                    htmlFor="report-year"
                    className="mb-1.5 block text-[11px] font-medium text-stone-400"
                  >
                    Report year
                  </label>
                  <input
                    id="report-year"
                    value={year}
                    onChange={(event) =>
                      setYear(event.target.value.replace(/[^0-9]/g, ""))
                    }
                    inputMode="numeric"
                    placeholder="Latest"
                    className="h-10 w-full rounded-xl border border-white/10 bg-black/30 px-3 font-mono text-[12px] text-stone-100 outline-none transition placeholder:text-stone-700 focus:border-emerald-300/35 focus:ring-2 focus:ring-emerald-300/10"
                  />
                </div>
              </div>

              {error ? (
                <div className="flex items-start gap-2 rounded-lg border border-red-300/15 bg-red-300/[0.05] px-3 py-2.5">
                  <AlertIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-300/80" />
                  <p className="text-[11.5px] leading-5 text-stone-400">
                    {error}
                  </p>
                </div>
              ) : null}

              {backendStatus === "offline" ? (
                <div className="flex items-start gap-2 rounded-lg border border-amber-300/15 bg-amber-300/[0.05] px-3 py-2.5">
                  <AlertIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-300/80" />
                  <p className="text-[11.5px] leading-5 text-stone-400">
                    The backend appears offline. Start FastAPI on port 8000
                    before queueing a filing.
                  </p>
                </div>
              ) : null}

              <button
                type="submit"
                disabled={submitting || !ticker.trim()}
                className="flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-emerald-300 text-[13px] font-semibold text-[#05100c] transition hover:bg-emerald-200 disabled:cursor-not-allowed disabled:bg-white/[0.06] disabled:text-stone-600"
              >
                {submitting ? (
                  <>
                    <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-[#05100c]/30 border-t-[#05100c]" />
                    Resolving ticker
                  </>
                ) : (
                  <>
                    <DatabaseIcon className="h-4 w-4" />
                    Queue ingestion
                  </>
                )}
              </button>
              <p className="text-center text-[10px] leading-4 text-stone-600">
                Downloads respect SEC fair-access limits. Large filings can
                take a few minutes to chunk and embed.
              </p>
            </form>
          )}
        </div>

        {result ? (
          <div className="flex gap-2 border-t border-white/[0.08] bg-black/20 px-6 py-4">
            <button
              type="button"
              onClick={reset}
              className="h-9 flex-1 rounded-lg border border-white/10 text-[12px] font-medium text-stone-300 transition hover:border-white/20 hover:text-stone-100"
            >
              Queue another
            </button>
            <button
              type="button"
              onClick={onClose}
              className="h-9 flex-1 rounded-lg bg-emerald-300 text-[12px] font-semibold text-[#05100c] transition hover:bg-emerald-200"
            >
              Done
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
