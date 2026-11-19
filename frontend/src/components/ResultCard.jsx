import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const OUTCOME_STYLE = {
  complete: "border-emerald-400/45 bg-emerald-500/15 text-emerald-100",
  partial: "border-amber-400/45 bg-amber-500/15 text-amber-100",
  failed: "border-red-400/45 bg-red-500/15 text-red-100",
};

const OUTCOME_LABEL = {
  complete: "All steps passed review",
  partial: "Some steps did not pass review",
  failed: "Run failed",
};

// Models put <br> inside markdown table cells. Raw HTML from a model is never rendered (XSS),
// so line-break tags become a visible separator instead.
export function cleanModelMarkdown(text) {
  return String(text || "").replace(/<br\s*\/?>/gi, " · ");
}

function formatSeconds(ms) {
  const value = Number(ms || 0);
  if (!value) return "—";
  return value < 1000 ? `${value} ms` : `${(value / 1000).toFixed(1)} s`;
}

function formatCost(usd) {
  if (usd === null || usd === undefined) return "not priced";
  const value = Number(usd);
  return value < 0.01 ? `$${value.toFixed(4)}` : `$${value.toFixed(2)}`;
}

export default function ResultCard({ task }) {
  const [copied, setCopied] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const result = task?.final_output?.result;
  if (!result) return null;

  const summary = task?.final_output?.summary || {};
  const usage = summary.llm_usage || {};
  const outcome = String(summary.outcome || (task?.status === "failed" ? "failed" : "complete"));
  const stats = [
    { label: "Time", value: formatSeconds(summary.total_duration_ms) },
    { label: "Steps", value: `${summary.successful_steps ?? "—"}/${summary.total_steps ?? "—"} passed` },
    { label: "Model calls", value: usage.calls ?? summary.llm_calls ?? "—" },
    { label: "Tokens", value: Number(summary.total_tokens || 0).toLocaleString() },
    { label: "Cost", value: formatCost(usage.cost_usd ?? summary.estimated_cost_usd) },
  ];

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(String(result));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  return (
    <section aria-labelledby="result-heading" className="rounded-2xl border border-white/10 bg-[var(--bg-card)]/80 p-4 sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 id="result-heading" className="text-lg font-semibold text-[var(--text-primary)]">Answer</h2>
          <span className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${OUTCOME_STYLE[outcome] || OUTCOME_STYLE.complete}`}>
            {OUTCOME_LABEL[outcome] || outcome}
          </span>
          {task?.confidence_score ? (
            <span className="rounded-full border border-white/15 px-2.5 py-0.5 text-xs text-[var(--text-secondary)]">
              Confidence: {task.confidence_score}
            </span>
          ) : null}
        </div>
        <button
          type="button"
          onClick={copy}
          className="rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 text-xs text-[var(--text-secondary)] transition hover:border-sky-400/40 hover:text-[var(--text-primary)]"
        >
          {copied ? "Copied" : "Copy answer"}
        </button>
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-5">
        {stats.map((item) => (
          <div key={item.label} className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2">
            <dt className="text-[11px] uppercase tracking-wide text-[var(--text-secondary)]">{item.label}</dt>
            <dd className="mt-0.5 text-sm font-semibold text-[var(--text-primary)]">{item.value}</dd>
          </div>
        ))}
      </dl>

      <div
        tabIndex={0}
        role="region"
        aria-label="Answer text"
        className={`prose prose-sm prose-invert mt-4 max-w-none overflow-y-auto leading-relaxed ${expanded ? "" : "max-h-[28rem]"}`}
      >
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{cleanModelMarkdown(result)}</ReactMarkdown>
      </div>
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="mt-2 text-xs text-sky-200 underline-offset-2 hover:underline"
      >
        {expanded ? "Show less" : "Show full answer"}
      </button>
    </section>
  );
}
