"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Polls `url` on an interval and returns the latest JSON response, starting
 * from `initial` (the server-rendered first paint). Plain polling, not
 * websockets: a few seconds of latency is invisible live.
 *
 * Fires an immediate fetch whenever `url` changes, not just on the next tick,
 * so a filter change doesn't leave the table showing stale data.
 *
 * Also returns `refresh()`, an on-demand refetch for a UI-triggered
 * "sync now" action.
 */
export function usePolling<T>(url: string, initial: T, intervalMs = 4000): [T, () => Promise<void>] {
  const [data, setData] = useState<T>(initial);
  const tickRef = useRef<() => Promise<void>>(async () => {});

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const res = await fetch(url, { cache: "no-store" });
        if (!res.ok) return;
        const json = await res.json();
        if (!cancelled) setData(json);
      } catch {
        // Transient fetch failure, keep showing the last good data, try again next tick.
      }
    };
    tickRef.current = tick;
    tick();
    const interval = setInterval(tick, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [url, intervalMs]);

  const refresh = useCallback(() => tickRef.current(), []);

  return [data, refresh];
}
