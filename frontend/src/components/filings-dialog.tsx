"use client";

import { useEffect, useMemo, useState } from "react";

import {
  AlertIcon,
  ArrowUpRightIcon,
  FilesIcon,
  RefreshIcon,
  SearchIcon,
  XIcon,
} from "@/components/icons";
import { listFilings } from "@/lib/api";
import type { FilingInfo } from "@/lib/types";

const STATUS_META: Record<string, { label: string; tone: string }> = {
  embedded: {
    label: "Embedded",
    tone: "border-lemon/30 bg-lemon/10 text-lemon-deep",
  },
  indexed: {
    label: "Indexed",
    tone: "border-lemon/30 bg-lemon/10 text-lemon-deep",
  },
  complete: {
    label: "Complete",
    tone: "border-lemon/30 bg-lemon/10 text-lemon-deep",
  },
  chunked: {
    label: "Chunked",
    tone: "border-hairline bg-canvas text-muted",
  },
  queued: {
    label: "Queued",
    tone: "border-warn/25 bg-warn/10 text-warn",
  },
  in_progress: {
    label: "In progress",
    tone: "border-warn/25 bg-warn/10 text-warn",
  },
};

function statusMeta(status: string) {
  const key = status.trim().toLowerCase();
  return (
    STATUS_META[key] ?? {
      label: status ? status.replaceAll("_", " ") : "Unknown",
      tone: "border-hairline bg-canvas text-muted",
    }
  );
}

