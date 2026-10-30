"use client";

import { useEffect } from "react";

import {
  ArrowUpRightIcon,
  DocumentIcon,
  LinkIcon,
  XIcon,
} from "@/components/icons";
import {
  LINK_TYPE_LABEL,
  hostOf,
  relevancePercent,
  sourceLocation,
} from "@/lib/format";
import type { Source } from "@/lib/types";

function CitationChip({ citation }: { citation: number }) {
  return (
    <span className="flex h-6 min-w-6 shrink-0 items-center justify-center rounded-md border border-lemon/25 bg-lemon/10 px-1.5 font-mono text-[10px] font-medium text-lemon">
      {citation}
    </span>
  );
}

function filingMeta(source: Source): string {
  return sourceLocation([source.ticker, source.form_type, source.year]);
}

function sectionLabel(source: Source): string {
  return sourceLocation([
    source.section.join(" · "),
    source.part,
    source.item_title,
  ]);
}

export function SourceRow({
  source,
  onOpen,
}: {
  source: Source;
  onOpen: (source: Source) => void;
}) {
  const relevance = relevancePercent(source.relevance_score);
  const meta = filingMeta(source);

  return (
    <button
      type="button"
      onClick={() => onOpen(source)}
      className="group flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left transition hover:bg-surface-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lemon/30"
    >
      <CitationChip citation={source.citation} />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[12.5px] font-medium leading-5 text-body transition group-hover:text-ink">
          {source.item_title || source.title}
        </span>
        <span className="flex items-center gap-1.5 truncate font-mono text-[9px] uppercase tracking-[0.1em] text-muted-soft">
          <span className="truncate">{meta || "SEC filing"}</span>
          {relevance !== null ? (
            <>
              <span className="h-2.5 w-px shrink-0 bg-hairline" />
              <span className="shrink-0">{relevance}% match</span>
            </>
          ) : null}
        </span>
      </span>
      <ArrowUpRightIcon className="h-3.5 w-3.5 shrink-0 text-muted-soft transition group-hover:text-lemon" />
    </button>
  );
}

export function SourceSheet({
  source,
  onClose,
}: {
  source: Source;
  onClose: () => void;
}) {
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

  const section = sectionLabel(source);
  const relevance = relevancePercent(source.relevance_score);

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <button
        type="button"
        aria-label="Close source panel"
        onClick={onClose}
        className="absolute inset-0 bg-black/75 backdrop-blur-[3px]"
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={`Source ${source.citation}`}
        className="relative flex h-full w-full max-w-xl flex-col overflow-hidden border-l border-hairline bg-surface shadow-float"
      >
        <div className="flex items-start justify-between gap-4 border-b border-hairline px-6 py-5">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <CitationChip citation={source.citation} />
              <p className="font-mono text-[9.5px] uppercase tracking-[0.2em] text-muted-soft">
                Cited passage
              </p>
            </div>
            <h2 className="mt-3 font-serif text-[24px] leading-tight text-ink">
              {source.item_title || source.title}
            </h2>
            <div className="mt-2.5 flex flex-wrap gap-1.5">
              {[source.ticker, source.form_type, source.year, source.part]
                .filter(Boolean)
                .map((value, index) => (
                  <span
                    key={`${value}-${index}`}
                    className="rounded-md border border-hairline px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] text-muted"
                  >
                    {value}
                  </span>
                ))}
              {relevance !== null ? (
                <span className="rounded-md border border-lemon/25 bg-lemon/10 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] text-lemon">
                  {relevance}% match
                </span>
              ) : null}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close source panel"
            className="rounded-lg border border-hairline p-2 text-muted transition hover:border-lemon/30 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lemon/40"
          >
            <XIcon className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-6">
          {section ? (
            <div className="mb-5">
              <p className="mb-1.5 font-mono text-[9px] uppercase tracking-[0.18em] text-muted-soft">
                Filing location
              </p>
              <p className="text-[11.5px] leading-5 text-body">{section}</p>
            </div>
          ) : null}

          <div className="border-l-2 border-lemon/40 pl-4">
            <p className="whitespace-pre-wrap text-[14px] leading-7 text-body">
              {source.snippet ||
                "No passage text was returned for this citation. Open the original filing to inspect the source location."}
            </p>
          </div>

          <div className="mt-6 space-y-2 border-t border-hairline pt-5">
            <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-muted-soft">
              Original record
            </p>
            <a
              href={source.url}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-2.5 rounded-lg border border-hairline bg-surface-soft px-3 py-2.5 transition hover:border-lemon/30"
            >
              <LinkIcon className="h-3.5 w-3.5 shrink-0 text-lemon" />
              <span className="min-w-0 flex-1">
                <span className="block text-[12px] font-medium text-body">
                  Open cited location
                </span>
                <span className="block truncate font-mono text-[9.5px] text-muted-soft">
                  {hostOf(source.url)}
                </span>
              </span>
              <ArrowUpRightIcon className="h-3.5 w-3.5 shrink-0 text-muted-soft" />
            </a>
            {source.document_url && source.document_url !== source.url ? (
              <a
                href={source.document_url}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-2.5 rounded-lg border border-hairline bg-surface-soft px-3 py-2.5 transition hover:border-lemon/30"
              >
                <DocumentIcon className="h-3.5 w-3.5 shrink-0 text-muted" />
                <span className="min-w-0 flex-1">
                  <span className="block text-[12px] font-medium text-body">
                    Full filing document
                  </span>
                  <span className="block truncate font-mono text-[9.5px] text-muted-soft">
                    {hostOf(source.document_url)}
                  </span>
                </span>
                <ArrowUpRightIcon className="h-3.5 w-3.5 shrink-0 text-muted-soft" />
              </a>
            ) : null}
          </div>
        </div>

        <div className="border-t border-hairline bg-canvas/40 px-6 py-3.5">
          <p className="text-[10.5px] leading-4 text-muted-soft">
            {LINK_TYPE_LABEL[source.link_type]} citation · Always verify figures
            against the original filing.
          </p>
        </div>
      </aside>
    </div>
  );
}
