import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useMemo, useRef, useState } from "react";

const FILTERS = [
  { id: "all", label: "All" },
  { id: "steps", label: "Steps" },
  { id: "errors", label: "Errors" },
  { id: "reliability", label: "Reliability" },
];

const EVENT_META = {
  task_started: {
    icon: "🚀",
    border: "border-l-cyan-400",
    category: "steps",
  },
  planning_complete: {
    icon: "📋",
    border: "border-l-sky-400",
    category: "steps",
  },
  step_started: {
    icon: "▶️",
    border: "border-l-blue-400",
    category: "steps",
  },
  step_completed: {
    icon: "✅",
    border: "border-l-emerald-400",
    category: "steps",
  },
  step_failed: {
    icon: "❌",
    border: "border-l-red-400",
    category: "errors",
  },
  retry_triggered: {
    icon: "🔁",
    border: "border-l-amber-400",
    category: "reliability",
  },
  fallback_triggered: {
    icon: "🔄",
    border: "border-l-orange-400",
    category: "reliability",
  },
  reflection_started: {
    icon: "🤔",
    border: "border-l-violet-400",
    category: "reliability",
  },
  reflection_completed: {
    icon: "💡",
    border: "border-l-fuchsia-400",
    category: "reliability",
  },
  checkpoint_saved: {
    icon: "💾",
    border: "border-l-slate-400",
    category: "reliability",
  },
  task_completed: {
    icon: "🎉",
    border: "border-l-emerald-500",
    category: "steps",
  },
  task_failed: {
    icon: "💥",
    border: "border-l-rose-500",
    category: "errors",
  },
};

export default function TraceTimeline({ trace = [], onEventClick }) {
  const [filter, setFilter] = useState("all");
  const [stickToBottom, setStickToBottom] = useState(true);
  const [expanded, setExpanded] = useState({});
  const [nowMs, setNowMs] = useState(Date.now());
  const containerRef = useRef(null);

  useEffect(() => {
    const timerId = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(timerId);
  }, []);

  const normalizedEvents = useMemo(() => {
    const items = Array.isArray(trace) ? trace : [];
    return items
      .map((event, index) => normalizeEvent(event, index))
      .filter((event) => event.type && event.type !== "ping");
  }, [trace]);

  const filteredEvents = useMemo(
    () => normalizedEvents.filter((event) => matchesFilter(event, filter)),
    [filter, normalizedEvents],
  );

  useEffect(() => {
    if (!stickToBottom || !containerRef.current) {
      return;
    }
    containerRef.current.scrollTop = containerRef.current.scrollHeight;
  }, [filteredEvents.length, stickToBottom]);

  const toggleExpanded = (id) => {
    setExpanded((prev) => ({
      ...prev,
      [id]: !prev[id],
    }));
  };

  return (
    <section className="rounded-2xl border border-white/10 bg-[var(--bg-card)]/70 p-4 sm:p-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold text-[var(--text-primary)]">Trace Timeline</h2>
          <p className="mt-1 text-xs text-[var(--text-secondary)]">Execution events with reliability and recovery signals.</p>
        </div>

        <button
          type="button"
          onClick={() => setStickToBottom((prev) => !prev)}
          className={`rounded-full border px-3 py-1.5 text-xs transition ${stickToBottom
            ? "border-[var(--accent-info)]/50 bg-[var(--accent-info)]/15 text-[var(--text-primary)]"
            : "border-white/15 bg-white/5 text-[var(--text-secondary)]"}`}
        >
          Stick to bottom: {stickToBottom ? "On" : "Off"}
        </button>
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        {FILTERS.map((option) => (
          <button
            key={option.id}
            type="button"
            onClick={() => setFilter(option.id)}
            className={`rounded-full border px-3 py-1 text-xs font-medium transition ${filter === option.id
              ? "border-[var(--accent-secondary)]/60 bg-[var(--accent-secondary)]/20 text-[var(--text-primary)]"
              : "border-white/15 bg-white/5 text-[var(--text-secondary)] hover:border-[var(--accent-info)]/40 hover:text-[var(--text-primary)]"}`}
          >
            {option.label}
          </button>
        ))}
      </div>

      <div ref={containerRef} className="mt-4 max-h-[460px] overflow-y-auto pr-1">
        {filteredEvents.length === 0 ? (
          <div className="rounded-xl border border-white/10 bg-[#0c142a]/80 p-4 text-sm text-[var(--text-secondary)]">
            No trace events for this filter yet.
          </div>
        ) : (
          <div className="space-y-2">
            <AnimatePresence initial={false}>
              {filteredEvents.map((event, index) => {
                const meta = EVENT_META[event.type] || {
                  icon: "•",
                  border: "border-l-slate-500",
                  category: "all",
                };
                const isOpen = Boolean(expanded[event.id]);
                const expandable = hasExpandableDetails(event);

                return (
                  <motion.article
                    key={event.id}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -6 }}
                    transition={{ duration: 0.2, delay: Math.min(index * 0.025, 0.25) }}
                    className={`rounded-xl border border-white/10 border-l-4 bg-[#0b1328]/85 p-3 ${meta.border}`}
                  >
                    <button
                      type="button"
                      onClick={() => {
                        if (typeof onEventClick === "function") {
                          onEventClick(event);
                        }
                        if (expandable) {
                          toggleExpanded(event.id);
                        }
                      }}
                      className="w-full text-left"
                    >
                      <div className="flex items-start gap-3">
                        <span className="mt-0.5 text-base leading-none" aria-hidden="true">{meta.icon}</span>

                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-start justify-between gap-2">
                            <p className="text-sm font-medium text-[var(--text-primary)]">{event.description}</p>
                            <span className="shrink-0 text-[11px] text-[var(--text-secondary)]">
                              {formatRelativeTime(event.timestamp, nowMs)}
                            </span>
                          </div>

                          <p className="mt-1 text-[11px] text-[var(--text-secondary)]">
                            {formatAbsoluteTime(event.timestamp)}
                          </p>
                        </div>

                        {expandable ? (
                          <span className="mt-0.5 text-xs text-[var(--text-secondary)]">{isOpen ? "▴" : "▾"}</span>
                        ) : null}
                      </div>
                    </button>

                    <AnimatePresence initial={false}>
                      {expandable && isOpen ? (
                        <motion.div
                          initial={{ opacity: 0, height: 0, y: -8 }}
                          animate={{ opacity: 1, height: "auto", y: 0 }}
                          exit={{ opacity: 0, height: 0, y: -8 }}
                          transition={{ duration: 0.2 }}
                          className="mt-3 rounded-lg border border-white/10 bg-[#111b37]/85 p-3"
                        >
                          <EventDetails event={event} />
                        </motion.div>
                      ) : null}
                    </AnimatePresence>
                  </motion.article>
                );
              })}
            </AnimatePresence>
          </div>
        )}
      </div>
    </section>
  );
}

