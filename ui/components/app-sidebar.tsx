"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { GitCompareArrows, ListTodo } from "lucide-react";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar";

// Escalation is a shortcut into the same Tickets list (status=needs_review),
// not a separate page.
const CONTROL_ROOM = [
  {
    href: "/",
    label: "Tickets",
    icon: ListTodo,
    isActive: (p: string, s: URLSearchParams) => (p === "/" || p.startsWith("/tickets/")) && s.get("status") !== "needs_review",
  },
  {
    href: "/?status=needs_review",
    label: "Escalation",
    icon: GitCompareArrows,
    isActive: (p: string, s: URLSearchParams) => p === "/" && s.get("status") === "needs_review",
  },
] as const;

export function AppSidebar() {
  const pathname = usePathname();
  const searchParams = useSearchParams();

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="border-b border-sidebar-border p-2">
        <Link
          href="/"
          className="flex items-center gap-2.5 rounded-md px-2 py-2 text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0"
        >
          <span className="font-heading flex size-8 shrink-0 items-center justify-center border border-sidebar-border bg-background text-[13px] font-bold tracking-[0.06em]">
            BS
          </span>
          <span className="flex min-w-0 flex-col leading-tight group-data-[collapsible=icon]:hidden">
            <span className="font-heading text-[16px] font-bold tracking-[0.06em]">BACKSTOP</span>
            <span className="font-data mt-0.5 text-[9.5px] tracking-[0.04em] text-neutral-600">CONTROL ROOM</span>
          </span>
        </Link>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel className="font-data tracking-[0.08em]">Control room</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {CONTROL_ROOM.map((item) => (
                <SidebarMenuItem key={item.href}>
                  <SidebarMenuButton
                    render={<Link href={item.href} />}
                    isActive={item.isActive(pathname, searchParams)}
                    tooltip={item.label}
                    className="text-sidebar-foreground hover:text-sidebar-accent-foreground"
                  >
                    <item.icon />
                    <span>{item.label}</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter className="border-t border-sidebar-border">
        <div className="flex items-center gap-2 px-2 py-1 group-data-[collapsible=icon]:justify-center">
          <span
            className="h-[7px] w-[7px] shrink-0 bg-accent"
            style={{ animation: "bsPulse 1.6s ease-in-out infinite" }}
          />
          <span className="font-data text-[10px] tracking-[0.06em] text-neutral-600 group-data-[collapsible=icon]:hidden">
            STREAM LIVE
          </span>
        </div>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
