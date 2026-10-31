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
const MIN_REPORT_YEAR = 1994;
const MAX_REPORT_YEAR = new Date().getFullYear();
const REPORT_YEARS = Array.from(
  { length: MAX_REPORT_YEAR - MIN_REPORT_YEAR + 1 },
  (_, index) => MAX_REPORT_YEAR - index,
);

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
    if (year) {
      reportYear = Number(year);
      if (
        !Number.isInteger(reportYear) ||
        reportYear < MIN_REPORT_YEAR ||
        reportYear > MAX_REPORT_YEAR
      ) {
        setError(
          `Report year must be between ${MIN_REPORT_YEAR} and ${MAX_REPORT_YEAR}.`,
        );
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
        className="absolute inset-0 bg-black/75 backdrop-blur-[3px]"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Index a filing"
        className="relative w-full max-w-lg overflow-hidden rounded-2xl border border-hairline bg-surface shadow-float"
      >
        <div className="flex items-start justify-between gap-4 border-b border-hairline px-6 py-5">
          <div>
            <p className="font-mono text-[9.5px] uppercase tracking-[0.2em] text-lemon">
              Ingestion queue
            </p>
            <h2 className="mt-1.5 font-serif text-display-sm leading-none text-ink">
              Index a filing
            </h2>
            <p className="mt-2 max-w-sm text-[12px] leading-5 text-muted">
              Choose a ticker, form, and report year to enqueue a filing.
              Workers download the document, build structure-aware chunks, and
              embed them for retrieval.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close ingest dialog"
            className="rounded-lg border border-hairline p-2 text-muted transition hover:border-lemon/30 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lemon/40"
          >
            <XIcon className="h-4 w-4" />
          </button>
        </div>

        <div className="px-6 py-5">
          {result ? (
            <div className="rounded-xl border border-hairline bg-surface-soft p-4">
              <div className="flex items-start gap-3">
                <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-lemon/25 bg-lemon/10 text-lemon">
                  {result.cached ? (
                    <DatabaseIcon className="h-4 w-4" />
                  ) : (
                    <CheckIcon className="h-4 w-4" />
                  )}
                </div>
                <div className="min-w-0">
                  <p className="text-[13.5px] font-medium text-ink">
                    Queued for ingestion
                  </p>
                  <p className="mt-1 font-mono text-[11px] leading-5 text-body">
                    {result.ticker} · {result.company}
                  </p>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    <span className="rounded-md border border-hairline px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.12em] text-muted">
                      {result.form_type}
                    </span>
                    {result.year ? (
                      <span className="rounded-md border border-hairline px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.12em] text-muted">
                        {result.year}
                      </span>
                    ) : null}
                    <span className="rounded-md border border-hairline px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.12em] text-muted">
                      CIK {result.cik}
                    </span>
                  </div>
                  <p className="mt-3 text-[11.5px] leading-5 text-muted">
                    {result.cached
                      ? "The company lookup came from cache. A filing job for this form and year was still published to the queue."
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
                  className="mb-1.5 block text-[11px] font-medium text-muted"
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
                  className="h-11 w-full rounded-lg border border-hairline bg-surface-soft px-3.5 font-mono text-[13px] uppercase tracking-[0.08em] text-ink outline-none transition placeholder:text-muted-soft focus:border-lemon/40 focus:ring-2 focus:ring-lemon/10"
                />
              </div>

              <div className="grid grid-cols-[1fr_128px] gap-3">
                <div>
                  <span className="mb-1.5 block text-[11px] font-medium text-muted">
                    Form
                  </span>
                  <div className="flex rounded-lg border border-hairline bg-canvas p-1">
                    {FORM_TYPES.map((option) => (
                      <button
                        key={option}
                        type="button"
                        onClick={() => setFormType(option)}
                        className={`h-8 flex-1 rounded-md font-mono text-[11px] transition ${
                          formType === option
                            ? "bg-surface-elevated text-ink shadow-[inset_0_0_0_1px_var(--color-hairline)]"
                            : "text-muted hover:text-body"
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
                    className="mb-1.5 block text-[11px] font-medium text-muted"
                  >
                    Report year
                  </label>
                  <select
                    id="report-year"
                    value={year}
                    onChange={(event) => setYear(event.target.value)}
                    className="h-10 w-full rounded-lg border border-hairline bg-surface-soft px-3 font-mono text-[12px] text-ink outline-none transition [color-scheme:dark] focus:border-lemon/40 focus:ring-2 focus:ring-lemon/10"
                  >
                    <option value="">Latest</option>
                    {REPORT_YEARS.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {error ? (
                <div className="flex items-start gap-2 rounded-lg border border-danger/20 bg-danger/[0.06] px-3 py-2.5">
                  <AlertIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-danger" />
                  <p className="text-[11.5px] leading-5 text-body">{error}</p>
                </div>
              ) : null}

              {backendStatus === "offline" ? (
                <div className="flex items-start gap-2 rounded-lg border border-warn/20 bg-warn/[0.06] px-3 py-2.5">
                  <AlertIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warn" />
                  <p className="text-[11.5px] leading-5 text-body">
                    The backend appears offline. Start FastAPI on port 8000
                    before queueing a filing.
                  </p>
                </div>
              ) : null}

              <button
                type="submit"
                disabled={submitting || !ticker.trim()}
                className="flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-lemon text-[13px] font-semibold text-on-lemon transition hover:bg-lemon-bright disabled:cursor-not-allowed disabled:bg-surface-elevated disabled:text-muted-soft"
              >
                {submitting ? (
                  <>
                    <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-on-lemon/30 border-t-on-lemon" />
                    Resolving ticker
                  </>
                ) : (
                  <>
                    <DatabaseIcon className="h-4 w-4" />
                    Queue ingestion
                  </>
                )}
              </button>
              <p className="text-center text-[10px] leading-4 text-muted-soft">
                Downloads respect SEC fair-access limits. Large filings can
                take a few minutes to chunk and embed.
              </p>
            </form>
          )}
        </div>

        {result ? (
          <div className="flex gap-2 border-t border-hairline bg-canvas/40 px-6 py-4">
            <button
              type="button"
              onClick={reset}
              className="h-9 flex-1 rounded-lg border border-hairline text-[12px] font-medium text-body transition hover:border-lemon/30 hover:text-ink"
            >
              Queue another
            </button>
            <button
              type="button"
              onClick={onClose}
              className="h-9 flex-1 rounded-lg bg-lemon text-[12px] font-semibold text-on-lemon transition hover:bg-lemon-bright"
            >
              Done
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
