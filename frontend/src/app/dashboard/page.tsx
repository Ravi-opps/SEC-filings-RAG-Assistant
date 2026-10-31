"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AuthLoading, AuthScreen } from "@/components/auth-screen";
import { BrandMark } from "@/components/brand-mark";
import {
  AlertIcon,
  ArrowLeftIcon,
  LayersIcon,
  RefreshIcon,
} from "@/components/icons";
import { fetchIngestStatus } from "@/lib/api";
import type {
  IngestStatusDocument,
  IngestStatusResponse,
  LoadState,
} from "@/lib/types";
import { useAuth } from "@/lib/use-auth";

const REFRESH_MS = 5_000;

const STATE_META: Record<
  LoadState,
  { label: string; badge: string; bar: string; dot: string }
> = {
  complete: {
    label: "Complete",
    badge: "border-success/30 bg-success/10 text-success",
    bar: "bg-success",
    dot: "bg-success",
  },
  in_progress: {
    label: "Loading",
    badge: "border-lemon/30 bg-lemon/10 text-lemon",
    bar: "bg-lemon",
    dot: "bg-lemon",
  },
  queued: {
    label: "Queued",
    badge: "border-warn/30 bg-warn/10 text-warn",
    bar: "bg-warn",
    dot: "bg-warn",
  },
  not_loaded: {
    label: "Not loaded",
    badge: "border-danger/30 bg-danger/10 text-danger",
    bar: "bg-danger",
    dot: "bg-danger",
  },
  unknown: {
    label: "Unknown",
    badge: "border-hairline bg-surface-card text-muted",
    bar: "bg-muted",
    dot: "bg-muted",
  },
};

const STATE_ORDER: Record<LoadState, number> = {
  in_progress: 0,
  queued: 1,
  not_loaded: 2,
  unknown: 3,
  complete: 4,
};

const NUMBER = new Intl.NumberFormat("en-US");

function formatNumber(value: number | null | undefined): string {
  return value == null ? "n/a" : NUMBER.format(value);
}

function formatTime(value: string | undefined): string {
  if (!value) return "n/a";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function SummaryCard({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="rounded-xl border border-hairline bg-surface/80 p-4">
      <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-muted-soft">
        {label}
      </p>
      <p className="mt-2 font-serif text-[28px] leading-none text-ink">
        {value}
      </p>
      <p className="mt-2 text-[10.5px] leading-4 text-muted-soft">{detail}</p>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-hairline-soft bg-surface-soft px-2.5 py-2">
      <dt className="font-mono text-[8px] uppercase tracking-[0.14em] text-muted-soft">
        {label}
      </dt>
      <dd className="mt-1 truncate text-[11.5px] text-body">{value}</dd>
    </div>
  );
}

function DocumentCard({ doc }: { doc: IngestStatusDocument }) {
  const meta = STATE_META[doc.state];
  const percent = doc.percent_loaded ?? 0;
  const width = Math.max(0, Math.min(percent, 100));

  return (
    <article className="rounded-2xl border border-hairline bg-surface/85 p-4 sm:p-5">
      <div className="flex flex-wrap items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-lemon/20 bg-lemon/[0.07] font-mono text-[11px] uppercase tracking-[0.08em] text-lemon">
          {doc.ticker || "?"}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate font-serif text-[18px] leading-tight text-ink">
              {doc.company || doc.document_id}
            </h3>
            <span
              className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 font-mono text-[8.5px] uppercase tracking-[0.14em] ${meta.badge}`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${meta.dot} ${
                  doc.state === "in_progress" ? "animate-pulse" : ""
                }`}
              />
              {meta.label}
            </span>
          </div>
          <p className="mt-1 font-mono text-[9.5px] uppercase tracking-[0.12em] text-muted-soft">
            {doc.document_id}
            <span className="px-1.5 text-hairline">/</span>
            {doc.form_type || "n/a"}
            {doc.report_date ? ` / ${doc.report_date}` : ""}
          </p>
        </div>

        <div className="text-right">
          <p className="font-mono text-[20px] leading-none text-ink">
            {doc.percent_loaded == null ? "n/a" : `${doc.percent_loaded}%`}
          </p>
          <p className="mt-1 font-mono text-[8.5px] uppercase tracking-[0.14em] text-muted-soft">
            in qdrant
          </p>
        </div>
      </div>

      <div className="mt-4">
        <div className="flex items-center justify-between gap-3">
          <p className="text-[10.5px] leading-5 text-body">{doc.verdict}</p>
          <p className="shrink-0 font-mono text-[9px] text-muted-soft">
            {formatNumber(doc.loaded_chunks)} /{" "}
            {formatNumber(doc.expected_chunks)}
          </p>
        </div>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-elevated">
          <div
            className={`h-full rounded-full transition-[width] duration-500 ${meta.bar}`}
            style={{ width: `${width}%` }}
          />
        </div>
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Loaded" value={formatNumber(doc.loaded_chunks)} />
        <Stat label="Remaining" value={formatNumber(doc.remaining_chunks)} />
        <Stat label="Redis pending" value={formatNumber(doc.pending_chunks)} />
        <Stat label="Redis status" value={doc.redis_status || "n/a"} />
      </dl>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-hairline-soft pt-3 font-mono text-[8.5px] uppercase tracking-[0.12em] text-muted-soft">
        <span>count: {doc.count_source}</span>
        {doc.indexed_at ? <span>indexed {doc.indexed_at}</span> : null}
        {doc.accession_number ? <span>{doc.accession_number}</span> : null}
      </div>

      {doc.notes.length ? (
        <ul className="mt-3 space-y-1">
          {doc.notes.map((note) => (
            <li
              key={note}
              className="flex gap-2 text-[10.5px] leading-5 text-warn"
            >
              <AlertIcon className="mt-0.5 h-3 w-3 shrink-0" />
              {note}
            </li>
          ))}
        </ul>
      ) : null}
    </article>
  );
}

