import { motion } from "framer-motion";
import { useMemo } from "react";
import {
  Bar,
  ComposedChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const STATUS_COLORS = {
  success: "#22c55e",
  completed: "#22c55e",
  failed: "#ef4444",
  retrying: "#f59e0b",
  reflecting: "#a855f7",
  running: "#3b82f6",
  pending: "#64748b",
  skipped: "#94a3b8",
};

const MARKER_META = {
  retry: { icon: "🔁", color: "#f59e0b" },
  fallback: { icon: "🔄", color: "#ef4444" },
  reflect: { icon: "🤔", color: "#a855f7" },
};

export default function TraceWaterfall({
  steps = [],
  results = [],
  trace = [],
  onStepClick,
}) {
  const chart = useMemo(
    () => buildWaterfallModel({ steps, results, trace }),
    [results, steps, trace],
  );

  const referenceMarkers = useMemo(() => {
    const lines = [];
    for (let t = 30_000; t < chart.domainMax; t += 30_000) {
      lines.push(t);
    }
    return lines;
  }, [chart.domainMax]);

  return (
    <section className="rounded-2xl border border-white/10 bg-[var(--bg-card)]/70 p-4 sm:p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-[var(--text-primary)]">Trace Waterfall</h2>
          <p className="text-xs text-[var(--text-secondary)]">
            Gantt-style timeline with retries, fallback markers, reflection moments, and parallel overlap.
          </p>
        </div>
        <span className="rounded-full border border-white/15 bg-white/5 px-3 py-1 text-xs text-[var(--text-secondary)]">
          Total {formatSeconds(chart.totalDurationMs)}
        </span>
      </div>

      {chart.rows.length === 0 ? (
        <div className="rounded-xl border border-white/10 bg-[#0b1328]/80 p-5 text-sm text-[var(--text-secondary)]">
          Timeline will appear once the first step starts executing.
        </div>
      ) : (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
          className="overflow-x-auto"
        >
          <div style={{ width: "100%", minWidth: 980, height: chart.chartHeight }}>
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart
                layout="vertical"
                data={chart.rows}
                margin={{ top: 18, right: 36, left: 12, bottom: 24 }}
                barCategoryGap={22}
              >
                <XAxis
                  type="number"
                  dataKey="startMs"
                  domain={[0, chart.domainMax]}
                  tickFormatter={formatMillisecondsTick}
                  stroke="rgba(148,163,184,0.72)"
                  tick={{ fill: "#cbd5e1", fontSize: 11 }}
                  axisLine={{ stroke: "rgba(148,163,184,0.42)" }}
                  tickLine={{ stroke: "rgba(148,163,184,0.42)" }}
                />
                <YAxis
                  type="category"
                  dataKey="label"
                  width={180}
                  interval={0}
                  tick={{ fill: "#e2e8f0", fontSize: 11 }}
                  axisLine={{ stroke: "rgba(148,163,184,0.42)" }}
                  tickLine={{ stroke: "rgba(148,163,184,0.42)" }}
                />

                <Tooltip cursor={{ fill: "rgba(148,163,184,0.08)" }} content={<WaterfallTooltip />} />

                {referenceMarkers.map((timeMs) => (
                  <ReferenceLine
                    key={timeMs}
                    x={timeMs}
                    stroke="rgba(148,163,184,0.4)"
                    strokeDasharray="4 5"
                    label={{
                      value: `${Math.round(timeMs / 1000)}s`,
                      fill: "#94a3b8",
                      position: "top",
                      fontSize: 10,
                    }}
                  />
                ))}

                <Bar dataKey="startMs" stackId="waterfall" fill="transparent" isAnimationActive={false} />
                <Bar
                  dataKey="durationMs"
                  stackId="waterfall"
                  shape={(shapeProps) => (
                    <WaterfallBar
                      {...shapeProps}
                      onStepClick={onStepClick}
                    />
                  )}
                  isAnimationActive
                  animationDuration={460}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          <div className="mt-4 flex flex-wrap gap-3 text-xs text-[var(--text-secondary)]">
            <LegendPill color={STATUS_COLORS.success} label="Success" />
            <LegendPill color={STATUS_COLORS.failed} label="Failed" />
            <LegendPill color={STATUS_COLORS.retrying} label="Retry" />
            <LegendPill color={STATUS_COLORS.reflecting} label="Reflect" />
            <LegendPill color={STATUS_COLORS.running} label="Running" />
            <LegendPill color={STATUS_COLORS.pending} label="Pending" />
          </div>
        </motion.div>
      )}
    </section>
  );
}

function WaterfallBar(props) {
  const {
    x,
    y,
    width,
    height,
    payload,
    onStepClick,
  } = props;

  const color = STATUS_COLORS[payload?.status] || STATUS_COLORS.pending;
  const parallelCount = Math.max(1, Number(payload?.parallelCount || 1));
  const parallelSlot = Number(payload?.parallelSlot || 0);

  const overlapShift = parallelCount > 1
    ? (parallelSlot - (parallelCount - 1) / 2) * 6
    : 0;

  const barY = y + overlapShift + 4;
  const barHeight = Math.max(10, height - 10);
  const effectiveWidth = Math.max(1, width);

  const attempts = Array.isArray(payload?.attempts) ? payload.attempts : [];
  const totalAttemptDuration = attempts.reduce((sum, item) => sum + Number(item?.durationMs || 0), 0);

  const markers = Array.isArray(payload?.markers) ? payload.markers : [];

  const clickable = typeof onStepClick === "function";

  return (
    <g
      onClick={() => {
        if (clickable) {
          onStepClick(payload);
        }
      }}
      style={{ cursor: clickable ? "pointer" : "default" }}
    >
      <rect
        x={x}
        y={barY}
        width={effectiveWidth}
        height={barHeight}
        rx={8}
        ry={8}
        fill={color}
        fillOpacity={parallelCount > 1 ? 0.68 : 0.92}
        stroke="rgba(255,255,255,0.22)"
      />

      {attempts.length > 1 ? (
        attempts.map((attempt, index) => {
          const priorDuration = attempts
            .slice(0, index)
            .reduce((sum, segment) => sum + Number(segment?.durationMs || 0), 0);

          const segmentWidth = totalAttemptDuration > 0
            ? (Number(attempt?.durationMs || 0) / totalAttemptDuration) * effectiveWidth
            : effectiveWidth / attempts.length;

          const segmentX = x + (totalAttemptDuration > 0
            ? (priorDuration / totalAttemptDuration) * effectiveWidth
            : (index / attempts.length) * effectiveWidth);

          return (
            <g key={`${payload.id}-attempt-${index + 1}`}>
              <rect
                x={segmentX}
                y={barY + 2}
                width={Math.max(1, segmentWidth)}
                height={Math.max(4, barHeight - 4)}
                rx={6}
                ry={6}
                fill="rgba(15,23,42,0.18)"
              />
              {attempts.length <= 3 ? (
                <text
                  x={segmentX + Math.max(14, segmentWidth / 2)}
                  y={barY + barHeight / 2 + 3}
                  fill="rgba(255,255,255,0.9)"
                  textAnchor="middle"
                  fontSize="9"
                >
                  {index + 1}
                </text>
              ) : null}
            </g>
          );
        })
      ) : null}

      {markers.map((marker, index) => {
        const markerMeta = MARKER_META[marker.type] || { icon: "•", color: "#94a3b8" };

        const relativeOffset = marker.absoluteMs - Number(payload.startMs || 0);
        const ratio = clamp(relativeOffset / Math.max(1, Number(payload.durationMs || 1)), 0, 1);
        const markerX = x + ratio * effectiveWidth;
        const markerY = barY - 4 - (index % 2) * 10;

        return (
          <g key={`${payload.id}-marker-${marker.type}-${index}`}>
            <line
              x1={markerX}
              y1={barY}
              x2={markerX}
              y2={markerY + 3}
              stroke={markerMeta.color}
              strokeWidth="1.2"
              strokeDasharray="2 2"
            />
            <text x={markerX} y={markerY} textAnchor="middle" fontSize="10" fill={markerMeta.color}>
              {markerMeta.icon}
            </text>
          </g>
        );
      })}

      {parallelCount > 1 ? (
        <text
          x={x + effectiveWidth + 8}
          y={barY + barHeight / 2 + 3}
          fill="rgba(191,219,254,0.9)"
          fontSize="10"
        >
          L{payload.level} parallel
        </text>
      ) : null}
    </g>
  );
}

function WaterfallTooltip({ active, payload }) {
  if (!active || !payload || !payload.length) {
    return null;
  }

  const row = payload[0]?.payload;
  if (!row) {
    return null;
  }

  return (
    <div className="min-w-[240px] rounded-xl border border-white/15 bg-[#09132a] p-3 text-xs text-slate-200 shadow-xl">
      <p className="text-sm font-semibold text-slate-100">{row.label}</p>
      <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1">
        <TooltipLine label="Status" value={row.status} />
        <TooltipLine label="Duration" value={formatMillisecondsTick(row.durationMs)} />
        <TooltipLine label="Model" value={row.model || "-"} />
        <TooltipLine label="Tokens" value={formatNumber(row.tokens)} />
        <TooltipLine label="Start" value={formatMillisecondsTick(row.startMs)} />
        <TooltipLine label="Level" value={String(row.level)} />
      </div>
    </div>
  );
}

function TooltipLine({ label, value }) {
  return (
    <p>
      <span className="text-slate-400">{label}:</span>{" "}
      <span className="text-slate-100">{value}</span>
    </p>
  );
}

function LegendPill({ color, label }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/5 px-2.5 py-1">
      <span className="h-2.5 w-2.5 rounded-full" style={{ background: color }} />
      {label}
    </span>
  );
}

