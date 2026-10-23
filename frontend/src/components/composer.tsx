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
    <div className="border-t border-white/[0.06] bg-[#080b10]/80 px-4 py-4 backdrop-blur-xl sm:px-8">
      <div className="mx-auto w-full max-w-3xl">
        <div className="relative rounded-2xl border border-white/[0.09] bg-[#0d1219] shadow-[0_18px_60px_rgba(0,0,0,0.4)] transition focus-within:border-emerald-300/30 focus-within:shadow-[0_18px_60px_rgba(0,0,0,0.4),0_0_0_3px_rgba(110,231,183,0.06)]">
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
            className="max-h-52 min-h-[58px] w-full resize-none bg-transparent px-4 py-4 pr-14 text-[14.5px] leading-6 text-stone-200 outline-none placeholder:text-stone-600 sm:px-5"
          />
          <button
            type="button"
            onClick={submit}
            disabled={!value.trim() || pending}
            aria-label="Send question"
            className="absolute bottom-3 right-3 flex h-9 w-9 items-center justify-center rounded-xl bg-emerald-300 text-[#04100b] transition hover:bg-emerald-200 disabled:cursor-not-allowed disabled:bg-white/[0.06] disabled:text-stone-600"
          >
            {pending ? (
              <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-stone-500 border-t-transparent" />
            ) : (
              <ArrowUpIcon className="h-4 w-4" />
            )}
          </button>
        </div>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2 px-1">
          <p className="text-[10.5px] text-stone-600">
            Answers are limited to indexed SEC filings. Verify against the
            cited source.
          </p>
          <p className="hidden items-center gap-1.5 font-mono text-[9.5px] uppercase tracking-[0.1em] text-stone-700 sm:flex">
            <kbd className="rounded border border-white/10 px-1 py-0.5 text-[9px] text-stone-500">
              Enter
            </kbd>
            send
            <kbd className="ml-1 rounded border border-white/10 px-1 py-0.5 text-[9px] text-stone-500">
              Shift + Enter
            </kbd>
            newline
          </p>
        </div>
      </div>
    </div>
  );
}
