import { AnimatePresence, motion } from "framer-motion";
import { useMemo, useState } from "react";

const STATUS_META = {
  pending: {
    label: "Pending",
    classes: "border-slate-500/40 bg-slate-700/40 text-slate-200",
    icon: ClockIcon,
  },
  running: {
    label: "Running...",
    classes: "border-blue-400/40 bg-blue-500/20 text-blue-100 running-pulse",
    icon: SpinnerIcon,
  },
  success: {
    label: "Complete",
    classes: "border-emerald-400/40 bg-emerald-500/20 text-emerald-100",
    icon: CheckIcon,
  },
  failed: {
    label: "Failed",
    classes: "border-red-400/40 bg-red-500/20 text-red-100",
    icon: XIcon,
  },
  retrying: {
    label: "Retry",
    classes: "border-amber-400/40 bg-amber-500/20 text-amber-100",
    icon: RefreshIcon,
  },
  reflecting: {
    label: "Reflecting...",
    classes: "border-violet-400/40 bg-violet-500/20 text-violet-100",
    icon: BrainIcon,
  },
  skipped: {
    label: "Skipped",
    classes: "border-slate-400/30 bg-slate-600/30 text-slate-300",
    icon: SkipIcon,
  },
};

export default function StepCard({ step, result = null, index = 0, isActive = false, onClick }) {
  const [isExpanded, setIsExpanded] = useState(false);

  const status = useMemo(() => {
    const rawStatus = String(result?.status || step?.status || (isActive ? "running" : "pending")).toLowerCase();
    if (STATUS_META[rawStatus]) {
      return rawStatus;
    }
    return "pending";
  }, [isActive, result?.status, step?.status]);

  const statusMeta = STATUS_META[status];
  const Icon = statusMeta.icon;
  const toolUsed = result?.tool_used || step?.tool_needed || null;
  const output = String(result?.output || step?.output || "").trim();
  const outputPreview = output.length > 300 ? `${output.slice(0, 300)}...` : output;
  const tokensUsed = Number(result?.tokens_used || 0);
  const latencyMs = Number(result?.latency_ms || 0);
  const retryCount = Number(result?.retry_count || 0);

  const statusText = status === "retrying" ? `Retry ${Math.max(1, retryCount)}/3` : statusMeta.label;

  const durationText = latencyMs > 0 ? `${(latencyMs / 1000).toFixed(1)}s` : null;

  const handleCardClick = () => {
    setIsExpanded((prev) => !prev);
    if (typeof onClick === "function") {
      onClick(step);
    }
  };

  return (
    <motion.article
      className="glass group rounded-2xl border border-white/10 bg-[var(--bg-card)]/70 p-4 transition hover:border-[var(--accent-secondary)]/45"
      initial={{ opacity: 0, x: -24 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.35, delay: Math.min(index * 0.06, 0.36) }}
      layout
    >
      <button
        type="button"
        onClick={handleCardClick}
        className="w-full text-left"
      >
        <div className="flex items-start gap-3">
          <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-[var(--accent-info)]/50 bg-[var(--accent-primary)]/20 text-xs font-bold text-[var(--text-primary)]">
            {index + 1}
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="truncate text-sm font-semibold text-[var(--text-primary)]">{step?.name || `Step ${index + 1}`}</h3>

              <motion.div
                key={status}
                layout
                transition={{ duration: 0.2 }}
                className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold ${statusMeta.classes}`}
              >
                <Icon animated={status === "running" || status === "retrying"} />
                <span>{statusText}</span>
              </motion.div>
            </div>

            <p className="mt-1 text-xs text-[var(--text-secondary)] line-clamp-2">{step?.description || "No description provided."}</p>

            <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px]">
              {toolUsed ? (
                <span className="rounded-full border border-white/15 bg-white/5 px-2.5 py-1 text-[var(--text-secondary)]">
                  {toolUsed}
                </span>
              ) : null}

              {durationText ? (
                <span className="rounded-full border border-white/15 bg-white/5 px-2.5 py-1 text-[var(--text-secondary)]">
                  {durationText}
                </span>
              ) : null}

              {tokensUsed > 0 ? (
                <span className="rounded-full border border-white/15 bg-white/5 px-2.5 py-1 text-[var(--text-secondary)]">
                  {tokensUsed.toLocaleString()} tokens
                </span>
              ) : null}
            </div>
          </div>
        </div>
      </button>

      <AnimatePresence initial={false}>
        {isExpanded ? (
          <motion.div
            className="mt-3 rounded-xl border border-white/10 bg-[#0d1630]/80 p-3"
            initial={{ opacity: 0, height: 0, y: -8 }}
            animate={{ opacity: 1, height: "auto", y: 0 }}
            exit={{ opacity: 0, height: 0, y: -8 }}
            transition={{ duration: 0.22 }}
          >
            <p className="text-[11px] uppercase tracking-wide text-[var(--text-secondary)]">Output Preview</p>
            <p className="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-[var(--text-primary)]">
              {outputPreview || "No output available yet."}
            </p>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </motion.article>
  );
}

function ClockIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M12 6v6l4 2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <circle cx="12" cy="12" r="8" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}

function SpinnerIcon({ animated = false }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={`h-3.5 w-3.5 ${animated ? "animate-spin" : ""}`}
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <circle cx="12" cy="12" r="8" stroke="currentColor" strokeOpacity="0.35" strokeWidth="2" />
      <path d="M12 4a8 8 0 0 1 8 8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M6 12.5 10 16l8-8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function XIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="m7 7 10 10M17 7 7 17" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function RefreshIcon({ animated = false }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={`h-3.5 w-3.5 ${animated ? "animate-spin" : ""}`}
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <path d="M4 12a8 8 0 0 1 14-5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <path d="M18 3v4h-4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M20 12a8 8 0 0 1-14 5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <path d="M6 21v-4h4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function BrainIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M9 5a3 3 0 0 0-6 0v5a3 3 0 0 0 3 3h1v3a3 3 0 0 0 6 0v-2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <path d="M15 5a3 3 0 0 1 6 0v5a3 3 0 0 1-3 3h-1v3a3 3 0 0 1-6 0v-2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function SkipIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="m6 7 8 5-8 5V7ZM18 7v10" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
