"use client";

import { useState } from "react";

import { BrandMark } from "@/components/brand-mark";
import { AlertIcon } from "@/components/icons";
import type { AuthUser } from "@/lib/types";

type Mode = "login" | "signup";

function BrandLockup({ size = "md" }: { size?: "md" | "lg" }) {
  const large = size === "lg";
  return (
    <div className="flex items-center gap-3">
      <BrandMark size={large ? "lg" : "md"} />
      <div>
        <p
          className={`font-serif leading-none text-ink ${
            large ? "text-[25px]" : "text-[21px]"
          }`}
        >
          Filing Room
        </p>
        <p className="mt-1.5 font-mono text-[9px] uppercase tracking-[0.22em] text-muted-soft">
          SEC research desk
        </p>
      </div>
    </div>
  );
}

export function AuthLoading() {
  return (
    <div className="flex h-dvh items-center justify-center bg-canvas">
      <span className="h-5 w-5 animate-spin rounded-full border-2 border-lemon/20 border-t-lemon" />
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
    <div className="relative flex min-h-dvh items-center justify-center overflow-hidden bg-canvas px-4 py-10 text-body">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(90%_60%_at_50%_-10%,rgba(215,255,62,0.055),transparent_60%)]" />
      <div className="pointer-events-none absolute -left-24 top-1/2 -translate-y-1/2 select-none font-serif text-[320px] leading-none text-ink/[0.015]">
        10-K
      </div>

      <div className="relative grid w-full max-w-5xl gap-12 lg:grid-cols-[1.05fr_0.95fr] lg:items-center">
        <div className="hidden lg:block">
          <BrandLockup size="lg" />
          <h1 className="mt-12 font-serif text-display-lg text-ink">
            Ask the filing.
            <br />
            <span className="text-muted">Not the open web.</span>
          </h1>
          <p className="mt-6 max-w-md text-[14px] leading-7 text-body">
            A retrieval desk for annual and quarterly reports. Every answer is
            built from indexed passages and carries a citation back to the exact
            source on SEC EDGAR.
          </p>
          <div className="mt-10 h-px w-24 bg-lemon" />
          <p className="mt-4 font-mono text-[9.5px] uppercase tracking-[0.22em] text-muted-soft">
            10-K / 10-Q / 8-K · EDGAR
          </p>
        </div>

        <div className="w-full max-w-md justify-self-center lg:ml-auto lg:justify-self-end">
          <div className="mb-6 lg:hidden">
            <BrandLockup />
          </div>

          <div className="overflow-hidden rounded-xl border border-hairline bg-surface shadow-float">
            <div className="border-b border-hairline px-6 py-5">
              <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-lemon">
                {isSignup ? "New account" : "Welcome back"}
              </p>
              <h2 className="mt-2 font-serif text-display-sm text-ink">
                {isSignup ? "Create your desk." : "Sign in to the desk."}
              </h2>
              <p className="mt-2.5 text-[12px] leading-5 text-muted">
                {isSignup
                  ? "Pick a username and a password. Your conversations stay on this browser."
                  : "Use the username and password you registered with."}
              </p>
            </div>

            <div className="px-6 pb-6 pt-5">
              <div className="mb-5 flex rounded-lg border border-hairline bg-canvas p-1">
                {(["login", "signup"] as const).map((option) => (
                  <button
                    key={option}
                    type="button"
                    onClick={() => switchMode(option)}
                    className={`h-8 flex-1 rounded-md font-mono text-[10.5px] uppercase tracking-[0.14em] transition ${
                      mode === option
                        ? "bg-surface-elevated text-ink shadow-[inset_0_0_0_1px_var(--color-hairline)]"
                        : "text-muted hover:text-body"
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
                    className="mb-1.5 block text-[11px] font-medium text-muted"
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
                    className="h-11 w-full rounded-lg border border-hairline bg-surface-soft px-3.5 text-[13px] text-ink outline-none transition placeholder:text-muted-soft focus:border-lemon/40 focus:ring-2 focus:ring-lemon/10"
                  />
                </div>

                <div>
                  <label
                    htmlFor="password"
                    className="mb-1.5 block text-[11px] font-medium text-muted"
                  >
                    Password
                  </label>
                  <input
                    id="password"
                    type="password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    autoComplete={isSignup ? "new-password" : "current-password"}
                    placeholder={
                      isSignup ? "At least 8 characters" : "Your password"
                    }
                    className="h-11 w-full rounded-lg border border-hairline bg-surface-soft px-3.5 text-[13px] text-ink outline-none transition placeholder:text-muted-soft focus:border-lemon/40 focus:ring-2 focus:ring-lemon/10"
                  />
                </div>

                {isSignup ? (
                  <div>
                    <label
                      htmlFor="confirm-password"
                      className="mb-1.5 block text-[11px] font-medium text-muted"
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
                      className="h-11 w-full rounded-lg border border-hairline bg-surface-soft px-3.5 text-[13px] text-ink outline-none transition placeholder:text-muted-soft focus:border-lemon/40 focus:ring-2 focus:ring-lemon/10"
                    />
                  </div>
                ) : null}

                {error ? (
                  <div
                    role="alert"
                    className="flex items-start gap-2 rounded-lg border border-danger/20 bg-danger/[0.06] px-3 py-2.5"
                  >
                    <AlertIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-danger" />
                    <p className="text-[11.5px] leading-5 text-body">{error}</p>
                  </div>
                ) : null}

                <button
                  type="submit"
                  disabled={submitting || !username.trim() || !password}
                  className="flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-lemon text-[13px] font-semibold text-on-lemon transition hover:bg-lemon-bright disabled:cursor-not-allowed disabled:bg-surface-elevated disabled:text-muted-soft"
                >
                  {submitting ? (
                    <>
                      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-on-lemon/30 border-t-on-lemon" />
                      {isSignup ? "Creating account" : "Signing in"}
                    </>
                  ) : isSignup ? (
                    "Create account"
                  ) : (
                    "Sign in"
                  )}
                </button>
              </form>

              <p className="mt-4 text-center text-[10px] leading-4 text-muted-soft">
                Passwords are hashed with bcrypt. Sessions use a signed JWT.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
