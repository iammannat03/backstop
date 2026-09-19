"use client";

import { useRouter, useSearchParams } from "next/navigation";

export function Pagination({ page, pageSize, total }: { page: number; pageSize: number; total: number }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const start = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const end = Math.min(total, page * pageSize);

  function goTo(nextPage: number) {
    const params = new URLSearchParams(searchParams.toString());
    if (nextPage <= 1) params.delete("page");
    else params.set("page", String(nextPage));
    router.push(`/?${params.toString()}`);
  }

  return (
    <div className="flex flex-wrap items-center justify-between gap-2.5 border-t border-divider px-3.5 py-2.5 font-data text-[11.5px] text-neutral-600">
      <span>
        Showing {start}–{end} of {total}
      </span>
      <div className="flex items-center gap-2.5">
        <button disabled={page <= 1} onClick={() => goTo(page - 1)} className="btn btn-ghost text-[11px]">
          ← Prev
        </button>
        <span>
          Page {page} of {pageCount}
        </span>
        <button disabled={page >= pageCount} onClick={() => goTo(page + 1)} className="btn btn-ghost text-[11px]">
          Next →
        </button>
      </div>
    </div>
  );
}