function formatDate(value: string): string {
  if (!value) return "";
  const parsed = new Date(
    value.length === 10 ? `${value}T00:00:00` : value,
  );
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function filingTitle(filing: FilingInfo): string {
  if (filing.title) return filing.title;
  const company = filing.company || filing.ticker || filing.document_id;
  return `${company}${filing.form_type ? ` ${filing.form_type}` : ""}`.trim();
}

export function FilingsDialog({ onClose }: { onClose: () => void }) {
  const [filings, setFilings] = useState<FilingInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
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
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    listFilings()
      .then((response) => {
        if (!cancelled) setFilings(response.filings);
      })
      .catch((caught) => {
        if (!cancelled) {
          setError(
            caught instanceof Error
              ? caught.message
              : "The filing list could not be loaded.",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return filings;
    return filings.filter((filing) =>
      [
        filing.document_id,
        filing.ticker,
        filing.company,
        filing.form_type,
        filing.report_date,
        filing.filing_date,
        filing.accession_number,
        filing.cik,
        filing.title,
        filing.status,
      ].some((value) => value.toLowerCase().includes(needle)),
    );
  }, [filings, query]);

  const refresh = () => {
    setLoading(true);
    setError(null);
    setRefreshKey((current) => current + 1);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        aria-label="Close filings dialog"
        onClick={onClose}
        className="absolute inset-0 bg-black/40 backdrop-blur-[3px]"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Available filings"
        className="relative flex max-h-[86vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-hairline-strong bg-canvas shadow-float"
      >
        <div className="flex items-start justify-between gap-4 border-b border-hairline px-6 py-5">
          <div>
            <p className="font-text text-[9.5px] uppercase tracking-[0.2em] text-lemon-deep">
              Filing library
            </p>
            <h2 className="mt-1.5 font-text text-title-lg font-semibold text-ink">
              Available filings
            </h2>
            <p className="mt-2 max-w-md text-[12px] leading-5 text-muted">
              Every filing currently tracked in Redis. Newly queued filings
              appear here after the loader and chunk workers process them.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close filings dialog"
            className="rounded-lg border border-hairline-strong p-2 text-muted transition hover:border-lemon/40 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lemon/40"
          >
            <XIcon className="h-4 w-4" />
          </button>
        </div>

        <div className="flex items-center gap-2 border-b border-hairline px-4 py-3 sm:px-6">
          <div className="relative min-w-0 flex-1">
            <SearchIcon className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-soft" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              spellCheck={false}
              autoComplete="off"
              placeholder="Filter by ticker, company, form, or document id"
              aria-label="Filter filings"
              className="h-9 w-full rounded-lg border border-hairline-strong bg-canvas pl-9 pr-3 font-text text-[12px] text-ink outline-none transition placeholder:text-muted-soft focus:border-lemon focus:ring-2 focus:ring-lemon/15"
            />
          </div>
          <button
            type="button"
            onClick={refresh}
            disabled={loading}
            className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-hairline-strong bg-canvas px-3 text-[11.5px] font-medium text-muted transition hover:border-lemon/40 hover:text-ink disabled:cursor-not-allowed disabled:text-muted-soft"
          >
            <RefreshIcon
              className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`}
            />
            <span className="hidden sm:inline">Refresh</span>
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6">
          {loading && filings.length === 0 ? (
            <div className="space-y-2">
              {[0, 1, 2].map((item) => (
                <div
                  key={item}
                  className="h-24 animate-pulse rounded-xl border border-hairline-soft bg-surface-cream/60"
                />
              ))}
            </div>
          ) : error ? (
            <div className="rounded-xl border border-danger/20 bg-danger/[0.06] p-4">
              <div className="flex items-start gap-3">
                <AlertIcon className="mt-0.5 h-4 w-4 shrink-0 text-danger" />
                <div className="min-w-0">
                  <p className="text-[13px] font-medium text-ink">
                    Could not load filings
                  </p>
                  <p className="mt-1 text-[11.5px] leading-5 text-body">
                    {error}
                  </p>
                  <button
                    type="button"
                    onClick={refresh}
                    className="mt-3 h-8 rounded-lg border border-hairline-strong px-3 text-[11.5px] font-medium text-body transition hover:border-lemon/40 hover:text-ink"
                  >
                    Try again
                  </button>
                </div>
              </div>
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center px-4 py-12 text-center">
              <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-beige bg-surface-cream text-muted">
                <FilesIcon className="h-5 w-5" />
              </div>
              <p className="mt-3 text-[13px] font-medium text-ink">
                {filings.length === 0 ? "No filings tracked yet" : "No matches"}
              </p>
              <p className="mt-1 max-w-sm text-[11.5px] leading-5 text-muted">
                {filings.length === 0
                  ? "Queue a filing from the header or sidebar. Once it is chunked, it will appear in this list."
                  : "Try a different ticker, company, form, or document id."}
              </p>
            </div>
          ) : (
            <ul className="space-y-2">
              {filtered.map((filing) => {
                const meta = statusMeta(filing.status);
                return (
                  <li
                    key={filing.document_id}
                    className="rounded-xl border border-hairline-soft bg-canvas p-3.5 transition hover:border-hairline-strong hover:bg-surface-soft"
                  >
                    <div className="flex items-start gap-3">
                      <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-beige bg-surface-cream text-lemon-deep">
                        <FilesIcon className="h-4 w-4" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-1.5">
                          {filing.ticker ? (
                            <span className="rounded-md border border-hairline px-1.5 py-0.5 font-text text-[9px] uppercase tracking-[0.12em] text-body">
                              {filing.ticker}
                            </span>
                          ) : null}
                          {filing.form_type ? (
                            <span className="rounded-md border border-hairline px-1.5 py-0.5 font-text text-[9px] uppercase tracking-[0.12em] text-muted">
                              {filing.form_type}
                            </span>
                          ) : null}
                          <span
                            className={`rounded-md border px-1.5 py-0.5 font-text text-[9px] uppercase tracking-[0.12em] ${meta.tone}`}
                          >
                            {meta.label}
                          </span>
                        </div>
                        <p className="mt-1.5 line-clamp-2 text-[13px] font-medium leading-5 text-ink">
                          {filingTitle(filing)}
                        </p>
                        {filing.company &&
                        filing.company !== filingTitle(filing) ? (
                          <p className="mt-0.5 truncate text-[11px] text-muted">
                            {filing.company}
                          </p>
                        ) : null}
                        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 font-text text-[10px] text-muted-soft">
                          {filing.report_date ? (
                            <span>
                              Report {formatDate(filing.report_date)}
                            </span>
                          ) : null}
                          {filing.filing_date ? (
                            <span>Filed {formatDate(filing.filing_date)}</span>
                          ) : null}
                          {filing.chunk_count !== null ? (
                            <span>
                              {filing.chunk_count.toLocaleString()} chunks
                            </span>
                          ) : null}
                          {filing.accession_number ? (
                            <span className="truncate">
                              {filing.accession_number}
                            </span>
                          ) : null}
                        </div>
                      </div>
                      {filing.source_url ? (
                        <a
                          href={filing.source_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          aria-label={`Open ${filingTitle(filing)} on SEC`}
                          title="Open on SEC EDGAR"
                          className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-hairline-strong text-muted transition hover:border-lemon/40 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lemon/40"
                        >
                          <ArrowUpRightIcon className="h-3.5 w-3.5" />
                        </a>
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div className="flex items-center justify-between gap-3 border-t border-beige/60 bg-surface-cream/60 px-6 py-4">
          <p className="font-text text-[9px] uppercase tracking-[0.14em] text-muted-soft">
            {filings.length} {filings.length === 1 ? "filing" : "filings"}{" "}
            tracked
          </p>
          <button
            type="button"
            onClick={onClose}
            className="h-9 rounded-lg bg-lemon px-5 text-[12px] font-medium text-on-lemon transition hover:bg-lemon-deep"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
