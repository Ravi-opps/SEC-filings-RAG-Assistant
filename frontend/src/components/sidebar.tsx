"use client";

import {
  DatabaseIcon,
  PlusIcon,
  TrashIcon,
  XIcon,
} from "@/components/icons";
import { relativeTime } from "@/lib/format";
import type { BackendStatus } from "@/lib/use-backend-status";
import type { Conversation } from "@/lib/types";

const STATUS_META: Record<
  BackendStatus,
  { label: string; dot: string; text: string; detail: string }
> = {
  online: {
    label: "Backend online",
    dot: "bg-emerald-300",
    text: "text-emerald-200",
    detail: "FastAPI · port 8000",
  },
  offline: {
    label: "Backend offline",
    dot: "bg-red-400",
    text: "text-red-300",
    detail: "Start the API to ask questions",
  },
  checking: {
    label: "Checking backend",
    dot: "bg-stone-400",
    text: "text-stone-400",
    detail: "Waiting for a response",
  },
};

export function Sidebar({
  conversations,
  activeId,
  open,
  backendStatus,
  onClose,
  onSelect,
  onNew,
  onDelete,
  onIndex,
  onRecheck,
}: {
  conversations: Conversation[];
  activeId: string | null;
  open: boolean;
  backendStatus: BackendStatus;
  onClose: () => void;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onIndex: () => void;
  onRecheck: () => void;
}) {
  const status = STATUS_META[backendStatus];

  return (
    <>
      <button
        type="button"
        aria-label="Close navigation"
        onClick={onClose}
        className={`fixed inset-0 z-30 bg-black/60 backdrop-blur-sm transition-opacity lg:hidden ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
      />
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-[286px] shrink-0 flex-col border-r border-white/[0.06] bg-[#080c11] transition-transform duration-300 ease-out lg:static lg:z-auto lg:translate-x-0 ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center gap-3 px-5 pb-5 pt-5">
          <div className="relative h-9 w-9 shrink-0 overflow-hidden rounded-xl border border-emerald-300/25 bg-emerald-300/[0.08]">
            <span className="absolute left-2.5 top-2.5 h-[1.5px] w-[18px] rounded-full bg-emerald-200/85" />
            <span className="absolute left-2.5 top-[17px] h-[1.5px] w-[13px] rounded-full bg-emerald-200/55" />
            <span className="absolute left-2.5 top-[23px] h-[1.5px] w-[16px] rounded-full bg-emerald-200/35" />
          </div>
          <div className="min-w-0">
            <p className="truncate font-serif text-[21px] leading-none text-stone-100">
              Filing Room
            </p>
            <p className="mt-1.5 font-mono text-[8.5px] uppercase tracking-[0.22em] text-stone-600">
              SEC research desk
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="ml-auto rounded-lg p-1.5 text-stone-500 transition hover:bg-white/5 hover:text-stone-200 lg:hidden"
            aria-label="Close navigation"
          >
            <XIcon className="h-4 w-4" />
          </button>
        </div>

        <div className="px-3">
          <button
            type="button"
            onClick={onNew}
            className="group flex h-10 w-full items-center gap-2 rounded-xl border border-emerald-300/20 bg-emerald-300/[0.07] px-3 text-[12.5px] font-medium text-emerald-100 transition hover:border-emerald-300/35 hover:bg-emerald-300/[0.11] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-300/30"
          >
            <PlusIcon className="h-4 w-4 transition group-hover:rotate-90" />
            New inquiry
            <span className="ml-auto font-mono text-[9px] uppercase tracking-[0.14em] text-emerald-200/40">
              chat
            </span>
          </button>
        </div>

        <div className="mt-6 flex min-h-0 flex-1 flex-col">
          <div className="flex items-center justify-between px-5 pb-2">
            <p className="font-mono text-[9px] uppercase tracking-[0.2em] text-stone-600">
              Inquiries
            </p>
            <span className="font-mono text-[9px] text-stone-700">
              {conversations.length}
            </span>
          </div>

          <nav className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
            {conversations.length === 0 ? (
              <p className="px-3 py-4 text-[11.5px] leading-5 text-stone-600">
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
                            ? "bg-white/[0.055] text-stone-100"
                            : "text-stone-400 hover:bg-white/[0.03] hover:text-stone-200"
                        }`}
                      >
                        <span className="line-clamp-1 pr-1 text-[12px] leading-5">
                          {conversation.title}
                        </span>
                        <span className="flex items-center gap-2 font-mono text-[8.5px] uppercase tracking-[0.12em] text-stone-600">
                          {relativeTime(conversation.updatedAt)}
                          <span className="text-stone-700">/</span>
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
                        className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-md p-1.5 text-stone-600 opacity-0 transition hover:bg-red-300/10 hover:text-red-300 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300/30 group-hover:opacity-100 max-lg:opacity-100"
                      >
                        <TrashIcon className="h-3.5 w-3.5" />
                      </button>
                      {isActive ? (
                        <span className="absolute bottom-2 left-0 top-2 w-[2px] rounded-full bg-emerald-300/70" />
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </nav>
        </div>

        <div className="border-t border-white/[0.06] p-3">
          <div className="mb-2 rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5">
            <button
              type="button"
              onClick={onRecheck}
              className="flex w-full items-center gap-2 text-left"
              title="Check backend connection"
            >
              <span className="relative flex h-2 w-2">
                {backendStatus === "online" ? (
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-300/50" />
                ) : null}
                <span
                  className={`relative inline-flex h-2 w-2 rounded-full ${status.dot}`}
                />
              </span>
              <span className={`text-[11px] font-medium ${status.text}`}>
                {status.label}
              </span>
            </button>
            <p className="mt-1 pl-4 text-[9.5px] text-stone-600">
              {status.detail}
            </p>
          </div>

          <button
            type="button"
            onClick={onIndex}
            className="flex h-9 w-full items-center gap-2 rounded-lg px-3 text-[11.5px] font-medium text-stone-400 transition hover:bg-white/[0.04] hover:text-stone-200"
          >
            <DatabaseIcon className="h-3.5 w-3.5" />
            Index a filing
            <span className="ml-auto font-mono text-[8.5px] uppercase tracking-[0.12em] text-stone-700">
              10-K / Q
            </span>
          </button>

          <p className="px-3 pb-1 pt-2 text-[9px] leading-4 text-stone-700">
            Answers grounded on SEC EDGAR filings. Not investment advice.
          </p>
        </div>
      </aside>
    </>
  );
}