function EventDetails({ event }) {
  if (event.type === "retry_triggered") {
    return (
      <div className="grid gap-2 text-xs text-[var(--text-secondary)]">
        <DetailLine label="Attempt" value={event.retryAttempt || "-"} />
        <DetailLine label="Error" value={event.error || "No reason provided."} />
        <DetailLine label="Backoff" value={event.backoffDelayMs ? `${event.backoffDelayMs} ms` : "Not provided"} />
      </div>
    );
  }

  if (event.type === "fallback_triggered") {
    return (
      <div className="grid gap-2 text-xs text-[var(--text-secondary)]">
        <DetailLine label="From" value={event.fromProvider || "unknown"} />
        <DetailLine label="To" value={event.toProvider || "unknown"} />
      </div>
    );
  }

  if (event.type === "reflection_started" || event.type === "reflection_completed") {
    return (
      <div className="grid gap-2 text-xs text-[var(--text-secondary)]">
        <DetailLine label="Action" value={event.reflectionAction || "Not provided"} />
        <DetailLine label="Reasoning" value={event.reflectionReason || event.error || "No reasoning provided."} />
      </div>
    );
  }

  if (event.type === "step_started" || event.type === "step_completed" || event.type === "step_failed") {
    return (
      <div className="space-y-2 text-xs text-[var(--text-secondary)]">
        <DetailLine label="Prompt" value={truncateText(event.prompt, 220) || "Not captured"} />
        <DetailLine label="Response" value={truncateText(event.response, 220) || "Not captured"} />
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <StatChip label="Tokens" value={event.tokens ?? "-"} />
          <StatChip label="Model" value={event.model || "-"} />
          <StatChip label="Latency" value={event.latencyMs ? `${event.latencyMs} ms` : "-"} />
          <StatChip label="Step" value={event.stepLabel || "-"} />
        </div>
      </div>
    );
  }

  return (
    <pre className="whitespace-pre-wrap break-words text-xs text-[var(--text-secondary)]">
      {JSON.stringify(event.rawPayload, null, 2)}
    </pre>
  );
}

function DetailLine({ label, value }) {
  return (
    <p>
      <span className="font-medium text-[var(--text-primary)]">{label}:</span> {value}
    </p>
  );
}

function StatChip({ label, value }) {
  return (
    <div className="rounded border border-white/10 bg-white/5 px-2 py-1">
      <p className="text-[10px] uppercase tracking-wide">{label}</p>
      <p className="truncate text-[11px] font-medium text-[var(--text-primary)]">{String(value)}</p>
    </div>
  );
}

