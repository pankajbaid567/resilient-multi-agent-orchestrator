import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  Bar,
  BarChart,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const TAB_PROMPT = "prompt";
const TAB_RESPONSE = "response";
const TAB_VALIDATION = "validation";
const TAB_ERRORS = "errors";
const TAB_TRACE = "trace";

const SCORE_DIMENSIONS = ["relevance", "completeness", "consistency", "plausibility"];

export default function StepDetailModal({
  isOpen = false,
  step = null,
  stepTrace = [],
  errors = [],
  onClose,
}) {
  const [activeTab, setActiveTab] = useState(TAB_PROMPT);
  const [expandedTraceIds, setExpandedTraceIds] = useState(() => new Set());
  const [copyState, setCopyState] = useState({ prompt: false, response: false });

  const details = useMemo(
    () => deriveStepDetails({ step, stepTrace, errors }),
    [errors, step, stepTrace],
  );

  const tabs = useMemo(() => {
    const baseTabs = [
      { id: TAB_PROMPT, label: "Prompt" },
      { id: TAB_RESPONSE, label: "Response" },
      { id: TAB_VALIDATION, label: "Validation" },
    ];

    if (details.errors.length > 0) {
      baseTabs.push({ id: TAB_ERRORS, label: `Errors (${details.errors.length})` });
    }

    baseTabs.push({ id: TAB_TRACE, label: `Trace (${details.trace.length})` });
    return baseTabs;
  }, [details.errors.length, details.trace.length]);

  useEffect(() => {
    if (!isOpen) {
      return undefined;
    }

    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        if (typeof onClose === "function") {
          onClose();
        }
      }
    };

    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", handleKeyDown);

    return () => {
      document.body.style.overflow = "";
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [isOpen, onClose]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    setActiveTab(TAB_PROMPT);
    setExpandedTraceIds(new Set());
    setCopyState({ prompt: false, response: false });
  }, [isOpen, details.stepId]);

  const handleCopy = async (type, content) => {
    try {
      await navigator.clipboard.writeText(String(content || ""));
      setCopyState((previous) => ({ ...previous, [type]: true }));
      window.setTimeout(() => {
        setCopyState((previous) => ({ ...previous, [type]: false }));
      }, 1400);
    } catch {
      setCopyState((previous) => ({ ...previous, [type]: false }));
    }
  };

  const toggleTraceExpansion = (traceId) => {
    setExpandedTraceIds((previous) => {
      const next = new Set(previous);
      if (next.has(traceId)) {
        next.delete(traceId);
      } else {
        next.add(traceId);
      }
      return next;
    });
  };

  const verdictMeta = getVerdictMeta(details.validation.verdict);

  return (
    <AnimatePresence>
      {isOpen ? (
        <motion.div
          className="fixed inset-0 z-[80] flex items-center justify-center bg-[#040812]/80 p-3 backdrop-blur-sm sm:p-6"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={() => {
            if (typeof onClose === "function") {
              onClose();
            }
          }}
        >
          <motion.div
            initial={{ opacity: 0, y: 42 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 42 }}
            transition={{ duration: 0.24 }}
            onClick={(event) => event.stopPropagation()}
            className="flex h-[80vh] w-full max-w-[1220px] flex-col overflow-hidden rounded-2xl border border-white/10 bg-[#081328] shadow-2xl"
          >
            <header className="flex flex-wrap items-start justify-between gap-3 border-b border-white/10 px-4 py-4 sm:px-6">
              <div>
                <h3 className="text-lg font-semibold text-[var(--text-primary)]">{details.stepName}</h3>
                <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-[var(--text-secondary)]">
                  <span className="rounded-full border border-white/15 bg-white/5 px-2.5 py-1">
                    {details.stepId || "step"}
                  </span>
                  <span className={`rounded-full border px-2.5 py-1 ${verdictMeta.badgeClass}`}>
                    {verdictMeta.icon} {verdictMeta.label}
                  </span>
                  {details.agent ? (
                    <span className={`rounded-full border px-2.5 py-1 ${details.agent.badgeClass}`}>
                      {details.agent.icon} {details.agent.label}
                    </span>
                  ) : null}
                  {details.modelLabel ? (
                    <span className="rounded-full border border-violet-400/35 bg-violet-500/15 px-2.5 py-1 text-violet-100">
                      {details.modelLabel}
                    </span>
                  ) : null}
                  <span className="rounded-full border border-white/15 bg-white/5 px-2.5 py-1">
                    {formatDuration(details.durationMs)}
                  </span>
                </div>
              </div>

              <button
                type="button"
                onClick={() => {
                  if (typeof onClose === "function") {
                    onClose();
                  }
                }}
                className="rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 text-sm text-[var(--text-secondary)] transition hover:border-white/30 hover:text-[var(--text-primary)]"
              >
                ✕
              </button>
            </header>

            <div className="border-b border-white/10 px-4 sm:px-6">
              <div className="flex flex-wrap gap-2 py-3">
                {tabs.map((tab) => (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => setActiveTab(tab.id)}
                    className={`rounded-full border px-3 py-1 text-xs font-medium transition ${activeTab === tab.id
                      ? "border-sky-400/50 bg-sky-500/20 text-sky-100"
                      : "border-white/15 bg-white/5 text-[var(--text-secondary)] hover:text-[var(--text-primary)]"}`}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="flex-1 overflow-y-auto px-4 py-4 sm:px-6">
              {activeTab === TAB_PROMPT ? (
                <section>
                  <div className="mb-3 flex items-center justify-between">
                    <p className="text-xs text-[var(--text-secondary)]">
                      Input tokens: <span className="font-semibold text-[var(--text-primary)]">{formatNumber(details.tokensIn)}</span>
                    </p>
                    <button
                      type="button"
                      onClick={() => handleCopy("prompt", details.prompt)}
                      className="rounded-md border border-white/15 bg-white/5 px-3 py-1 text-xs text-[var(--text-secondary)] transition hover:text-[var(--text-primary)]"
                    >
                      {copyState.prompt ? "Copied" : "Copy"}
                    </button>
                  </div>
                  <pre className="max-h-[52vh] overflow-auto rounded-xl border border-cyan-400/20 bg-[#071225] p-4 text-xs leading-relaxed text-cyan-100">
                    {details.prompt || "Prompt not captured for this step."}
                  </pre>
                </section>
              ) : null}

              {activeTab === TAB_RESPONSE ? (
                <section>
                  <div className="mb-3 flex items-center justify-between">
                    <p className="text-xs text-[var(--text-secondary)]">
                      Output tokens: <span className="font-semibold text-[var(--text-primary)]">{formatNumber(details.tokensOut)}</span>
                    </p>
                    <button
                      type="button"
                      onClick={() => handleCopy("response", details.response)}
                      className="rounded-md border border-white/15 bg-white/5 px-3 py-1 text-xs text-[var(--text-secondary)] transition hover:text-[var(--text-primary)]"
                    >
                      {copyState.response ? "Copied" : "Copy"}
                    </button>
                  </div>

                  <div className="max-h-[52vh] overflow-auto rounded-xl border border-white/10 bg-[#0b1733] p-4 text-sm leading-relaxed text-slate-100">
                    {details.response ? (
                      <ReactMarkdown
                        components={{
                          h1: ({ children }) => <h1 className="mb-2 mt-3 text-lg font-semibold">{children}</h1>,
                          h2: ({ children }) => <h2 className="mb-2 mt-3 text-base font-semibold">{children}</h2>,
                          h3: ({ children }) => <h3 className="mb-1 mt-3 text-sm font-semibold">{children}</h3>,
                          p: ({ children }) => <p className="mb-2">{children}</p>,
                          li: ({ children }) => <li className="ml-4 list-disc">{children}</li>,
                          code: ({ children }) => (
                            <code className="rounded bg-white/10 px-1 py-0.5 text-xs text-cyan-100">{children}</code>
                          ),
                          pre: ({ children }) => (
                            <pre className="my-2 overflow-auto rounded-lg border border-white/10 bg-[#071225] p-3 text-xs">{children}</pre>
                          ),
                        }}
                      >
                        {details.response}
                      </ReactMarkdown>
                    ) : (
                      <p className="text-sm text-[var(--text-secondary)]">Response not available.</p>
                    )}
                  </div>
                </section>
              ) : null}

              {activeTab === TAB_VALIDATION ? (
                <section className="space-y-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold text-[var(--text-primary)]">Quality Scores (0-10)</p>
                      <p className="text-xs text-[var(--text-secondary)]">Relevance, completeness, consistency, plausibility</p>
                    </div>
                    <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${verdictMeta.badgeClass}`}>
                      {verdictMeta.icon} {verdictMeta.label}
                    </span>
                  </div>

                  <div className="h-[250px] rounded-xl border border-white/10 bg-[#0b1733] p-3">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart
                        layout="vertical"
                        data={details.validation.chartData}
                        margin={{ left: 20, right: 20, top: 8, bottom: 8 }}
                      >
                        <XAxis
                          type="number"
                          domain={[0, 10]}
                          tick={{ fill: "#cbd5e1", fontSize: 11 }}
                          axisLine={{ stroke: "rgba(148,163,184,0.42)" }}
                          tickLine={{ stroke: "rgba(148,163,184,0.42)" }}
                        />
                        <YAxis
                          type="category"
                          dataKey="name"
                          tick={{ fill: "#e2e8f0", fontSize: 11 }}
                          width={110}
                          axisLine={{ stroke: "rgba(148,163,184,0.42)" }}
                          tickLine={{ stroke: "rgba(148,163,184,0.42)" }}
                        />
                        <Tooltip content={<ValidationTooltip />} />
                        <Bar dataKey="score" radius={[6, 6, 6, 6]}>
                          {details.validation.chartData.map((item) => (
                            <Cell key={item.key} fill={scoreColor(item.score)} />
                          ))}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                  </div>

                  <div className="rounded-xl border border-white/10 bg-[#09142b] p-3 text-sm text-[var(--text-secondary)]">
                    <p className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">Validator Reason</p>
                    <p className="mt-1 text-sm text-[var(--text-primary)]">{details.validation.reason || "No reason was captured."}</p>
                  </div>
                </section>
              ) : null}

              {activeTab === TAB_ERRORS ? (
                <section className="space-y-2">
                  {details.errors.map((item, index) => (
                    <article
                      key={`${item.timestamp}-${item.errorType}-${index}`}
                      className={`rounded-xl border p-3 text-xs ${errorSeverityClass(item.errorType)}`}
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="font-semibold">{item.errorType}</p>
                        <p className="text-[11px] opacity-90">{formatTimestamp(item.timestamp)}</p>
                      </div>
                      <p className="mt-1 text-sm text-[var(--text-primary)]">{item.message}</p>
                      <p className="mt-1 text-[11px] text-[var(--text-secondary)]">Attempt #{item.attempt}</p>
                    </article>
                  ))}
                </section>
              ) : null}

              {activeTab === TAB_TRACE ? (
                <section className="space-y-2">
                  {details.trace.length === 0 ? (
                    <div className="rounded-xl border border-white/10 bg-[#09142b] p-4 text-sm text-[var(--text-secondary)]">
                      No trace events available for this step.
                    </div>
                  ) : (
                    details.trace.map((event, index) => {
                      const expanded = expandedTraceIds.has(event.id);
                      return (
                        <article key={event.id} className="rounded-xl border border-white/10 bg-[#0a162f]">
                          <button
                            type="button"
                            onClick={() => toggleTraceExpansion(event.id)}
                            className="flex w-full items-center justify-between px-3 py-2 text-left"
                          >
                            <div>
                              <p className="text-sm font-medium text-[var(--text-primary)]">{event.type}</p>
                              <p className="text-[11px] text-[var(--text-secondary)]">
                                {formatTimestamp(event.timestamp)}
                                {event.model ? ` · ${event.model}` : ""}
                                {event.durationMs ? ` · ${event.durationMs}ms` : ""}
                              </p>
                            </div>
                            <span className="text-xs text-[var(--text-secondary)]">{expanded ? "▴" : "▾"}</span>
                          </button>

                          <AnimatePresence initial={false}>
                            {expanded ? (
                              <motion.pre
                                initial={{ opacity: 0, height: 0 }}
                                animate={{ opacity: 1, height: "auto" }}
                                exit={{ opacity: 0, height: 0 }}
                                transition={{ duration: 0.18 }}
                                className="overflow-auto border-t border-white/10 px-3 py-2 text-[11px] text-slate-300"
                              >
                                {JSON.stringify(event.raw, null, 2)}
                              </motion.pre>
                            ) : null}
                          </AnimatePresence>
                        </article>
                      );
                    })
                  )}
                </section>
              ) : null}
            </div>
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}

function ValidationTooltip({ active, payload }) {
  if (!active || !payload || !payload.length) {
    return null;
  }

  const item = payload[0]?.payload;
  if (!item) {
    return null;
  }

  return (
    <div className="rounded-md border border-white/15 bg-[#050b19] px-2.5 py-1.5 text-xs text-slate-100">
      {item.name}: {item.score}/10
    </div>
  );
}

function deriveStepDetails({ step, stepTrace, errors }) {
  const safeStep = step && typeof step === "object" ? step : {};
  const traceEvents = (Array.isArray(stepTrace) ? stepTrace : [])
    .map((event, index) => normalizeTraceEvent(event, index))
    .sort((a, b) => toTimestamp(a.timestamp) - toTimestamp(b.timestamp));

  const structuredErrors = [
    ...normalizeErrors(Array.isArray(errors) ? errors : []),
    ...normalizeErrors(extractErrorsFromTrace(traceEvents)),
  ].sort((a, b) => toTimestamp(a.timestamp) - toTimestamp(b.timestamp));

  const prompt = String(
    safeStep.prompt
      || safeStep.full_prompt
      || safeStep.input_prompt
      || pickFirstTraceValue(traceEvents, ["prompt", "prompt_preview", "details.prompt", "details.prompt_preview"])
      || "",
  );

  const response = String(
    safeStep.response
      || safeStep.output
      || safeStep.full_response
      || pickLastTraceValue(traceEvents, ["response", "response_preview", "details.response", "details.response_preview", "details.output"])
      || "",
  );

  const tokensIn = toNumber(
    safeStep.tokens_in
      || safeStep.prompt_tokens
      || sumBy(traceEvents, (event) => event.tokensIn),
  );

  const tokensOut = toNumber(
    safeStep.tokens_out
      || safeStep.completion_tokens
      || sumBy(traceEvents, (event) => event.tokensOut),
  );

  const stepId = String(
    safeStep.step_id
      || safeStep.id
      || traceEvents[0]?.stepId
      || "",
  );

  const stepName = String(
    safeStep.name
      || safeStep.step_name
      || traceEvents[0]?.stepName
      || stepId
      || "Step",
  );

  const durationMs = toNumber(
    safeStep.latency_ms
      || safeStep.duration_ms
      || sumBy(traceEvents, (event) => event.durationMs),
  );

  const modelLabel = normalizeModelLabel(
    safeStep.model_used
      || pickLastTraceValue(traceEvents, ["model", "details.model_used", "details.model"])
      || "",
  );

  const agentName = String(
    safeStep.agent_name
      || pickLastTraceValue(traceEvents, ["agentName", "details.agent_name"]) || "",
  ).trim();

  const validationRaw = resolveValidation({ safeStep, traceEvents, structuredErrors });

  return {
    stepId,
    stepName,
    prompt,
    response,
    tokensIn,
    tokensOut,
    durationMs,
    modelLabel,
    agent: resolveAgent(agentName),
    validation: validationRaw,
    errors: structuredErrors,
    trace: traceEvents,
  };
}

function resolveValidation({ safeStep, traceEvents, structuredErrors }) {
  const directScores = safeStep.scores && typeof safeStep.scores === "object" ? safeStep.scores : null;

  const fromErrorLog = [...structuredErrors]
    .reverse()
    .find((entry) => entry.scores && typeof entry.scores === "object");

  const fromTrace = [...traceEvents]
    .reverse()
    .find((event) => event.scores && typeof event.scores === "object");

  const scoresSource = directScores || fromErrorLog?.scores || fromTrace?.scores || {};

  const chartData = SCORE_DIMENSIONS.map((dimension) => ({
    key: dimension,
    name: capitalize(dimension),
    score: clamp(toNumber(scoresSource?.[dimension]), 0, 10),
  }));

  const verdict = String(
    safeStep.validation
      || fromTrace?.validatorVerdict
      || fromErrorLog?.verdict
      || "pass",
  ).toLowerCase();

  const reason = String(
    safeStep.validation_reason
      || fromTrace?.validatorReason
      || fromErrorLog?.reason
      || "",
  );

  return {
    verdict,
    reason,
    chartData,
  };
}

function normalizeTraceEvent(event, index) {
  const raw = event && typeof event === "object" ? event : {};
  const details = raw.details && typeof raw.details === "object" ? raw.details : {};
  const data = raw.data && typeof raw.data === "object" ? raw.data : {};

  return {
    id: `${String(raw.timestamp || "")}::${String(raw.event_type || "event")}::${index}`,
    type: String(raw.event_type || "event"),
    timestamp: String(raw.timestamp || new Date().toISOString()),
    stepId: String(raw.step_id || data.step_id || ""),
    stepName: String(raw.step_name || data.step_name || ""),
    model: normalizeModelLabel(raw.model_used || details.model_used || details.model || data.model || ""),
    agentName: String(raw.agent_name || details.agent_name || data.agent_name || ""),
    durationMs: toNumber(raw.duration_ms || details.duration_ms || data.duration_ms),
    tokensIn: toNumber(raw.tokens_in || details.tokens_in || data.tokens_in),
    tokensOut: toNumber(raw.tokens_out || details.tokens_out || data.tokens_out),
    validatorVerdict: String(details.validator_verdict || data.validator_verdict || "").toLowerCase(),
    validatorReason: String(details.validator_reason || data.validator_reason || ""),
    scores: details.scores || data.scores || null,
    raw: raw,
  };
}

function extractErrorsFromTrace(traceEvents) {
  return traceEvents
    .filter((event) => event.type === "step_failed" || event.type === "task_failed")
    .map((event) => ({
      timestamp: event.timestamp,
      error_type: event.raw?.error_type || "EXECUTION_FAILURE",
      error_message: event.raw?.error || event.raw?.details?.reason || "Step failed",
      attempt_number: event.raw?.details?.attempt || event.raw?.details?.retry_count || 1,
    }));
}

function normalizeErrors(errors) {
  return errors
    .filter((item) => item && typeof item === "object")
    .map((item) => ({
      timestamp: String(item.timestamp || new Date().toISOString()),
      errorType: String(item.error_type || item.type || "ERROR"),
      message: String(item.error_message || item.message || item.error || "Unknown error"),
      attempt: toNumber(item.attempt_number || item.attempt || 1),
      verdict: String(item.validator_verdict || "").toLowerCase(),
      reason: String(item.reason || item.validator_reason || ""),
      scores: item.scores && typeof item.scores === "object" ? item.scores : null,
    }));
}

function pickFirstTraceValue(traceEvents, pathCandidates) {
  for (const event of traceEvents) {
    for (const candidate of pathCandidates) {
      const value = readPath(event, candidate);
      if (value !== undefined && value !== null && String(value).trim()) {
        return value;
      }
    }
  }
  return "";
}

function pickLastTraceValue(traceEvents, pathCandidates) {
  for (let i = traceEvents.length - 1; i >= 0; i -= 1) {
    const event = traceEvents[i];
    for (const candidate of pathCandidates) {
      const value = readPath(event, candidate);
      if (value !== undefined && value !== null && String(value).trim()) {
        return value;
      }
    }
  }
  return "";
}

function readPath(source, path) {
  const parts = String(path || "").split(".");
  let current = source;
  for (const part of parts) {
    if (!current || typeof current !== "object") {
      return undefined;
    }
    current = current[part];
  }
  return current;
}

function getVerdictMeta(verdict) {
  const normalized = String(verdict || "pass").toLowerCase();
  if (normalized === "retry") {
    return {
      label: "Retry",
      icon: "🔁",
      badgeClass: "border-amber-400/45 bg-amber-500/20 text-amber-100",
    };
  }
  if (normalized === "reflect") {
    return {
      label: "Reflect",
      icon: "🤔",
      badgeClass: "border-violet-400/45 bg-violet-500/20 text-violet-100",
    };
  }

  return {
    label: "Pass",
    icon: "✅",
    badgeClass: "border-emerald-400/45 bg-emerald-500/20 text-emerald-100",
  };
}

function resolveAgent(agentName) {
  const normalized = String(agentName || "").toLowerCase();
  if (!normalized) {
    return null;
  }

  if (normalized.includes("research")) {
    return {
      label: "Research Agent",
      icon: "🔬",
      badgeClass: "border-sky-400/40 bg-sky-500/20 text-sky-100",
    };
  }
  if (normalized.includes("code")) {
    return {
      label: "Code Agent",
      icon: "💻",
      badgeClass: "border-emerald-400/40 bg-emerald-500/20 text-emerald-100",
    };
  }
  if (normalized.includes("analysis")) {
    return {
      label: "Analysis Agent",
      icon: "📊",
      badgeClass: "border-amber-400/40 bg-amber-500/20 text-amber-100",
    };
  }
  if (normalized.includes("writing")) {
    return {
      label: "Writing Agent",
      icon: "✍️",
      badgeClass: "border-violet-400/40 bg-violet-500/20 text-violet-100",
    };
  }

  return {
    label: agentName,
    icon: "🤖",
    badgeClass: "border-slate-400/35 bg-slate-500/20 text-slate-100",
  };
}

function scoreColor(score) {
  if (score >= 6) {
    return "#22c55e";
  }
  if (score >= 3) {
    return "#f59e0b";
  }
  return "#ef4444";
}

function errorSeverityClass(errorType) {
  const normalized = String(errorType || "").toUpperCase();
  if (normalized.includes("TIMEOUT") || normalized.includes("RATE")) {
    return "border-amber-400/35 bg-amber-500/10 text-amber-100";
  }
  if (normalized.includes("SERVER") || normalized.includes("CONNECTION") || normalized.includes("FAILED")) {
    return "border-rose-400/35 bg-rose-500/10 text-rose-100";
  }
  return "border-white/10 bg-white/5 text-slate-200";
}

function formatDuration(durationMs) {
  const ms = toNumber(durationMs);
  if (!ms) {
    return "0.0s";
  }
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatTimestamp(timestamp) {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) {
    return String(timestamp || "-");
  }
  return date.toLocaleString();
}

function normalizeModelLabel(modelName) {
  const normalized = String(modelName || "").trim().toLowerCase();
  if (!normalized) {
    return "";
  }
  if (normalized.includes("gpt-4o-mini")) {
    return "GPT-4o-mini";
  }
  if (normalized.includes("gpt-4o")) {
    return "GPT-4o";
  }
  if (normalized.includes("claude")) {
    return "Claude";
  }
  return String(modelName || "").trim();
}

function toTimestamp(value) {
  if (!value) {
    return 0;
  }
  const parsed = Date.parse(String(value));
  return Number.isFinite(parsed) ? parsed : 0;
}

function toNumber(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
}

function sumBy(items, selector) {
  return items.reduce((sum, item) => sum + toNumber(selector(item)), 0);
}

function formatNumber(value) {
  return toNumber(value).toLocaleString();
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function capitalize(value) {
  const text = String(value || "");
  if (!text) {
    return "";
  }
  return text.charAt(0).toUpperCase() + text.slice(1);
}
