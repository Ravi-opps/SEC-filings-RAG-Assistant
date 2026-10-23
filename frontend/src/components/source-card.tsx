"use client";

import { useEffect } from "react";

import {
  ArrowUpRightIcon,
  FileTextIcon,
  LayersIcon,
  XIcon,
} from "@/components/icons";
import {
  LINK_TYPE_LABEL,
  hostOf,
  relevancePercent,
  sourceLocation,
} from "@/lib/format";
import type { Source } from "@/lib/types";

function SourceLabel({ source }: { source: Source }) {
  const label = sourceLocation([source.ticker, source.form_type, source.year]);
  return (
    <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-stone-500">
      {label || "SEC filing"}
    </span>
  );
}

export function SourceCard({
  source,
  onOpen,
}: {
  source: Source;
  onOpen: (source: Source) => void;
}) {
  const relevance = relevancePercent(source.relevance_score);
  const section = source.section?.filter(Boolean) ?? [];
  const heading = source.item_title
    ? `Item ${source.item_id ?? "?"} — ${source.item_title}`
    : section[0];

  return (
    <button
      type="button"
      onClick={() => onOpen(source)}
      className="group flex w-full flex-col gap-2 rounded-xl border border-white/[0.07] bg-white/[0.025] p-3.5 text-left transition hover:border-emerald-300/25 hover:bg-emerald-300/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-300/40"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-1.5 rounded-md border border-white/10 bg-black/20 px-1.5 py-0.5 font-mono text-[10px] text-emerald-200/90">
          [{source.citation}]
        </span>
        <SourceLabel source={source} />
      </div>

      <p className="line-clamp-2 text-[13px] font-medium leading-5 text-stone-200">
        {source.title || "SEC filing source"}
      </p>

      {heading || section.length > 0 ? (
        <p className="line-clamp-1 font-mono text-[10.5px] leading-4 text-stone-500">
          {[source.part, heading, ...section.slice(1)]
            .filter(Boolean)
            .join(" / ")}
        </p>
      ) : null}

      {source.snippet ? (
        <p className="line-clamp-2 text-[12px] leading-5 text-stone-400">
          {source.snippet}
        </p>
      ) : null}

      <div className="mt-auto flex items-center justify-between gap-3 pt-0.5">
        <span className="truncate text-[10px] text-stone-600">
          {hostOf(source.url)}
        </span>
        <span className="flex items-center gap-2">
          {relevance !== null ? (
            <span className="flex items-center gap-1.5">
              <span className="h-1 w-10 overflow-hidden rounded-full bg-white/10">
                <span
                  className="block h-full rounded-full bg-emerald-300/70"
                  style={{ width: `${relevance}%` }}
                />
              </span>
              <span className="font-mono text-[9.5px] text-stone-500">
                {relevance}%
              </span>
            </span>
          ) : null}
          <ArrowUpRightIcon className="h-3.5 w-3.5 text-stone-600 transition group-hover:text-emerald-200" />
        </span>
      </div>
    </button>
  );
}

function MetaRow({
  label,
  value,
}: {
  label: string;
  value: string | number | null | undefined;
}) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div className="flex flex-col gap-0.5 border-b border-white/5 py-2">
      <dt className="text-[10px] uppercase tracking-[0.14em] text-stone-600">
        {label}
      </dt>
      <dd className="break-words font-mono text-[11.5px] leading-5 text-stone-300">
        {value}
      </dd>
    </div>
  );
}