function normalizeEvent(entry, index) {
  const payload = isObject(entry) ? entry : {};
  const data = isObject(payload.data) ? payload.data : {};
  const details = isObject(payload.details) ? payload.details : {};
  const merged = { ...details, ...data };

  const type = String(payload.event_type || payload.type || "unknown").toLowerCase();
  const timestamp = String(payload.timestamp || merged.timestamp || new Date().toISOString());
  const stepId = asString(payload.step_id || merged.step_id);
  const stepName = asString(payload.step_name || merged.step_name);
  const stepLabel = deriveStepLabel(stepId, stepName);

  const tokens = asNumber(payload.tokens_used ?? merged.tokens_used ?? merged.total_tokens);
  const model = asString(payload.model_used || merged.model_used || merged.model);
  const latencyMs = asNumber(payload.duration_ms ?? merged.latency_ms ?? merged.duration_ms);

  const retryAttempt = asNumber(merged.attempt ?? merged.retry_count ?? merged.attempt_number);
  const backoffDelayMs = asNumber(merged.backoff_delay_ms ?? merged.backoff_ms ?? merged.delay_ms);

  const fromProvider = asString(payload.from_provider || merged.from_provider || merged.from);
  const toProvider = asString(payload.to_provider || merged.to_provider || merged.to);

  const reflectionAction = asString(merged.action);
  const reflectionReason = asString(merged.reasoning || merged.reason);

  const prompt = asString(merged.prompt || merged.input_prompt || merged.user_prompt || merged.input);
  const response = asString(merged.response || merged.output || merged.output_preview || merged.result);
  const error = asString(payload.error || merged.error || merged.validator_reason || merged.reason);

  const description = describeEvent({
    type,
    stepLabel,
    retryAttempt,
    fromProvider,
    toProvider,
    reflectionAction,
    error,
    merged,
  });

  return {
    id: `${timestamp}-${type}-${index}`,
    type,
    timestamp,
    stepId,
    stepName,
    stepLabel,
    description,
    prompt,
    response,
    tokens,
    model,
    latencyMs,
    retryAttempt,
    backoffDelayMs,
    fromProvider,
    toProvider,
    reflectionAction,
    reflectionReason,
    error,
    rawPayload: payload,
  };
}

function describeEvent({ type, stepLabel, retryAttempt, fromProvider, toProvider, reflectionAction, error, merged }) {
  if (type === "task_started") {
    return "Task execution started.";
  }
  if (type === "planning_complete") {
    const stepCount = asNumber(merged.step_count);
    return stepCount ? `Planning complete with ${stepCount} steps.` : "Planning complete.";
  }
  if (type === "step_started") {
    return `${stepLabel || "Step"} started.`;
  }
  if (type === "step_completed") {
    return `${stepLabel || "Step"} completed.`;
  }
  if (type === "step_failed") {
    return `${stepLabel || "Step"} failed${error ? `: ${truncateText(error, 110)}` : "."}`;
  }
  if (type === "retry_triggered") {
    const attemptText = retryAttempt ? `attempt ${retryAttempt}` : "retry";
    return `${stepLabel || "Step"} retry triggered (${attemptText}).`;
  }
  if (type === "fallback_triggered") {
    if (fromProvider || toProvider) {
      return `Fallback triggered: ${fromProvider || "unknown"} -> ${toProvider || "unknown"}.`;
    }
    return "Fallback provider switch triggered.";
  }
  if (type === "reflection_started") {
    return `${stepLabel || "Step"} reflection started.`;
  }
  if (type === "reflection_completed") {
    return `${stepLabel || "Step"} reflection completed${reflectionAction ? ` (${reflectionAction})` : "."}`;
  }
  if (type === "checkpoint_saved") {
    return `Checkpoint saved${merged.node ? ` at ${merged.node}` : ""}.`;
  }
  if (type === "task_completed") {
    return "Task completed.";
  }
  if (type === "task_failed") {
    return `Task failed${error ? `: ${truncateText(error, 110)}` : "."}`;
  }
  return `${type.replace(/_/g, " ")} event.`;
}

function matchesFilter(event, filter) {
  if (filter === "all") {
    return true;
  }

  const meta = EVENT_META[event.type];
  const category = meta?.category || "all";

  if (filter === "errors") {
    return category === "errors";
  }
  if (filter === "steps") {
    return category === "steps";
  }
  if (filter === "reliability") {
    return category === "reliability";
  }
  return true;
}

function hasExpandableDetails(event) {
  if (
    event.type === "step_started" ||
    event.type === "step_completed" ||
    event.type === "step_failed" ||
    event.type === "retry_triggered" ||
    event.type === "fallback_triggered" ||
    event.type === "reflection_started" ||
    event.type === "reflection_completed"
  ) {
    return true;
  }

  return Boolean(event.rawPayload?.details || event.rawPayload?.data);
}

function deriveStepLabel(stepId, stepName) {
  if (stepName) {
    return stepName;
  }
  const match = String(stepId || "").match(/step_(\d+)/i);
  if (match) {
    return `Step ${match[1]}`;
  }
  return stepId || "";
}

function formatAbsoluteTime(timestamp) {
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) {
    return "Unknown time";
  }
  return parsed.toLocaleTimeString();
}

function formatRelativeTime(timestamp, nowMs) {
  const parsed = new Date(timestamp).getTime();
  if (Number.isNaN(parsed)) {
    return "unknown";
  }
  const delta = Math.max(0, Math.floor((nowMs - parsed) / 1000));
  if (delta < 60) {
    return `${delta}s ago`;
  }
  const minutes = Math.floor(delta / 60);
  if (minutes < 60) {
    return `${minutes}m ago`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours}h ago`;
  }
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function truncateText(value, maxLength = 240) {
  if (!value) {
    return "";
  }
  const text = String(value);
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
