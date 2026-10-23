"use client";

import { useCallback, useEffect, useState } from "react";

import { ChatView } from "@/components/chat-view";
import { Composer } from "@/components/composer";
import { DatabaseIcon, MenuIcon } from "@/components/icons";
import { IngestDialog } from "@/components/ingest-dialog";
import { Sidebar } from "@/components/sidebar";
import { SourceSheet } from "@/components/source-card";
import { askQuestion } from "@/lib/api";
import { makeTitle, useChatStore } from "@/lib/use-chat-store";
import { useBackendStatus } from "@/lib/use-backend-status";
import type { ChatMessage, Source } from "@/lib/types";

export default function Home() {
  const store = useChatStore();
  const { status: backendStatus, recheck } = useBackendStatus();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [ingestOpen, setIngestOpen] = useState(false);
  const [sourceSheet, setSourceSheet] = useState<Source | null>(null);
  const [pendingIds, setPendingIds] = useState<Set<string>>(new Set());

  const {
    active,
    activeId,
    createConversation,
    appendMessage,
    patchMessage,
    setActiveId,
    deleteConversation,
  } = store;

  const send = useCallback(
    async (prompt: string) => {
      const text = prompt.trim();
      if (!text) return;

      const conversationId =
        activeId ?? createConversation(makeTitle(text));

      const now = Date.now();
      const userMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: "user",
        content: text,
        createdAt: now,
      };
      const assistantMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: "",
        status: "pending",
        createdAt: now + 1,
      };

      appendMessage(conversationId, userMessage);
      appendMessage(conversationId, assistantMessage);
      setPendingIds((previous) => new Set(previous).add(conversationId));

      try {
        const response = await askQuestion(text, conversationId);
        patchMessage(conversationId, assistantMessage.id, {
          status: response.type === "blocked" ? "blocked" : "complete",
          route: response.type,
          content: response.answer ?? "",
          sources: response.sources ?? [],
        });
      } catch (caught) {
        patchMessage(conversationId, assistantMessage.id, {
          status: "error",
          error:
            caught instanceof Error
              ? caught.message
              : "The retrieval request failed.",
        });
      } finally {
        setPendingIds((previous) => {
          const next = new Set(previous);
          next.delete(conversationId);
          return next;
        });
      }
    },
    [activeId, appendMessage, createConversation, patchMessage],
  );

  const startNewInquiry = useCallback(() => {
    if (active && active.messages.length === 0) {
      setSidebarOpen(false);
      return;
    }
    createConversation("New inquiry");
    setSidebarOpen(false);
    setSourceSheet(null);
  }, [active, createConversation]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        startNewInquiry();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [startNewInquiry]);

  const isPending = activeId ? pendingIds.has(activeId) : false;
  const lastRoute = [...(active?.messages ?? [])]
    .reverse()
    .find((message) => message.route)?.route;

  return (
    <div className="flex h-dvh overflow-hidden bg-[#070a0f] text-stone-200">
      <Sidebar
        conversations={store.conversations}
        activeId={activeId}
        open={sidebarOpen}
        backendStatus={backendStatus}
        onClose={() => setSidebarOpen(false)}
        onSelect={(id) => {
          setActiveId(id);
          setSourceSheet(null);
        }}
        onNew={startNewInquiry}
        onDelete={(id) => {
          deleteConversation(id);
          setSourceSheet(null);
        }}
        onIndex={() => setIngestOpen(true)}
        onRecheck={recheck}
      />

      <main className="relative flex min-w-0 flex-1 flex-col bg-[#080b10] bg-[radial-gradient(120%_75%_at_50%_-20%,rgba(110,231,183,0.05),transparent_55%)]">
        <header className="z-20 flex h-14 shrink-0 items-center gap-3 border-b border-white/[0.06] bg-[#080b10]/70 px-4 backdrop-blur-xl sm:px-6">
          <button
            type="button"
            onClick={() => setSidebarOpen(true)}
            className="rounded-lg border border-white/10 p-1.5 text-stone-400 transition hover:border-white/20 hover:text-stone-100 lg:hidden"
            aria-label="Open navigation"
          >
            <MenuIcon className="h-4 w-4" />
          </button>

          <div className="min-w-0">
            <p className="flex items-center gap-1.5 font-mono text-[8px] uppercase tracking-[0.2em] text-stone-600">
              Filing Room
              <span className="text-stone-700">/</span>
              <span className={lastRoute ? "text-stone-500" : ""}>
                {lastRoute === "retrieval"
                  ? "grounded inquiry"
                  : lastRoute === "conversational"
                    ? "direct answer"
                    : lastRoute === "blocked"
                      ? "guardrail"
                      : "inquiry"}
              </span>
            </p>
            <h1 className="max-w-[46vw] truncate font-serif text-[17px] leading-tight text-stone-200 sm:max-w-md">
              {active?.title ?? "New inquiry"}
            </h1>
          </div>

          <div className="ml-auto flex items-center gap-2">
            <div className="hidden items-center gap-2 rounded-full border border-white/[0.08] bg-white/[0.02] px-2.5 py-1 sm:flex">
              <span className="relative flex h-1.5 w-1.5">
                {backendStatus === "online" ? (
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-300/50" />
                ) : null}
                <span
                  className={`relative inline-flex h-1.5 w-1.5 rounded-full ${
                    backendStatus === "online"
                      ? "bg-emerald-300"
                      : backendStatus === "offline"
                        ? "bg-red-400"
                        : "bg-stone-400"
                  }`}
                />
              </span>
              <span className="font-mono text-[8.5px] uppercase tracking-[0.14em] text-stone-500">
                {backendStatus === "online"
                  ? "API online"
                  : backendStatus === "offline"
                    ? "API offline"
                    : "checking"}
              </span>
            </div>
            <button
              type="button"
              onClick={() => setIngestOpen(true)}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-white/10 px-2.5 text-[11px] font-medium text-stone-400 transition hover:border-emerald-300/25 hover:text-emerald-100"
            >
              <DatabaseIcon className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Index filing</span>
            </button>
          </div>
        </header>

        <ChatView
          conversation={active}
          backendStatus={backendStatus}
          onSend={(prompt) => void send(prompt)}
          onRetry={(prompt) => void send(prompt)}
          onOpenSource={setSourceSheet}
          onIndex={() => setIngestOpen(true)}
        />

        <Composer
          onSend={(prompt) => void send(prompt)}
          pending={isPending}
          backendOnline={backendStatus !== "offline"}
        />
      </main>

      <IngestDialog
        open={ingestOpen}
        backendStatus={backendStatus}
        onClose={() => setIngestOpen(false)}
        onQueued={recheck}
      />

      <SourceSheet source={sourceSheet} onClose={() => setSourceSheet(null)} />
    </div>
  );
}