export function SourceSheet({
  source,
  onClose,
}: {
  source: Source | null;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!source) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [source, onClose]);

  if (!source) return null;

  const relevance = relevancePercent(source.relevance_score);
  const section = source.section?.filter(Boolean) ?? [];

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <button
        type="button"
        aria-label="Close source details"
        onClick={onClose}
        className="absolute inset-0 bg-black/60 backdrop-blur-[2px]"
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={`Source ${source.citation}`}
        className="relative flex h-full w-full max-w-md flex-col border-l border-white/10 bg-[#0b1017] shadow-[-24px_0_80px_rgba(0,0,0,0.55)]"
      >
        <header className="flex items-start justify-between gap-4 border-b border-white/10 px-6 py-5">
          <div className="min-w-0">
            <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-emerald-200/80">
              Citation [{source.citation}]
            </p>
            <h2 className="mt-1.5 line-clamp-2 font-serif text-2xl leading-snug text-stone-100">
              {source.title || "SEC filing source"}
            </h2>
            <p className="mt-2 inline-flex items-center gap-1.5 rounded-full border border-white/10 px-2 py-0.5 font-mono text-[9.5px] uppercase tracking-[0.14em] text-stone-500">
              <LayersIcon className="h-3 w-3" />
              {LINK_TYPE_LABEL[source.link_type] ?? source.link_type}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            autoFocus
            className="rounded-lg border border-white/10 p-2 text-stone-400 transition hover:border-white/20 hover:text-stone-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-300/40"
            aria-label="Close source details"
          >
            <XIcon className="h-4 w-4" />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-6 py-5">
          {source.snippet ? (
            <section className="mb-5 rounded-xl border border-white/[0.07] bg-white/[0.03] p-4">
              <p className="mb-2 font-mono text-[10px] uppercase tracking-[0.16em] text-stone-600">
                Retrieved passage
              </p>
              <p className="whitespace-pre-wrap text-[13px] leading-6 text-stone-300">
                {source.snippet}
              </p>
            </section>
          ) : null}

          <section>
            <p className="mb-1 font-mono text-[10px] uppercase tracking-[0.16em] text-stone-600">
              Filing context
            </p>
            <dl className="grid grid-cols-2 gap-x-5">
              <MetaRow label="Company / ticker" value={source.ticker} />
              <MetaRow label="Form" value={source.form_type} />
              <MetaRow label="Fiscal year" value={source.year} />
              <MetaRow label="CIK" value={source.cik} />
              <MetaRow label="Part" value={source.part} />
              <MetaRow
                label="Item"
                value={
                  source.item_title
                    ? `${source.item_id ?? "?"} — ${source.item_title}`
                    : source.item_id
                }
              />
              <MetaRow
                label="Section path"
                value={section.length ? section.join(" / ") : undefined}
              />
              <MetaRow label="Accession" value={source.accession_number} />
              <MetaRow label="Document ID" value={source.document_id} />
              <MetaRow label="Anchor" value={source.anchor_id} />
              <MetaRow label="DOM ID" value={source.source_dom_id} />
              <MetaRow label="Point ID" value={source.point_id} />
              <MetaRow
                label="Relevance"
                value={relevance !== null ? `${relevance}%` : undefined}
              />
            </dl>
          </section>
        </div>

        <footer className="space-y-2 border-t border-white/10 bg-black/20 px-6 py-5">
          <a
            href={source.url}
            target="_blank"
            rel="noreferrer"
            className="flex h-11 items-center justify-center gap-2 rounded-xl bg-emerald-300 text-[13px] font-semibold text-[#05100c] transition hover:bg-emerald-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-200/60"
          >
            <FileTextIcon className="h-4 w-4" />
            Open cited passage
            <ArrowUpRightIcon className="h-3.5 w-3.5" />
          </a>
          {source.document_url && source.document_url !== source.url ? (
            <a
              href={source.document_url}
              target="_blank"
              rel="noreferrer"
              className="flex h-10 items-center justify-center gap-2 rounded-xl border border-white/10 text-[12px] font-medium text-stone-300 transition hover:border-white/20 hover:text-stone-100"
            >
              Open full filing
              <ArrowUpRightIcon className="h-3.5 w-3.5" />
            </a>
          ) : null}
          <p className="truncate pt-1 text-center font-mono text-[9px] text-stone-600">
            {hostOf(source.url)}
          </p>
        </footer>
      </aside>
    </div>
  );
}
