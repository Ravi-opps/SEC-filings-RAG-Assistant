"use client";

import { useCallback, useEffect, useState } from "react";

import { AuthLoading, AuthScreen } from "@/components/auth-screen";
import { ChatView } from "@/components/chat-view";
import { Composer } from "@/components/composer";
import { DatabaseIcon, MenuIcon } from "@/components/icons";
import { IngestDialog } from "@/components/ingest-dialog";
import { Sidebar } from "@/components/sidebar";
import { SourceSheet } from "@/components/source-card";
import { askQuestionStream } from "@/lib/api";
import { useAuth } from "@/lib/use-auth";
import { makeTitle, useChatStore } from "@/lib/use-chat-store";
import { useBackendStatus } from "@/lib/use-backend-status";
import type { ChatMessage, Source } from "@/lib/types";

const ROUTE_LABEL: Record<string, string> = {
  retrieval: "grounded inquiry",
  conversational: "direct answer",
  blocked: "guardrail",
};

export default function Home() {
  const auth = useAuth();
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

      let streamed = "";
      let lastFlush = 0;
      let flushTimer: number | null = null;

      const flush = () => {
        lastFlush = Date.now();
        patchMessage(conversationId, assistantMessage.id, {
          content: streamed,
        });
      };
      const clearFlushTimer = () => {
        if (flushTimer !== null) {
          window.clearTimeout(flushTimer);
          flushTimer = null;
        }
      };
      const scheduleFlush = () => {
        if (flushTimer !== null) return;
        const wait = Math.max(0, 60 - (Date.now() - lastFlush));
        flushTimer = window.setTimeout(() => {
          flushTimer = null;
          flush();
        }, wait);
      };

      try {
        const response = await askQuestionStream(text, conversationId, {
          onStatus: (stage) =>
            patchMessage(conversationId, assistantMessage.id, { stage }),
          onSources: (sources) =>
            patchMessage(conversationId, assistantMessage.id, { sources }),
          onDelta: (chunk) => {
            streamed += chunk;
            if (Date.now() - lastFlush >= 60) {
              clearFlushTimer();
              flush();
            } else {
              scheduleFlush();
            }
          },
        });

        clearFlushTimer();
        patchMessage(conversationId, assistantMessage.id, {
          status: response.type === "blocked" ? "blocked" : "complete",
          route: response.type,
          content: response.answer ?? streamed,
          sources: response.sources ?? [],
          stage: undefined,
        });
      } catch (caught) {
        clearFlushTimer();
        patchMessage(conversationId, assistantMessage.id, {
          status: "error",
          content: streamed,
          stage: undefined,
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

  if (auth.status === "loading") {
    return <AuthLoading />;
  }

  if (!auth.user) {
    return <AuthScreen onLogin={auth.login} onSignup={auth.signup} />;
  }

  const isPending = activeId ? pendingIds.has(activeId) : false;
  const lastRoute = [...(active?.messages ?? [])]
    .reverse()
    .find((message) => message.route)?.route;

  return (
    <div className="flex h-dvh overflow-hidden bg-canvas text-body">
      <Sidebar
        conversations={store.conversations}
        activeId={activeId}
        open={sidebarOpen}
        backendStatus={backendStatus}
        user={auth.user}
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
        onLogout={() => {
          store.clearAll();
          auth.logout();
        }}
      />

      <main className="relative flex min-w-0 flex-1 flex-col bg-canvas bg-[radial-gradient(120%_75%_at_50%_-20%,rgba(215,255,62,0.045),transparent_55%)]">
        <header className="z-20 flex h-16 shrink-0 items-center gap-3 border-b border-hairline bg-canvas/80 px-4 backdrop-blur-xl sm:px-6">
          <button
            type="button"
            onClick={() => setSidebarOpen(true)}
            className="rounded-lg border border-hairline p-1.5 text-muted transition hover:border-hairline-soft hover:bg-surface-soft hover:text-ink lg:hidden"
            aria-label="Open navigation"
          >
            <MenuIcon className="h-4 w-4" />
          </button>

          <div className="min-w-0">
            <p className="flex items-center gap-1.5 font-mono text-[9px] uppercase tracking-[0.2em] text-muted-soft">
              Filing Room
              <span className="text-hairline">/</span>
              <span className={lastRoute ? "text-lemon/80" : ""}>
                {ROUTE_LABEL[lastRoute ?? ""] ?? "inquiry"}
              </span>
            </p>
            <h1 className="max-w-[46vw] truncate font-serif text-[17px] leading-tight text-ink sm:max-w-md">
              {active?.title ?? "New inquiry"}
            </h1>
          </div>

          <div className="ml-auto flex items-center gap-2">
            <div className="hidden items-center gap-2 rounded-full border border-hairline bg-surface-soft px-2.5 py-1 sm:flex">
              <span className="relative flex h-1.5 w-1.5">
                {backendStatus === "online" ? (
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-lemon/50" />
                ) : null}
                <span
                  className={`relative inline-flex h-1.5 w-1.5 rounded-full ${
                    backendStatus === "online"
                      ? "bg-lemon"
                      : backendStatus === "offline"
                        ? "bg-danger"
                        : "bg-muted"
                  }`}
                />
              </span>
              <span className="font-mono text-[9px] uppercase tracking-[0.14em] text-muted">
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
              className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-hairline bg-surface-soft px-3 text-[11.5px] font-medium text-muted transition hover:border-lemon/30 hover:text-ink"
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

      {sourceSheet ? (
        <SourceSheet
          source={sourceSheet}
          onClose={() => setSourceSheet(null)}
        />
      ) : null}
    </div>
  );
}
