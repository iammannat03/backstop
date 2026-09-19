import type { Metadata } from "next";
import { Suspense } from "react";
import { Barlow, Barlow_Condensed, IBM_Plex_Mono } from "next/font/google";
import { AppSidebar } from "@/components/app-sidebar";
import { SiteHeader } from "@/components/site-header";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import "./globals.css";

const barlow = Barlow({
  subsets: ["latin"],
  weight: ["400", "500", "700"],
  variable: "--font-barlow",
  display: "swap",
});

const barlowCondensed = Barlow_Condensed({
  subsets: ["latin"],
  weight: ["400", "600", "700"],
  variable: "--font-barlow-condensed",
  display: "swap",
});

const ibmPlexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-ibm-plex-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Backstop: Control Room",
  description: "Governed billing support control room",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${barlow.variable} ${barlowCondensed.variable} ${ibmPlexMono.variable}`}
    >
      <body className="h-screen overflow-hidden antialiased">
        <TooltipProvider>
          <SidebarProvider className="h-full min-h-0 overflow-hidden">
            <Suspense fallback={null}>
              <AppSidebar />
            </Suspense>
            <SidebarInset className="min-h-0 overflow-hidden bg-bg">
              <Suspense fallback={null}>
                <SiteHeader />
              </Suspense>
              <div className="flex min-h-0 flex-1 flex-col overflow-hidden">{children}</div>
            </SidebarInset>
          </SidebarProvider>
        </TooltipProvider>
      </body>
    </html>
  );
}
