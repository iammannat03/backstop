"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

const ACTION_TYPE_OPTIONS = [
  { value: "all", label: "All actions" },
  { value: "refund", label: "Full refund" },
  { value: "partial_refund", label: "Partial refund" },
  { value: "no_action", label: "No action" },
  { value: "escalate", label: "Escalate" },
  { value: "cancel_subscription", label: "Cancel subscription" },
  { value: "apply_account_credit", label: "Apply account credit" },
  { value: "flag_for_fraud_review", label: "Flag for fraud review" },
];

const STATUS_OPTIONS = [
  { value: "all", label: "All statuses" },
  { value: "needs_review", label: "Needs review" },
  { value: "in_progress", label: "In progress" },
  { value: "resolved", label: "Resolved" },
];

const inputClass =
  "border border-divider bg-bg px-2.5 py-1.5 text-[12.5px] font-data text-text focus-visible:outline-2 focus-visible:outline-accent";

export function TicketsFilters({
  initialQ,
  initialCustomerId,
  initialStatus,
  initialActionType,
  initialDateFrom,
  initialDateTo,
}: {
  initialQ: string;
  initialCustomerId: string;
  initialStatus: string;
  initialActionType: string;
  initialDateFrom: string;
  initialDateTo: string;
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [q, setQ] = useState(initialQ);
  const [customerId, setCustomerId] = useState(initialCustomerId);

  function pushParams(next: Record<string, string>) {
    const params = new URLSearchParams(searchParams.toString());
    for (const [key, value] of Object.entries(next)) {
      if (!value || value === "all") params.delete(key);
      else params.set(key, value);
    }
    // Any filter change invalidates the current page offset.
    params.delete("page");
    router.push(`/?${params.toString()}`);
  }

  return (
    <div className="flex flex-wrap items-center gap-2.5 border-b border-divider bg-neutral-100 px-[22px] py-3">
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") pushParams({ q });
        }}
        onBlur={() => pushParams({ q })}
        placeholder="Search customer email or ticket text…"
        className={`min-w-[200px] flex-1 ${inputClass}`}
      />
      <input
        value={customerId}
        onChange={(e) => setCustomerId(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") pushParams({ customerId });
        }}
        onBlur={() => pushParams({ customerId })}
        placeholder="Customer ID (cus_…)"
        className={`min-w-[160px] ${inputClass}`}
      />
      <select
        defaultValue={initialStatus}
        onChange={(e) => pushParams({ status: e.target.value })}
        className={inputClass}
      >
        {STATUS_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
      <select
        defaultValue={initialActionType}
        onChange={(e) => pushParams({ type: e.target.value })}
        className={inputClass}
      >
        {ACTION_TYPE_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
      <label className="flex items-center gap-1.5 font-data text-[11px] text-neutral-600">
        From
        <input
          type="date"
          defaultValue={initialDateFrom}
          onChange={(e) => pushParams({ dateFrom: e.target.value })}
          className={inputClass}
        />
      </label>
      <label className="flex items-center gap-1.5 font-data text-[11px] text-neutral-600">
        To
        <input
          type="date"
          defaultValue={initialDateTo}
          onChange={(e) => pushParams({ dateTo: e.target.value })}
          className={inputClass}
        />
      </label>
    </div>
  );
}
