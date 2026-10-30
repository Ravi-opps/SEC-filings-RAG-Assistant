"use client";

import {
  DatabaseIcon,
  LogOutIcon,
  PlusIcon,
  SpikeMark,
  TrashIcon,
  XIcon,
} from "@/components/icons";
import { relativeTime } from "@/lib/format";
import type { BackendStatus } from "@/lib/use-backend-status";
import type { AuthUser, Conversation } from "@/lib/types";

const STATUS_META: Record<
  BackendStatus,
  { label: string; dot: string; text: string; detail: string }
> = {
  online: {
    label: "Backend online",
    dot: "bg-lemon",
    text: "text-lemon",
    detail: "FastAPI · port 8000",
  },
  offline: {
    label: "Backend offline",
    dot: "bg-danger",
    text: "text-danger",
    detail: "Start the API to ask questions",
  },
  checking: {
    label: "Checking backend",
    dot: "bg-muted",
    text: "text-muted",
    detail: "Waiting for a response",
  },
};

export function Sidebar({
  conversations,
  activeId,
  open,
  backendStatus,
  user,
  onClose,
  onSelect,
  onNew,
  onDelete,
  onIndex,
  onRecheck,
  onLogout,
}: {
  conversations: Conversation[];
  activeId: string | null;
  open: boolean;
  backendStatus: BackendStatus;
  user: AuthUser;
  onClose: () => void;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onIndex: () => void;
  onRecheck: () => void;
  onLogout: () => void;
}) {
  const status = STATUS_META[backendStatus];

  return (
    <>
      <button
        type="button"
        aria-label="Close navigation"
        onClick={onClose}
        className={`fixed inset-0 z-30 bg-black/70 backdrop-blur-sm transition-opacity lg:hidden ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
      />
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-[288px] shrink-0 flex-col border-r border-hairline bg-surface transition-transform duration-300 ease-out lg:static lg:z-auto lg:translate-x-0 ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center gap-3 px-5 pb-5 pt-5">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-lemon/25 bg-lemon/[0.08] text-lemon">
            <SpikeMark className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <p className="truncate font-serif text-[21px] leading-none text-ink">
              Filing Room
            </p>
            <p className="mt-1.5 font-mono text-[9px] uppercase tracking-[0.22em] text-muted-soft">
              SEC research desk
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="ml-auto rounded-lg p-1.5 text-muted-soft transition hover:bg-surface-card hover:text-ink lg:hidden"
            aria-label="Close navigation"
          >
            <XIcon className="h-4 w-4" />
          </button>
        </div>

        <div className="px-3">
          <button
            type="button"
            onClick={onNew}
            className="group flex h-10 w-full items-center gap-2 rounded-lg bg-lemon px-3 text-[12.5px] font-semibold text-on-lemon transition hover:bg-lemon-bright focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lemon/40"
          >
            <PlusIcon className="h-4 w-4 transition duration-200 group-hover:rotate-90" />
            New inquiry
            <span className="ml-auto font-mono text-[9px] uppercase tracking-[0.14em] text-on-lemon/55">
              ⌘K
            </span>
          </button>
        </div>

        <div className="mt-6 flex min-h-0 flex-1 flex-col">
          <div className="flex items-center justify-between px-5 pb-2">
            <p className="font-mono text-[9px] uppercase tracking-[0.2em] text-muted-soft">
              Inquiries
            </p>
            <span className="font-mono text-[9px] text-muted-soft">
              {conversations.length}
            </span>
          </div>

          <nav className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
            {conversations.length === 0 ? (
              <p className="px-3 py-4 text-[11.5px] leading-5 text-muted-soft">
                No inquiries yet. Ask a question to start a research thread.
              </p>
            ) : (
              <ul className="space-y-0.5">
                {conversations.map((conversation) => {
                  const isActive = conversation.id === activeId;
                  return (
                    <li key={conversation.id} className="group relative">
                      <button
                        type="button"
                        onClick={() => {
                          onSelect(conversation.id);
                          onClose();
                        }}
                        className={`flex w-full flex-col gap-1 rounded-lg py-2 pl-3 pr-8 text-left transition ${
                          isActive
                            ? "bg-surface-card text-ink"
                            : "text-muted hover:bg-surface-soft hover:text-body"
                        }`}
                      >
                        <span className="line-clamp-1 pr-1 text-[12px] leading-5">
                          {conversation.title}
                        </span>
                        <span className="flex items-center gap-2 font-mono text-[9px] uppercase tracking-[0.12em] text-muted-soft">
                          {relativeTime(conversation.updatedAt)}
                          <span className="text-hairline">/</span>
                          {conversation.messages.length} msg
                        </span>
                      </button>
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          onDelete(conversation.id);
                        }}
                        aria-label={`Delete ${conversation.title}`}
                        className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-md p-1.5 text-muted-soft opacity-0 transition hover:bg-danger/10 hover:text-danger focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger/30 group-hover:opacity-100 max-lg:opacity-100"
                      >
                        <TrashIcon className="h-3.5 w-3.5" />
                      </button>
                      {isActive ? (
                        <span className="absolute bottom-2 left-0 top-2 w-[2px] rounded-full bg-lemon" />
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </nav>
        </div>

        <div className="border-t border-hairline p-3">
          <div className="mb-2 rounded-xl border border-hairline bg-surface-soft px-3 py-2.5">
            <button
              type="button"
              onClick={onRecheck}
              className="flex w-full items-center gap-2 text-left"
              title="Check backend connection"
            >
              <span className="relative flex h-2 w-2">
                {backendStatus === "online" ? (
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-lemon/50" />
                ) : null}
                <span
                  className={`relative inline-flex h-2 w-2 rounded-full ${status.dot}`}
                />
              </span>
              <span className={`text-[11px] font-medium ${status.text}`}>
                {status.label}
              </span>
            </button>
            <p className="mt-1 pl-4 text-[9.5px] text-muted-soft">
              {status.detail}
            </p>
          </div>

          <div className="mb-2 flex items-center gap-2 rounded-xl border border-hairline bg-surface-soft px-2.5 py-2">
            <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-lemon/20 bg-lemon/[0.08] font-mono text-[11px] uppercase text-lemon">
              {user.username.slice(0, 1)}
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-[11.5px] font-medium text-body">
                {user.username}
              </p>
              <p className="font-mono text-[8px] uppercase tracking-[0.14em] text-muted-soft">
                signed in
              </p>
            </div>
            <button
              type="button"
              onClick={onLogout}
              aria-label="Sign out"
              title="Sign out"
              className="rounded-md p-1.5 text-muted-soft transition hover:bg-danger/10 hover:text-danger focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger/30"
            >
              <LogOutIcon className="h-3.5 w-3.5" />
            </button>
          </div>

          <button
            type="button"
            onClick={onIndex}
            className="flex h-9 w-full items-center gap-2 rounded-lg px-3 text-[11.5px] font-medium text-muted transition hover:bg-surface-soft hover:text-ink"
          >
            <DatabaseIcon className="h-3.5 w-3.5" />
            Index a filing
            <span className="ml-auto font-mono text-[9px] uppercase tracking-[0.12em] text-muted-soft">
              10-K / Q
            </span>
          </button>

          <p className="px-3 pb-1 pt-2 text-[9px] leading-4 text-muted-soft">
            Answers grounded on SEC EDGAR filings. Not investment advice.
          </p>
        </div>
      </aside>
    </>
  );
}