function buildWaterfallModel({ steps, results, trace }) {
  const normalizedSteps = (Array.isArray(steps) ? steps : []).map((step, index) => ({
    ...step,
    step_id: String(step?.step_id || `step_${index + 1}`),
    name: String(step?.name || `Step ${index + 1}`),
    dependencies: Array.isArray(step?.dependencies)
      ? step.dependencies.map((dependencyId) => String(dependencyId)).filter(Boolean)
      : [],
  }));

  if (!normalizedSteps.length) {
    return {
      rows: [],
      totalDurationMs: 0,
      domainMax: 60_000,
      chartHeight: 320,
    };
  }

  const resultById = new Map();
  for (const result of Array.isArray(results) ? results : []) {
    const id = String(result?.step_id || "").trim();
    if (id) {
      resultById.set(id, result);
    }
  }

  const traceByStep = new Map();
  for (const event of Array.isArray(trace) ? trace : []) {
    const stepId = String(event?.step_id || event?.data?.step_id || "").trim();
    if (!stepId) {
      continue;
    }

    if (!traceByStep.has(stepId)) {
      traceByStep.set(stepId, []);
    }
    traceByStep.get(stepId).push(event);
  }

  for (const events of traceByStep.values()) {
    events.sort((a, b) => toTimestamp(a?.timestamp) - toTimestamp(b?.timestamp));
  }

  const { levelByStepId } = computeExecutionLevels(normalizedSteps);

  const stepItems = normalizedSteps.map((step, index) => {
    const stepTrace = traceByStep.get(step.step_id) || [];
    const result = resultById.get(step.step_id) || null;

    const startedAtTs = getEventTimestamp(stepTrace, "step_started", "first");
    const endedAtTs = getEventTimestamp(stepTrace, "step_completed", "last")
      || getEventTimestamp(stepTrace, "step_failed", "last");

    const retryEvents = stepTrace.filter((event) => String(event?.event_type || "") === "retry_triggered");
    const fallbackEvents = stepTrace.filter((event) => String(event?.event_type || "") === "fallback_triggered");
    const reflectionEvents = stepTrace.filter((event) => {
      const type = String(event?.event_type || "");
      return type === "reflection_started" || type === "reflection_completed";
    });

    const status = resolveStepStatus({
      result,
      traceEvents: stepTrace,
      index,
    });

    const durationFromResult = toNumber(result?.latency_ms);
    const durationFromTrace = startedAtTs && endedAtTs ? Math.max(0, endedAtTs - startedAtTs) : 0;
    const durationMs = Math.max(500, durationFromResult || durationFromTrace || 2_000);

    const retryCount = Math.max(toNumber(result?.retry_count), retryEvents.length);
    const attempts = buildAttemptSegments(durationMs, retryCount);

    return {
      id: step.step_id,
      label: step.name,
      step,
      result,
      status,
      level: Number.isFinite(Number(step?.level)) ? Number(step.level) : levelByStepId.get(step.step_id) || 0,
      dependencies: step.dependencies,
      durationMs,
      startedAtTs,
      endedAtTs,
      attempts,
      markers: [
        ...retryEvents.map((event) => ({ type: "retry", timestamp: toTimestamp(event?.timestamp) })),
        ...fallbackEvents.map((event) => ({ type: "fallback", timestamp: toTimestamp(event?.timestamp) })),
        ...reflectionEvents.map((event) => ({ type: "reflect", timestamp: toTimestamp(event?.timestamp) })),
      ],
      tokens: toNumber(result?.tokens_used),
      model: normalizeModelLabel(result?.model_used || step?.model_used || ""),
    };
  });

  const earliestTraceStart = stepItems
    .map((item) => item.startedAtTs)
    .filter(Boolean)
    .sort((a, b) => a - b)[0];

  let cursor = earliestTraceStart || Date.now();
  const endByStepId = new Map();

  for (const item of stepItems) {
    const dependencyEnd = item.dependencies
      .map((dependencyId) => endByStepId.get(dependencyId))
      .filter(Boolean)
      .reduce((max, value) => Math.max(max, value), 0);

    const computedStart = item.startedAtTs || Math.max(cursor, dependencyEnd || cursor);
    const computedEnd = item.endedAtTs || computedStart + item.durationMs;

    item.absoluteStart = computedStart;
    item.absoluteEnd = Math.max(computedEnd, computedStart + item.durationMs);

    endByStepId.set(item.id, item.absoluteEnd);

    const sameLevelFollowing = stepItems.some(
      (candidate) => candidate.level === item.level && candidate.id !== item.id,
    );

    cursor = sameLevelFollowing ? cursor : item.absoluteEnd;
  }

  const baseline = Math.min(...stepItems.map((item) => item.absoluteStart));
  const maxEnd = Math.max(...stepItems.map((item) => item.absoluteEnd));

  const groupedByLevel = groupBy(stepItems, (item) => item.level);

  const rows = stepItems.map((item) => {
    const levelItems = groupedByLevel.get(item.level) || [];
    const parallelCount = levelItems.length;
    const parallelSlot = levelItems.findIndex((candidate) => candidate.id === item.id);

    const startMs = Math.max(0, item.absoluteStart - baseline);
    const durationMs = Math.max(1, item.absoluteEnd - item.absoluteStart);

    const markers = item.markers
      .filter((marker) => Number.isFinite(marker.timestamp) && marker.timestamp > 0)
      .map((marker) => ({
        ...marker,
        absoluteMs: Math.max(0, marker.timestamp - baseline),
      }));

    return {
      id: item.id,
      label: item.label,
      status: item.status,
      startMs,
      durationMs,
      attempts: item.attempts,
      markers,
      tokens: item.tokens,
      model: item.model,
      level: item.level,
      parallelCount,
      parallelSlot,
      step_id: item.id,
      step_name: item.label,
      retry_count: Math.max(0, item.attempts.length - 1),
    };
  });

  const totalDurationMs = Math.max(1_000, maxEnd - baseline);

  return {
    rows,
    totalDurationMs,
    domainMax: normalizeDomain(totalDurationMs),
    chartHeight: Math.max(330, rows.length * 62 + 74),
  };
}

