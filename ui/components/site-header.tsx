"use client";

import type { ReactNode } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { LiveClock } from "./LiveClock";

const TITLES: { prefix: string; title: string; subtitle: string }[] = [
  { prefix: "/tickets/", title: "Ticket detail", subtitle: "Worker vs verifier reasoning · full audit trail" },
  { prefix: "/", title: "Tickets", subtitle: "Live ticket pipeline · search, filter, and review" },
];

export function SiteHeader({ userMenu }: { userMenu?: ReactNode }) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const isEscalationView = pathname === "/" && searchParams.get("status") === "needs_review";
  const meta = isEscalationView
    ? { title: "Escalation", subtitle: "Tickets needing human review" }
    : (TITLES.find((t) => (t.prefix === "/" ? pathname === "/" : pathname.startsWith(t.prefix))) ?? TITLES.at(-1)!);

  return (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b border-divider bg-neutral-100 px-3">
      <SidebarTrigger className="text-text hover:text-text" />
      <Separator orientation="vertical" className="!h-4 !self-center" />
      <div className="heading-label min-w-0 text-[14px] tracking-[0.08em] text-text">{meta.title}</div>
      <span className="font-data hidden min-w-0 truncate text-[11px] text-neutral-500 md:inline">{meta.subtitle}</span>
      <div className="ml-auto flex shrink-0 items-center gap-4">
        <LiveClock />
        {userMenu}
      </div>
    </header>
  );
}
