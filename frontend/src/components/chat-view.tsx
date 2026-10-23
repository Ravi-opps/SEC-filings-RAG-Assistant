"use client";

import { useEffect, useRef } from "react";

import { ArrowUpRightIcon, SearchIcon } from "@/components/icons";
import { Message } from "@/components/message";
import type { BackendStatus } from "@/lib/use-backend-status";
import type { Conversation, Source } from "@/lib/types";

const SUGGESTIONS = [
  {
    category: "Risk factors",
    prompt: "What are the biggest supply-chain risks described in Apple's latest 10-K?",
  },
  {
    category: "Revenue mix",
    prompt: "How did NVIDIA describe the drivers of its data center revenue?",
  },
  {
    category: "MD&A",
    prompt: "Summarize Microsoft's management discussion and analysis from its last 10-Q.",
  },
  {
    category: "Competition",
    prompt: "How does Tesla frame competition and pricing pressure in its 10-K?",
  },
];

function EmptyState({
  backendStatus,
  onSend,
  onIndex,
}: {
  backendStatus: BackendStatus;
  onSend: (prompt: string) => void;
  onIndex: () => void;
}) {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col px-4 pb-10 pt-10 sm:px-8 sm:pt-16">
      <div className="relative">
        <div className="pointer-events-none absolute -left-8 -top-14 select-none font-serif text-[160px] leading-none text-white/[0.018] sm:-left-14 sm:text-[220px]">
          10-K
        </div>
        <p className="relative font-mono text-[9.5px] uppercase tracking-[0.28em] text-emerald-200/70">
          SEC filing intelligence
        </p>
        <h1 className="relative mt-4 max-w-xl font-serif text-[38px] leading-[1.08] text-stone-100 sm:text-[52px]">
          Ask the filing.
          <br />
          <span className="text-stone-400">Not the open web.</span>
        </h1>
        <p className="relative mt-5 max-w-lg text-[13.5px] leading-6 text-stone-500">
          A retrieval desk for annual and quarterly reports. Every answer is
          built from indexed passages and carries a citation back to the exact
          source on SEC EDGAR.
        </p>
      </div>

      {backendStatus === "offline" ? (
        <div className="mt-8 flex items-start gap-3 rounded-xl border border-amber-300/15 bg-amber-300/[0.05] px-4 py-3">
          <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-300" />
          <div>
            <p className="text-[12px] font-medium text-amber-100/90">
              Retrieval backend is offline
            </p>
            <p className="mt-0.5 text-[11.5px] leading-5 text-stone-500">
              Start FastAPI with{" "}
              <code className="rounded bg-white/[0.06] px-1 py-0.5 font-mono text-[10.5px] text-stone-300">
                python main.py
              </code>{" "}
              in the backend folder, then send a question.
            </p>
          </div>
        </div>
      ) : null}

      <div className="mt-10">
        <div className="mb-3 flex items-center gap-3">
          <p className="font-mono text-[9px] uppercase tracking-[0.2em] text-stone-600">
            Start with a line of inquiry
          </p>
          <span className="h-px flex-1 bg-white/[0.06]" />
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          {SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion.category}
              type="button"
              onClick={() => onSend(suggestion.prompt)}
              disabled={backendStatus === "offline"}
              className="group flex flex-col gap-2.5 rounded-xl border border-white/[0.07] bg-white/[0.02] p-4 text-left transition hover:border-emerald-300/25 hover:bg-emerald-300/[0.035] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-300/30 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <span className="flex items-center justify-between">
                <span className="font-mono text-[8.5px] uppercase tracking-[0.18em] text-stone-600 transition group-hover:text-emerald-200/60">
                  {suggestion.category}
                </span>
                <ArrowUpRightIcon className="h-3.5 w-3.5 text-stone-700 transition group-hover:text-emerald-200" />
              </span>
              <span className="text-[12.5px] leading-5 text-stone-300">
                {suggestion.prompt}
              </span>
            </button>
          ))}
        </div>
      </div>

      <div className="mt-10 flex flex-wrap items-center justify-between gap-4 border-t border-white/[0.06] pt-5">
        <div className="flex items-center gap-2 text-[11px] text-stone-600">
          <SearchIcon className="h-3.5 w-3.5" />
          Answers only draw from filings already in the index.
        </div>
        <button
          type="button"
          onClick={onIndex}
          className="text-[11px] font-medium text-emerald-200/80 underline decoration-emerald-300/25 underline-offset-4 transition hover:text-emerald-100 hover:decoration-emerald-300/60"
        >
          Index a filing
        </button>
      </div>
    </div>
  );
}

export function ChatView({
  conversation,
  backendStatus,
  onSend,
  onOpenSource,
  onRetry,
  onIndex,
}: {
  conversation: Conversation | null;
  backendStatus: BackendStatus;
  onSend: (prompt: string) => void;
  onOpenSource: (source: Source) => void;
  onRetry: (prompt: string) => void;
  onIndex: () => void;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const messages = conversation?.messages ?? [];
  const lastMessageStatus = messages[messages.length - 1]?.status;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, lastMessageStatus]);

  if (messages.length === 0) {
    return (
      <div className="flex-1 overflow-y-auto">
        <EmptyState
          backendStatus={backendStatus}
          onSend={onSend}
          onIndex={onIndex}
        />
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-8 px-4 py-8 sm:px-8 sm:py-10">
        {messages.map((message, index) => {
          const retryPrompt =
            message.role === "assistant" && message.status === "error"
              ? [...messages.slice(0, index)]
                  .reverse()
                  .find((candidate) => candidate.role === "user")?.content
              : undefined;

          return (
            <Message
              key={message.id}
              message={message}
              onOpenSource={onOpenSource}
              retryPrompt={retryPrompt}
              onRetry={onRetry}
            />
          );
        })}
        <div ref={bottomRef} className="h-1" />
      </div>
    </div>
  );
}