function buildAttemptSegments(durationMs, retryCount) {
  const attemptsCount = Math.max(1, Math.min(4, retryCount + 1));
  const segmentDuration = durationMs / attemptsCount;

  return new Array(attemptsCount).fill(null).map((_, index) => ({
    attempt: index + 1,
    durationMs: segmentDuration,
  }));
}

function resolveStepStatus({ result, traceEvents, index }) {
  const resultStatus = String(result?.status || "").toLowerCase().trim();
  if (resultStatus) {
    return resultStatus;
  }

  if (traceEvents.some((event) => String(event?.event_type || "") === "step_failed")) {
    return "failed";
  }
  if (traceEvents.some((event) => String(event?.event_type || "") === "reflection_started")) {
    return "reflecting";
  }
  if (traceEvents.some((event) => String(event?.event_type || "") === "retry_triggered")) {
    return "retrying";
  }
  if (traceEvents.some((event) => String(event?.event_type || "") === "step_completed")) {
    return "success";
  }
  if (index === 0 && traceEvents.some((event) => String(event?.event_type || "") === "step_started")) {
    return "running";
  }
  return "pending";
}

function computeExecutionLevels(steps) {
  const ids = steps.map((step) => step.step_id);
  const idSet = new Set(ids);
  const dependenciesById = new Map();

  for (const step of steps) {
    dependenciesById.set(
      step.step_id,
      new Set(step.dependencies.filter((dependencyId) => idSet.has(dependencyId))),
    );
  }

  const assigned = new Set();
  const levels = [];

  while (assigned.size < steps.length) {
    const ready = [];
    for (const id of ids) {
      if (assigned.has(id)) {
        continue;
      }
      const dependencies = dependenciesById.get(id) || new Set();
      if ([...dependencies].every((dependencyId) => assigned.has(dependencyId))) {
        ready.push(id);
      }
    }

    if (!ready.length) {
      const remaining = ids.filter((id) => !assigned.has(id));
      if (!remaining.length) {
        break;
      }
      ready.push(remaining[0]);
    }

    ready.forEach((id) => assigned.add(id));
    levels.push(ready);
  }

  const levelByStepId = new Map();
  levels.forEach((level, index) => {
    level.forEach((stepId) => levelByStepId.set(stepId, index));
  });

  return { levels, levelByStepId };
}

function getEventTimestamp(events, eventType, strategy = "first") {
  const filtered = events
    .filter((event) => String(event?.event_type || "") === eventType)
    .map((event) => toTimestamp(event?.timestamp))
    .filter(Boolean)
    .sort((a, b) => a - b);

  if (!filtered.length) {
    return 0;
  }

  return strategy === "last" ? filtered[filtered.length - 1] : filtered[0];
}

function groupBy(items, keyFn) {
  const map = new Map();
  for (const item of items) {
    const key = keyFn(item);
    if (!map.has(key)) {
      map.set(key, []);
    }
    map.get(key).push(item);
  }
  return map;
}

function normalizeDomain(totalDurationMs) {
  if (totalDurationMs <= 30_000) {
    return 30_000;
  }
  return Math.ceil(totalDurationMs / 30_000) * 30_000;
}

function formatMillisecondsTick(value) {
  const ms = Number(value || 0);
  if (ms < 1000) {
    return `${ms}ms`;
  }
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatSeconds(valueMs) {
  return `${(Number(valueMs || 0) / 1000).toFixed(1)}s`;
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

function formatNumber(value) {
  const amount = Number(value || 0);
  if (!Number.isFinite(amount)) {
    return "0";
  }
  return amount.toLocaleString();
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}
