import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useMemo, useRef, useState } from "react";

const ICON_BY_TYPE = {
  task_started: "🚀",
  planning_complete: "📋",
  step_started: "▶️",
  step_completed: "✅",
  step_failed: "❌",
  retry_triggered: "🔁",
  fallback_triggered: "🔄",
  reflection_started: "🤔",
  reflection_completed: "💡",
  checkpoint_saved: "💾",
  task_completed: "🎉",
  task_failed: "💥",
};

export default function LiveLogs({ events = [] }) {
  const [copied, setCopied] = useState(false);
  const containerRef = useRef(null);

  const lines = useMemo(() => {
    const safeEvents = Array.isArray(events) ? events : [];
    return safeEvents
      .map((event, index) => buildLogLine(event, index))
      .filter(Boolean);
  }, [events]);

  useEffect(() => {
    if (!containerRef.current) {
      return;
    }
    containerRef.current.scrollTop = containerRef.current.scrollHeight;
  }, [lines.length]);

  const handleCopyAll = async () => {
    const text = lines.join("\n");
    if (!text) {
      return;
    }

    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      const helper = document.createElement("textarea");
      helper.value = text;
      helper.setAttribute("readonly", "readonly");
      helper.style.position = "fixed";
      helper.style.opacity = "0";
      document.body.appendChild(helper);
      helper.select();
      document.execCommand("copy");
      document.body.removeChild(helper);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    }
  };

  return (
    <section className="rounded-2xl border border-white/10 bg-[var(--bg-card)]/70 p-4 sm:p-5">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-[var(--text-primary)]">Live Logs</h2>
          <p className="mt-1 text-xs text-[var(--text-secondary)]">Compact terminal stream of execution events.</p>
        </div>

        <button
          type="button"
          onClick={handleCopyAll}
          className="rounded-md border border-white/15 bg-white/5 px-3 py-1.5 text-xs text-[var(--text-secondary)] transition hover:border-[var(--accent-info)]/50 hover:text-[var(--text-primary)]"
        >
          {copied ? "Copied" : "Copy all"}
        </button>
      </div>

      <div
        ref={containerRef}
        className="max-h-72 overflow-y-auto rounded-xl border border-white/10 bg-[#060b17] p-3 font-mono text-[12px] leading-relaxed text-[#d8e6ff]"
      >
        {lines.length === 0 ? (
          <p className="text-[#7f8aa3]">No log events yet.</p>
        ) : (
          <AnimatePresence initial={false}>
            {lines.map((line, index) => (
              <motion.p
                key={`${line}-${index}`}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.16 }}
                className="whitespace-pre-wrap break-words"
              >
                {line}
              </motion.p>
            ))}
          </AnimatePresence>
        )}
      </div>
    </section>
  );
}

function buildLogLine(event, index) {
  const payload = isObject(event) ? event : {};
  const data = isObject(payload.data) ? payload.data : {};
  const details = isObject(payload.details) ? payload.details : {};
  const merged = { ...details, ...data };

  const type = String(payload.event_type || payload.type || "").toLowerCase();
  if (!type || type === "ping") {
    return "";
  }

  const timestamp = String(payload.timestamp || merged.timestamp || new Date().toISOString());
  const icon = ICON_BY_TYPE[type] || "•";
  const stepLabel = deriveStepLabel(payload.step_id || merged.step_id, payload.step_name || merged.step_name);

  const tokens = asNumber(payload.tokens_used ?? merged.tokens_used ?? merged.total_tokens);
  const model = asString(payload.model_used || merged.model_used || merged.model);
  const latencyMs = asNumber(payload.duration_ms ?? merged.latency_ms ?? merged.duration_ms);
  const latencySeconds = latencyMs ? (latencyMs / 1000).toFixed(1) : "";

  if (type === "step_completed") {
    const summary = [
      model || null,
      tokens !== null ? `${Math.round(tokens)} tokens` : null,
      latencySeconds ? `${latencySeconds}s` : null,
    ].filter(Boolean).join(", ");
    return `[${formatClockTime(timestamp)}] ${icon} ${stepLabel || `Step ${index + 1}`} completed${summary ? ` (${summary})` : ""}`;
  }

  if (type === "retry_triggered") {
    const attempt = asNumber(merged.attempt ?? merged.retry_count ?? merged.attempt_number) || 1;
    const reason = asString(payload.error || merged.reason || merged.error || "");
    return `[${formatClockTime(timestamp)}] ${icon} ${stepLabel || "Step"} retry ${attempt}/3${reason ? ` (${truncate(reason, 80)})` : ""}`;
  }

  if (type === "fallback_triggered") {
    const fromProvider = asString(payload.from_provider || merged.from_provider || merged.from || "unknown");
    const toProvider = asString(payload.to_provider || merged.to_provider || merged.to || "unknown");
    return `[${formatClockTime(timestamp)}] ${icon} Fallback: ${fromProvider} -> ${toProvider}`;
  }

  if (type === "step_failed") {
    const reason = asString(payload.error || merged.validator_reason || merged.reason || merged.error || "");
    return `[${formatClockTime(timestamp)}] ${icon} ${stepLabel || "Step"} failed${reason ? ` (${truncate(reason, 90)})` : ""}`;
  }

  if (type === "step_started") {
    return `[${formatClockTime(timestamp)}] ${icon} ${stepLabel || "Step"} started`;
  }

  if (type === "planning_complete") {
    const stepCount = asNumber(merged.step_count);
    return `[${formatClockTime(timestamp)}] ${icon} Planning complete${stepCount ? ` (${stepCount} steps)` : ""}`;
  }

  if (type === "task_started") {
    return `[${formatClockTime(timestamp)}] ${icon} Task started`;
  }

  if (type === "reflection_started") {
    return `[${formatClockTime(timestamp)}] ${icon} Reflection started for ${stepLabel || "step"}`;
  }

  if (type === "reflection_completed") {
    const action = asString(merged.action || "");
    return `[${formatClockTime(timestamp)}] ${icon} Reflection completed for ${stepLabel || "step"}${action ? ` (${action})` : ""}`;
  }

  if (type === "checkpoint_saved") {
    const node = asString(payload.node || merged.node || "");
    return `[${formatClockTime(timestamp)}] ${icon} Checkpoint saved${node ? ` (${node})` : ""}`;
  }

  if (type === "task_completed") {
    return `[${formatClockTime(timestamp)}] ${icon} Task completed`;
  }

  if (type === "task_failed") {
    const reason = asString(payload.error || merged.error || merged.reason || "");
    return `[${formatClockTime(timestamp)}] ${icon} Task failed${reason ? ` (${truncate(reason, 90)})` : ""}`;
  }

  return `[${formatClockTime(timestamp)}] ${icon} ${type.replace(/_/g, " ")}`;
}

function deriveStepLabel(stepId, stepName) {
  if (stepName) {
    return String(stepName);
  }
  const stepIdText = String(stepId || "");
  const match = stepIdText.match(/step_(\d+)/i);
  if (match) {
    return `Step ${match[1]}`;
  }
  if (stepIdText) {
    return stepIdText;
  }
  return "";
}

function formatClockTime(timestamp) {
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) {
    return "--:--:--";
  }
  return parsed.toLocaleTimeString("en-US", { hour12: false });
}

function truncate(value, maxLength = 80) {
  const text = String(value || "");
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength)}...`;
}

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function asString(value) {
  if (value === null || value === undefined) {
    return "";
  }
  return String(value);
}

function asNumber(value) {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return null;
  }
  return parsed;
}
