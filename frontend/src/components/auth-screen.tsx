"use client";

import { useState } from "react";

import { AlertIcon } from "@/components/icons";
import type { AuthUser } from "@/lib/types";

type Mode = "login" | "signup";

export function AuthLoading() {
  return (
    <div className="flex h-dvh items-center justify-center bg-[#070a0f]">
      <span className="h-5 w-5 animate-spin rounded-full border-2 border-emerald-300/20 border-t-emerald-300" />
    </div>
  );
}

export function AuthScreen({
  onLogin,
  onSignup,
}: {
  onLogin: (username: string, password: string) => Promise<AuthUser>;
  onSignup: (username: string, password: string) => Promise<AuthUser>;
}) {
  const [mode, setMode] = useState<Mode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isSignup = mode === "signup";

  const switchMode = (next: Mode) => {
    setMode(next);
    setError(null);
    setConfirm("");
  };

  const submit = async () => {
    const normalized = username.trim();
    if (!normalized) {
      setError("Enter a username.");
      return;
    }
    if (isSignup && password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (isSignup && password !== confirm) {
      setError("Passwords do not match.");
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      if (isSignup) {
        await onSignup(normalized, password);
      } else {
        await onLogin(normalized, password);
      }
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "The account request could not be completed.",
      );
      setSubmitting(false);
    }
  };

  return (
    <div className="relative flex min-h-dvh items-center justify-center overflow-hidden bg-[#070a0f] px-4 py-10 text-stone-200">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(90%_60%_at_50%_-10%,rgba(110,231,183,0.08),transparent_60%)]" />
      <div className="pointer-events-none absolute -left-24 top-1/2 -translate-y-1/2 select-none font-serif text-[320px] leading-none text-white/[0.015]">
        10-K
      </div>

      <div className="relative w-full max-w-md">
        <div className="mb-6 flex items-center gap-3">
          <div className="relative h-10 w-10 shrink-0 overflow-hidden rounded-xl border border-emerald-300/25 bg-emerald-300/[0.08]">
            <span className="absolute left-2.5 top-2.5 h-[1.5px] w-5 rounded-full bg-emerald-200/85" />
            <span className="absolute left-2.5 top-[19px] h-[1.5px] w-3.5 rounded-full bg-emerald-200/55" />
            <span className="absolute left-2.5 top-[25px] h-[1.5px] w-4 rounded-full bg-emerald-200/35" />
          </div>
          <div>
            <p className="font-serif text-[23px] leading-none text-stone-100">
              Filing Room
            </p>
            <p className="mt-1.5 font-mono text-[8.5px] uppercase tracking-[0.22em] text-stone-600">
              SEC research desk
            </p>
          </div>
        </div>

        <div className="overflow-hidden rounded-2xl border border-white/10 bg-[#0b1017]/95 shadow-[0_30px_100px_rgba(0,0,0,0.55)]">
          <div className="border-b border-white/[0.07] px-6 py-5">
            <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-emerald-200/70">
              {isSignup ? "New account" : "Welcome back"}
            </p>
            <h1 className="mt-2 font-serif text-[28px] leading-none text-stone-100">
              {isSignup ? "Create your desk." : "Sign in to the desk."}
            </h1>
            <p className="mt-2.5 text-[12px] leading-5 text-stone-500">
              {isSignup
                ? "Pick a username and a password. Your conversations stay on this browser."
                : "Use the username and password you registered with."}
            </p>
          </div>

          <div className="px-6 pb-6 pt-5">
            <div className="mb-5 flex rounded-xl border border-white/10 bg-black/30 p-1">
              {(["login", "signup"] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  onClick={() => switchMode(option)}
                  className={`h-8 flex-1 rounded-lg font-mono text-[10.5px] uppercase tracking-[0.14em] transition ${
                    mode === option
                      ? "bg-emerald-300/15 text-emerald-100 shadow-[inset_0_0_0_1px_rgba(110,231,183,0.2)]"
                      : "text-stone-500 hover:text-stone-300"
                  }`}
                >
                  {option === "login" ? "Sign in" : "Sign up"}
                </button>
              ))}
            </div>

            <form
              onSubmit={(event) => {
                event.preventDefault();
                void submit();
              }}
              className="space-y-4"
            >
              <div>
                <label
                  htmlFor="username"
                  className="mb-1.5 block text-[11px] font-medium text-stone-400"
                >
                  Username
                </label>
                <input
                  id="username"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  autoFocus
                  autoComplete="username"
                  spellCheck={false}
                  placeholder="ada_lovelace"
                  className="h-11 w-full rounded-xl border border-white/10 bg-black/30 px-3.5 text-[13px] text-stone-100 outline-none transition placeholder:text-stone-700 focus:border-emerald-300/35 focus:ring-2 focus:ring-emerald-300/10"
                />
              </div>

              <div>
                <label
                  htmlFor="password"
                  className="mb-1.5 block text-[11px] font-medium text-stone-400"
                >
                  Password
                </label>
                <input
                  id="password"
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete={isSignup ? "new-password" : "current-password"}
                  placeholder={isSignup ? "At least 8 characters" : "Your password"}
                  className="h-11 w-full rounded-xl border border-white/10 bg-black/30 px-3.5 text-[13px] text-stone-100 outline-none transition placeholder:text-stone-700 focus:border-emerald-300/35 focus:ring-2 focus:ring-emerald-300/10"
                />
              </div>

              {isSignup ? (
                <div>
                  <label
                    htmlFor="confirm-password"
                    className="mb-1.5 block text-[11px] font-medium text-stone-400"
                  >
                    Confirm password
                  </label>
                  <input
                    id="confirm-password"
                    type="password"
                    value={confirm}
                    onChange={(event) => setConfirm(event.target.value)}
                    autoComplete="new-password"
                    placeholder="Repeat the password"
                    className="h-11 w-full rounded-xl border border-white/10 bg-black/30 px-3.5 text-[13px] text-stone-100 outline-none transition placeholder:text-stone-700 focus:border-emerald-300/35 focus:ring-2 focus:ring-emerald-300/10"
                  />
                </div>
              ) : null}

              {error ? (
                <div
                  role="alert"
                  className="flex items-start gap-2 rounded-lg border border-red-300/15 bg-red-300/[0.05] px-3 py-2.5"
                >
                  <AlertIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-300/80" />
                  <p className="text-[11.5px] leading-5 text-stone-400">
                    {error}
                  </p>
                </div>
              ) : null}

              <button
                type="submit"
                disabled={submitting || !username.trim() || !password}
                className="flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-emerald-300 text-[13px] font-semibold text-[#05100c] transition hover:bg-emerald-200 disabled:cursor-not-allowed disabled:bg-white/[0.06] disabled:text-stone-600"
              >
                {submitting ? (
                  <>
                    <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-[#05100c]/30 border-t-[#05100c]" />
                    {isSignup ? "Creating account" : "Signing in"}
                  </>
                ) : isSignup ? (
                  "Create account"
                ) : (
                  "Sign in"
                )}
              </button>
            </form>

            <p className="mt-4 text-center text-[10px] leading-4 text-stone-600">
              Passwords are hashed with bcrypt. Sessions use a signed JWT.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
