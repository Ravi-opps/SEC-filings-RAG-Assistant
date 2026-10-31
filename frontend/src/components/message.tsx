"use client";

import { useState } from "react";

import { BrandMark } from "@/components/brand-mark";
import {
  AlertIcon,
  CheckIcon,
  ChevronRightIcon,
  CopyIcon,
  LayersIcon,
} from "@/components/icons";
import { Markdown } from "@/components/markdown";
import { SourceRow } from "@/components/source-card";
import { ROUTE_META } from "@/lib/format";
import type { ChatMessage, Source } from "@/lib/types";

function RouteBadge({ route }: { route: NonNullable<ChatMessage["route"]> }) {
  const meta = ROUTE_META[route];
  return (
    <span
      title={meta.description}
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 font-mono text-[9.5px] uppercase tracking-[0.14em] ${meta.tone}`}
    >
      <span className="h-1 w-1 rounded-full bg-current" />
      {meta.label}
    </span>
  );
}

function Timestamp({ value }: { value: number }) {
  return (
    <span className="font-mono text-[9.5px] text-muted-soft">
      {new Date(value).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      })}
    </span>
  );
}

const STAGE_LABEL: Record<string, string> = {
  accepted: "Opening the inquiry",
  guardrail: "Checking the request",
  planning: "Planning the retrieval",
  retrieving: "Searching indexed filings",
  answering: "Reading the evidence and writing",
};

function PendingAnswer({ stage }: { stage?: string }) {
  return (
    <div className="space-y-3 py-1">
      <div className="flex items-center gap-2 text-[12.5px] text-muted">
        <span className="flex gap-1">
          {[0, 1, 2].map((index) => (
            <span
              key={index}
              className="h-1.5 w-1.5 animate-bounce rounded-full bg-lemon/70 [animation-duration:1.1s]"
              style={{ animationDelay: `${index * 140}ms` }}
            />
          ))}
        </span>
        {STAGE_LABEL[stage ?? ""] ??
          "Planning, retrieving, and reading filing context"}
      </div>
      <div className="space-y-2.5">
        <div className="h-2.5 w-[82%] animate-pulse rounded-full bg-surface-elevated" />
        <div className="h-2.5 w-[64%] animate-pulse rounded-full bg-surface-elevated/80 [animation-delay:150ms]" />
        <div className="h-2.5 w-[41%] animate-pulse rounded-full bg-surface-elevated/60 [animation-delay:300ms]" />
      </div>
    </div>
  );
}

export function Message({
  message,
  onOpenSource,
  retryPrompt,
  onRetry,
}: {
  message: ChatMessage;
  onOpenSource: (source: Source) => void;
  retryPrompt?: string;
  onRetry?: (prompt: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const [sourcesOpen, setSourcesOpen] = useState(false);

  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[88%] sm:max-w-[78%]">
          <div className="whitespace-pre-wrap rounded-xl rounded-br-sm border border-lemon/15 bg-lemon/[0.06] px-4 py-3 text-[14.5px] leading-7 text-ink shadow-card">
            {message.content}
          </div>
          <div className="mt-1.5 flex justify-end pr-1">
            <Timestamp value={message.createdAt} />
          </div>
        </div>
      </div>
    );
  }

  const sources = message.sources ?? [];
  const isPending = message.status === "pending";
  const isError = message.status === "error";
  const isBlocked = message.route === "blocked" || message.status === "blocked";

  const copyAnswer = async () => {
    if (!message.content) return;
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1_600);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="group/message flex gap-3.5 sm:gap-4">
      {isBlocked ? (
        <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-warn/20 bg-warn/[0.08] text-warn">
          <AlertIcon className="h-4 w-4" />
        </div>
      ) : (
        <BrandMark size="sm" className="mt-0.5" />
      )}

      <div className="min-w-0 flex-1">
        <div className="mb-2 flex min-h-5 flex-wrap items-center gap-2">
          <span className="text-[11px] font-medium tracking-wide text-muted">
            Filing analyst
          </span>
          {message.route && !isPending ? (
            <RouteBadge route={message.route} />
          ) : null}
          <span className="ml-auto flex items-center gap-2">
            {message.content && !isPending && !isError ? (
              <button
                type="button"
                onClick={copyAnswer}
                className="rounded-md p-1 text-muted-soft opacity-0 transition hover:bg-surface-soft hover:text-lemon focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lemon/30 group-hover/message:opacity-100 max-sm:opacity-100"
                aria-label="Copy answer"
                title="Copy answer"
              >
                {copied ? (
                  <CheckIcon className="h-3.5 w-3.5 text-lemon" />
                ) : (
                  <CopyIcon className="h-3.5 w-3.5" />
                )}
              </button>
            ) : null}
            <Timestamp value={message.createdAt} />
          </span>
        </div>

        {isPending ? (
          message.content ? (
            <div className="max-w-[68ch]">
              <Markdown>{message.content}</Markdown>
              <span
                className="mt-1 inline-block h-3.5 w-[7px] animate-pulse rounded-[1px] bg-lemon/80"
                aria-hidden="true"
              />
            </div>
          ) : (
            <PendingAnswer stage={message.stage} />
          )
        ) : isError ? (
          <div className="rounded-xl border border-danger/20 bg-danger/[0.06] p-4">
            <div className="flex items-start gap-2.5">
              <AlertIcon className="mt-0.5 h-4 w-4 shrink-0 text-danger" />
              <div className="min-w-0">
                <p className="text-[13px] font-medium text-danger">
                  Request failed
                </p>
                <p className="mt-1 text-[12.5px] leading-6 text-muted">
                  {message.error ||
                    "The retrieval backend did not return a response."}
                </p>
                {retryPrompt && onRetry ? (
                  <button
                    type="button"
                    onClick={() => onRetry(retryPrompt)}
                    className="mt-2.5 rounded-lg border border-hairline bg-surface-soft px-2.5 py-1 text-[11.5px] font-medium text-body transition hover:border-lemon/30 hover:text-ink"
                  >
                    Try again
                  </button>
                ) : null}
              </div>
            </div>
          </div>
        ) : (
          <div className="max-w-[68ch]">
            {message.content ? (
              <Markdown>{message.content}</Markdown>
            ) : (
              <p className="text-[14px] leading-7 text-muted">
                {isBlocked
                  ? "This request was stopped by the input guardrail."
                  : "The assistant returned an empty answer."}
              </p>
            )}
          </div>
        )}

        {sources.length > 0 && !isPending ? (
          <div className="mt-3">
            <button
              type="button"
              onClick={() => setSourcesOpen((open) => !open)}
              aria-expanded={sourcesOpen}
              aria-controls={`sources-${message.id}`}
              className={`inline-flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-[11px] font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lemon/30 ${
                sourcesOpen
                  ? "border-lemon/30 bg-lemon/[0.06] text-ink"
                  : "border-hairline bg-surface-card/60 text-muted hover:border-lemon/30 hover:text-ink"
              }`}
            >
              <LayersIcon className="h-3.5 w-3.5 text-lemon" />
              Sources
              <span className="rounded-full border border-hairline bg-surface-soft px-1.5 font-mono text-[9px] leading-4 text-muted-soft">
                {sources.length}
              </span>
              <ChevronRightIcon
                className={`h-3 w-3 text-muted-soft transition-transform ${
                  sourcesOpen ? "rotate-90" : ""
                }`}
              />
            </button>

            {sourcesOpen ? (
              <div
                id={`sources-${message.id}`}
                className="mt-2 max-w-xl overflow-hidden rounded-xl border border-hairline bg-surface p-1 shadow-lift"
              >
                <div className="max-h-72 overflow-y-auto">
                  {sources.map((source) => (
                    <SourceRow
                      key={`${source.citation}-${source.point_id ?? source.url}`}
                      source={source}
                      onOpen={onOpenSource}
                    />
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
