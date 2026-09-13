"use client";

import { useEffect, useState } from "react";

function formatUtc(date: Date): string {
  return date.toISOString().slice(11, 19);
}

export function LiveClock() {
  const [time, setTime] = useState<string | null>(null);

  useEffect(() => {
    // Deliberately sets state synchronously on mount: the first render must
    // stay `null` (matching the server-rendered placeholder) to avoid a
    // hydration mismatch, and only fill in the real, client-only time value
    // once mounted.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setTime(formatUtc(new Date()));
    const interval = setInterval(() => setTime(formatUtc(new Date())), 1000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="flex items-center gap-[18px]">
      <div className="flex items-center gap-[7px]">
        <span className="h-[7px] w-[7px] shrink-0 bg-accent" style={{ animation: "bsPulse 1.6s ease-in-out infinite" }} />
        <span className="font-data text-[11px] tracking-[0.06em] text-neutral-700">STREAM LIVE</span>
      </div>
      <span className="font-data text-[12px] text-text" suppressHydrationWarning>
        {time ?? "--:--:--"}
      </span>
      <span className="font-data text-[11px] text-neutral-600">UTC · reg-eu-1</span>
    </div>
  );
}
