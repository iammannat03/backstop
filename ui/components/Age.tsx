"use client";

import { useEffect, useState } from "react";
import { formatAge } from "@/lib/format";

/**
 * Renders elapsed time since `fromIso`, ticking every second. Defers the
 * actual value to the client to avoid a hydration mismatch, same as LiveClock.
 */
export function Age({ fromIso }: { fromIso: string }) {
  const [display, setDisplay] = useState<string | null>(null);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDisplay(formatAge(fromIso));
    const interval = setInterval(() => setDisplay(formatAge(fromIso)), 1000);
    return () => clearInterval(interval);
  }, [fromIso]);

  return <span suppressHydrationWarning>{display ?? "--:--:--"}</span>;
}
