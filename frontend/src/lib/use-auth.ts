"use client";

import { useCallback, useEffect, useState } from "react";

import {
  AUTH_EVENT,
  clearSession,
  fetchCurrentUser,
  login as loginRequest,
  readStoredUser,
  readToken,
  signup as signupRequest,
  storeSession,
} from "./api";
import type { AuthUser } from "./types";

export type AuthStatus = "loading" | "anonymous" | "authenticated";

export function useAuth() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");

  useEffect(() => {
    let cancelled = false;

    const sync = () => {
      if (!readToken()) {
        if (!cancelled) {
          setUser(null);
          setStatus("anonymous");
        }
        return;
      }

      const cached = readStoredUser();
      if (cached && !cancelled) {
        setUser(cached);
        setStatus("authenticated");
      }

      void fetchCurrentUser()
        .then((current) => {
          if (cancelled) return;
          setUser(current);
          setStatus("authenticated");
        })
        .catch(() => {
          if (cancelled) return;
          clearSession();
          setUser(null);
          setStatus("anonymous");
        });
    };

    sync();
    window.addEventListener(AUTH_EVENT, sync);
    return () => {
      cancelled = true;
      window.removeEventListener(AUTH_EVENT, sync);
    };
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const session = await loginRequest(username, password);
    storeSession(session);
    setUser(session.user);
    setStatus("authenticated");
    return session.user;
  }, []);

  const signup = useCallback(async (username: string, password: string) => {
    const session = await signupRequest(username, password);
    storeSession(session);
    setUser(session.user);
    setStatus("authenticated");
    return session.user;
  }, []);

  const logout = useCallback(() => {
    clearSession();
    setUser(null);
    setStatus("anonymous");
  }, []);

  return { user, status, login, signup, logout };
}
