"use client";

import { useCallback, useEffect, useState } from "react";

import { checkBackend } from "./api";

export type BackendStatus = "checking" | "online" | "offline";

export function useBackendStatus() {
  const [status, setStatus] = useState<BackendStatus>("checking");

  const check = useCallback(async () => {
    try {
      const result = await checkBackend();
      setStatus(result?.service ? "online" : "offline");
    } catch {
      setStatus("offline");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    const run = async () => {
      try {
        const result = await checkBackend();
        if (!cancelled) setStatus(result?.service ? "online" : "offline");
      } catch {
        if (!cancelled) setStatus("offline");
      }
    };

    void run();
    const interval = window.setInterval(run, 30_000);

    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, []);

  return { status, recheck: check };
}
