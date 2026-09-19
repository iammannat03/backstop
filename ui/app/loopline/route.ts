import { LOOPLINE_HTML } from "@/lib/loopline-html";

export const dynamic = "force-static";

export function GET() {
  return new Response(LOOPLINE_HTML, { headers: { "Content-Type": "text/html; charset=utf-8" } });
}