export default function DashboardPage() {
  const auth = useAuth();
  const [data, setData] = useState<IngestStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [refreshKey, setRefreshKey] = useState(0);

  const refresh = useCallback(() => {
    setRefreshKey((value) => value + 1);
  }, []);

  useEffect(() => {
    if (auth.status !== "authenticated") return;

    let cancelled = false;
    let timer: number | undefined;

    const tick = async () => {
      setRefreshing(true);
      try {
        const next = await fetchIngestStatus();
        if (!cancelled) {
          setData(next);
          setError(null);
        }
      } catch (caught) {
        if (!cancelled) {
          setError(
            caught instanceof Error
              ? caught.message
              : "Could not load pipeline status.",
          );
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
          setRefreshing(false);
          if (autoRefresh) {
            timer = window.setTimeout(() => void tick(), REFRESH_MS);
          }
        }
      }
    };

    void tick();

    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [auth.status, autoRefresh, refreshKey]);

  const documents = useMemo(() => {
    return [...(data?.documents ?? [])].sort(
      (left, right) =>
        STATE_ORDER[left.state] - STATE_ORDER[right.state] ||
        left.ticker.localeCompare(right.ticker),
    );
  }, [data]);

  const totals = useMemo(() => {
    const expected = documents.reduce(
      (sum, doc) => sum + (doc.expected_chunks ?? doc.loaded_chunks),
      0,
    );
    const loaded = documents.reduce(
      (sum, doc) => sum + doc.loaded_chunks,
      0,
    );
    const remaining = documents.reduce(
      (sum, doc) => sum + (doc.remaining_chunks ?? 0),
      0,
    );
    const complete = documents.filter(
      (doc) => doc.state === "complete",
    ).length;
    const active = documents.filter(
      (doc) => doc.state === "in_progress" || doc.state === "queued",
    ).length;
    return {
      documents: documents.length,
      complete,
      active,
      loaded,
      expected,
      remaining,
      percent: expected > 0 ? Math.round((loaded / expected) * 1000) / 10 : null,
    };
  }, [documents]);

  if (auth.status === "loading") {
    return <AuthLoading />;
  }

  if (!auth.user) {
    return <AuthScreen onLogin={auth.login} onSignup={auth.signup} />;
  }

  return (
    <div className="h-dvh overflow-y-auto bg-canvas bg-[radial-gradient(90%_60%_at_50%_-10%,rgba(215,255,62,0.05),transparent_60%)]">
      <header className="sticky top-0 z-20 border-b border-hairline bg-canvas/85 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-6xl items-center gap-3 px-4 sm:px-6">
          <Link
            href="/"
            aria-label="Back to research desk"
            className="rounded-lg border border-hairline p-2 text-muted transition hover:border-lemon/30 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lemon/40"
          >
            <ArrowLeftIcon className="h-4 w-4" />
          </Link>

          <BrandMark />
          <div className="min-w-0">
            <p className="font-mono text-[9px] uppercase tracking-[0.2em] text-muted-soft">
              Filing Room / pipeline
            </p>
            <h1 className="font-serif text-[19px] leading-tight text-ink">
              Indexing status
            </h1>
          </div>

          <div className="ml-auto flex items-center gap-2">
            <span className="hidden font-mono text-[8.5px] uppercase tracking-[0.14em] text-muted-soft sm:inline">
              checked {formatTime(data?.checked_at)}
            </span>
            <button
              type="button"
              onClick={() => setAutoRefresh((value) => !value)}
              aria-pressed={autoRefresh}
              className={`inline-flex h-9 items-center gap-2 rounded-lg border px-2.5 font-mono text-[9px] uppercase tracking-[0.12em] transition ${
                autoRefresh
                  ? "border-lemon/25 bg-lemon/[0.06] text-lemon"
                  : "border-hairline bg-surface-soft text-muted"
              }`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  autoRefresh ? "animate-pulse bg-lemon" : "bg-muted-soft"
                }`}
              />
              Live
            </button>
            <button
              type="button"
              onClick={refresh}
              disabled={refreshing}
              className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-hairline bg-surface-soft px-3 text-[11.5px] font-medium text-muted transition hover:border-lemon/30 hover:text-ink disabled:cursor-wait disabled:opacity-60"
            >
              <RefreshIcon
                className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`}
              />
              <span className="hidden sm:inline">Refresh</span>
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
        <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="font-serif text-[28px] leading-tight text-ink">
              Qdrant ingestion pipeline
            </h2>
            <p className="mt-2 max-w-2xl text-[12.5px] leading-6 text-muted">
              Live per-document progress from Redis chunk queues and Qdrant
              point counts. Auto-refresh runs every 5 seconds.
            </p>
          </div>
          <Link
            href="/"
            className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-hairline bg-surface-soft px-3 text-[11.5px] font-medium text-muted transition hover:border-lemon/30 hover:text-ink"
          >
            Open research desk
          </Link>
        </div>

        {error ? (
          <div className="mb-4 flex items-start gap-2 rounded-xl border border-danger/25 bg-danger/[0.07] px-3.5 py-3 text-[11px] leading-5 text-danger">
            <AlertIcon className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>{error}</span>
          </div>
        ) : null}

        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <SummaryCard
            label="Documents"
            value={String(totals.documents)}
            detail={`${totals.complete} complete / ${totals.active} in flight`}
          />
          <SummaryCard
            label="Chunks in Qdrant"
            value={formatNumber(totals.loaded)}
            detail={`of ${formatNumber(totals.expected)} expected`}
          />
          <SummaryCard
            label="Progress"
            value={totals.percent == null ? "n/a" : `${totals.percent}%`}
            detail="weighted across tracked documents"
          />
          <SummaryCard
            label="Remaining"
            value={formatNumber(totals.remaining)}
            detail="chunks still to embed or upload"
          />
        </section>

        <section className="mt-8">
          <div className="mb-3 flex items-center justify-between">
            <p className="font-mono text-[9px] uppercase tracking-[0.2em] text-muted-soft">
              Documents
            </p>
            <p className="font-mono text-[9px] uppercase tracking-[0.14em] text-muted-soft">
              {documents.length} tracked
            </p>
          </div>

          {loading && !data ? (
            <div className="space-y-3">
              {[0, 1].map((key) => (
                <div
                  key={key}
                  className="h-44 animate-pulse rounded-2xl border border-hairline bg-surface/60"
                />
              ))}
            </div>
          ) : documents.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-hairline bg-surface/50 px-5 py-12 text-center">
              <LayersIcon className="mx-auto h-6 w-6 text-muted-soft" />
              <p className="mt-3 font-serif text-[20px] text-ink">
                No filings tracked yet
              </p>
              <p className="mx-auto mt-2 max-w-md text-[11.5px] leading-5 text-muted">
                Index a filing from the research desk. Progress appears here
                once the loader and chunk worker start.
              </p>
              <Link
                href="/"
                className="mt-4 inline-flex h-9 items-center rounded-lg bg-lemon px-3 text-[11.5px] font-semibold text-on-lemon transition hover:bg-lemon-bright"
              >
                Open research desk
              </Link>
            </div>
          ) : (
            <div className="space-y-3">
              {documents.map((doc) => (
                <DocumentCard key={doc.document_id} doc={doc} />
              ))}
            </div>
          )}
        </section>

        <p className="mt-6 font-mono text-[8.5px] uppercase tracking-[0.14em] text-muted-soft">
          Source: Redis document metadata + Qdrant payload counts / last check{" "}
          {formatTime(data?.checked_at)}
        </p>
      </main>
    </div>
  );
}
