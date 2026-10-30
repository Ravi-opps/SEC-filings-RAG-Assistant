"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ArrowUpIcon } from "@/components/icons";

export function Composer({
  onSend,
  pending,
  backendOnline,
}: {
  onSend: (prompt: string) => void;
  pending: boolean;
  backendOnline: boolean;
}) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const resize = useCallback(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`;
  }, []);

  useEffect(() => {
    resize();
  }, [value, resize]);

  const submit = () => {
    const prompt = value.trim();
    if (!prompt || pending) return;
    onSend(prompt);
    setValue("");
    requestAnimationFrame(() => textareaRef.current?.focus());
  };

  return (
    <div className="border-t border-hairline bg-canvas/80 px-4 py-4 backdrop-blur-xl sm:px-8">
      <div className="mx-auto w-full max-w-3xl">
        <div className="relative rounded-xl border border-hairline bg-surface-card shadow-card transition focus-within:border-lemon/40 focus-within:ring-2 focus-within:ring-lemon/10">
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submit();
              }
            }}
            rows={1}
            placeholder={
              backendOnline
                ? "Ask about a filing, a risk factor, or a financial trend..."
                : "Backend offline — start FastAPI, then ask a question..."
            }
            aria-label="Ask about SEC filings"
            className="max-h-52 min-h-[58px] w-full resize-none bg-transparent px-4 py-4 pr-14 text-[14.5px] leading-6 text-ink outline-none placeholder:text-muted-soft sm:px-5"
          />
          <button
            type="button"
            onClick={submit}
            disabled={!value.trim() || pending}
            aria-label="Send question"
            className="absolute bottom-3 right-3 flex h-9 w-9 items-center justify-center rounded-lg bg-lemon text-on-lemon transition hover:bg-lemon-bright disabled:cursor-not-allowed disabled:bg-surface-elevated disabled:text-muted-soft"
          >
            {pending ? (
              <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-on-lemon/30 border-t-on-lemon" />
            ) : (
              <ArrowUpIcon className="h-4 w-4" />
            )}
          </button>
        </div>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2 px-1">
          <p className="text-[10.5px] text-muted-soft">
            Answers are limited to indexed SEC filings. Verify against the
            cited source.
          </p>
          <p className="hidden items-center gap-1.5 font-mono text-[9.5px] uppercase tracking-[0.1em] text-muted-soft sm:flex">
            <kbd className="rounded border border-hairline bg-surface-soft px-1 py-0.5 text-[9px] text-muted">
              Enter
            </kbd>
            send
            <kbd className="ml-1 rounded border border-hairline bg-surface-soft px-1 py-0.5 text-[9px] text-muted">
              Shift + Enter
            </kbd>
            newline
          </p>
        </div>
      </div>
    </div>
  );
}
