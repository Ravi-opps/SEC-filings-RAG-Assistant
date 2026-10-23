"use client";

import { useState } from "react";

import {
  AlertIcon,
  BookOpenIcon,
  CheckIcon,
  CopyIcon,
} from "@/components/icons";
import { Markdown } from "@/components/markdown";
import { SourceCard } from "@/components/source-card";
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
    <span className="font-mono text-[9.5px] text-stone-600">
      {new Date(value).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      })}
    </span>
  );
}

function PendingAnswer() {
  return (
    <div className="space-y-3 py-1">
      <div className="flex items-center gap-2 text-[12.5px] text-stone-500">
        <span className="flex gap-1">
          {[0, 1, 2].map((index) => (
            <span
              key={index}
              className="h-1.5 w-1.5 animate-bounce rounded-full bg-emerald-300/70 [animation-duration:1.1s]"
              style={{ animationDelay: `${index * 140}ms` }}
            />
          ))}
        </span>
        Planning, retrieving, and reading filing context
      </div>
      <div className="space-y-2.5">
        <div className="h-2.5 w-[82%] animate-pulse rounded-full bg-white/[0.06]" />
        <div className="h-2.5 w-[64%] animate-pulse rounded-full bg-white/[0.045] [animation-delay:150ms]" />
        <div className="h-2.5 w-[41%] animate-pulse rounded-full bg-white/[0.03] [animation-delay:300ms]" />
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

  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[88%] sm:max-w-[78%]">
          <div className="rounded-2xl rounded-br-md border border-emerald-300/[0.16] bg-emerald-300/[0.07] px-4 py-3 text-[14.5px] leading-7 text-stone-200 shadow-[0_8px_30px_rgba(0,0,0,0.18)] whitespace-pre-wrap">
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
      <div
        className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border ${
          isBlocked
            ? "border-amber-300/20 bg-amber-300/[0.08] text-amber-200"
            : "border-emerald-300/20 bg-emerald-300/[0.07] text-emerald-200"
        }`}
      >
        {isBlocked ? (
          <AlertIcon className="h-4 w-4" />
        ) : (
          <BookOpenIcon className="h-4 w-4" />
        )}
      </div>

      <div className="min-w-0 flex-1">
        <div className="mb-2 flex min-h-5 flex-wrap items-center gap-2">
          <span className="text-[11px] font-medium tracking-wide text-stone-400">
            Filing analyst
          </span>
          {message.route && !isPending ? (
            <RouteBadge route={message.route} />
          ) : null}
          {sources.length > 0 ? (
            <span className="font-mono text-[9.5px] uppercase tracking-[0.12em] text-stone-600">
              {sources.length} {sources.length === 1 ? "source" : "sources"}
            </span>
          ) : null}
          <span className="ml-auto flex items-center gap-2">
            {message.content && !isPending && !isError ? (
              <button
                type="button"
                onClick={copyAnswer}
                className="rounded-md p-1 text-stone-600 opacity-0 transition hover:bg-white/5 hover:text-stone-300 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-300/30 group-hover/message:opacity-100 max-sm:opacity-100"
                aria-label="Copy answer"
                title="Copy answer"
              >
                {copied ? (
                  <CheckIcon className="h-3.5 w-3.5 text-emerald-300" />
                ) : (
                  <CopyIcon className="h-3.5 w-3.5" />
                )}
              </button>
            ) : null}
            <Timestamp value={message.createdAt} />
          </span>
        </div>

        {isPending ? (
          <PendingAnswer />
        ) : isError ? (
          <div className="rounded-xl border border-red-300/15 bg-red-300/[0.05] p-4">
            <div className="flex items-start gap-2.5">
              <AlertIcon className="mt-0.5 h-4 w-4 shrink-0 text-red-300/80" />
              <div className="min-w-0">
                <p className="text-[13px] font-medium text-red-200/90">
                  Request failed
                </p>
                <p className="mt-1 text-[12.5px] leading-6 text-stone-400">
                  {message.error ||
                    "The retrieval backend did not return a response."}
                </p>
                {retryPrompt && onRetry ? (
                  <button
                    type="button"
                    onClick={() => onRetry(retryPrompt)}
                    className="mt-2.5 rounded-lg border border-white/10 px-2.5 py-1 text-[11.5px] font-medium text-stone-300 transition hover:border-white/20 hover:text-stone-100"
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
              <p className="text-[14px] leading-7 text-stone-500">
                {isBlocked
                  ? "This request was stopped by the input guardrail."
                  : "The assistant returned an empty answer."}
              </p>
            )}
          </div>
        )}

        {sources.length > 0 && !isPending ? (
          <div className="mt-4">
            <div className="mb-2 flex items-baseline justify-between gap-3">
              <p className="font-mono text-[9.5px] uppercase tracking-[0.18em] text-stone-600">
                Cited passages
              </p>
              <p className="hidden text-[10px] text-stone-600 sm:block">
                Select a source to inspect the passage
              </p>
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              {sources.map((source) => (
                <SourceCard
                  key={`${source.citation}-${source.point_id ?? source.url}`}
                  source={source}
                  onOpen={onOpenSource}
                />
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
