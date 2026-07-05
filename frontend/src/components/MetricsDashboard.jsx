import { motion } from "framer-motion";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const STATUS_COLORS = {
  Success: "#22c55e",
  Failed: "#ef4444",
  Skipped: "#64748b",
  Retried: "#f59e0b",
};

const REFLECTION_COLORS = ["#3b82f6", "#facc15", "#a855f7", "#ef4444"];
const MODEL_COLORS = {
  "GPT-4o": "#3b82f6",
  "GPT-4o-mini": "#22c55e",
  "Claude 3.5": "#8b5cf6",
};

const MOCK_METRICS = {
  total_tasks: 30,
  completed_tasks: 29,
  failed_tasks: 1,
  completion_rate: 0.967,
  avg_recovery_rate: 0.784,
  avg_quality_score: 7.4,
  total_tokens_consumed: 124580,
  total_cost_usd: 1.87,
  tokens_input: 74500,
  tokens_output: 50080,
  retry_count: 7,
  reflection_count: 6,
  skipped_steps: 2,
  success_delta: 0.034,
  recovery_delta: 0.027,
  quality_delta: 0.018,
  tokens_delta: 0.062,
  cost_delta: -0.014,
  execution_breakdown: [
    { step: "Plan", execution_ms: 1240, retry_ms: 0, reflection_ms: 0, status: "Success" },
    { step: "Search", execution_ms: 4020, retry_ms: 680, reflection_ms: 260, status: "Retried" },
    { step: "Analyze", execution_ms: 3180, retry_ms: 520, reflection_ms: 440, status: "Success" },
    { step: "Draft", execution_ms: 2860, retry_ms: 0, reflection_ms: 520, status: "Success" },
    { step: "Review", execution_ms: 1670, retry_ms: 210, reflection_ms: 0, status: "Failed" },
  ],
  reflection_strategies: {
    MODIFY_STEP: 8,
    SKIP_STEP: 3,
    DECOMPOSE: 2,
    ABORT: 1,
  },
  failure_types: {
    TIMEOUT: 4,
    RATE_LIMITED: 3,
    EMPTY_OUTPUT: 2,
    SERVER_ERROR: 1,
  },
  trends: {
    recovery: [62, 66, 68, 71, 74, 76, 78.4],
  },
  cost_by_model: {
    "GPT-4o": 1.08,
    "GPT-4o-mini": 0.32,
    "Claude 3.5": 0.47,
  },
  agent_contributions: {
    "Research Agent": { steps_handled: 9, avg_quality: 7.9, tokens_used: 35640 },
    "Code Agent": { steps_handled: 7, avg_quality: 7.3, tokens_used: 28920 },
    "Analysis Agent": { steps_handled: 8, avg_quality: 7.6, tokens_used: 33120 },
    "Writing Agent": { steps_handled: 6, avg_quality: 8.2, tokens_used: 26900 },
  },
};

const MOCK_PROVIDER_METRICS = {
  openai_gpt4o: {
    display_name: "OpenAI GPT-4o",
    calls: 41,
    failures: 3,
    avg_latency: 1810,
    circuit_state: "closed",
    latency_trend: [1900, 1830, 1750, 1680, 1820, 1760, 1810],
  },
  openai_gpt4o_mini: {
    display_name: "OpenAI GPT-4o-mini",
    calls: 58,
    failures: 4,
    avg_latency: 950,
    circuit_state: "half_open",
    latency_trend: [870, 890, 910, 980, 1000, 970, 950],
  },
  anthropic_claude35: {
    display_name: "Claude 3.5",
    calls: 17,
    failures: 2,
    avg_latency: 2320,
    circuit_state: "open",
    latency_trend: [2100, 2180, 2240, 2300, 2390, 2450, 2320],
  },
};

export default function MetricsDashboard({ metrics = null, providerMetrics = null }) {
  const normalized = useMemo(
    () => normalizeMetrics(metrics, providerMetrics),
    [metrics, providerMetrics],
  );

  return (
    <section className="space-y-5 rounded-2xl border border-white/10 bg-[linear-gradient(140deg,rgba(15,23,42,0.74),rgba(30,41,59,0.56))] p-4 sm:p-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold tracking-tight text-[var(--text-primary)]">Metrics Dashboard</h2>
          <p className="text-xs text-[var(--text-secondary)]">Operational reliability, execution behavior, provider health, and contribution analytics.</p>
        </div>
        <span className="rounded-full border border-white/15 bg-white/5 px-3 py-1 text-xs text-[var(--text-secondary)]">
          Updated from live + aggregate metrics
        </span>
      </header>

      <section className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-5">
        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.28, delay: 0.02 }}>
          <CompletionCard
            completed={normalized.keyMetrics.tasksCompleted}
            total={normalized.keyMetrics.tasksTotal}
            trend={normalized.keyMetrics.completionDelta}
          />
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.28, delay: 0.08 }}>
          <RecoveryCard
            value={normalized.keyMetrics.recoveryRate}
            trend={normalized.keyMetrics.recoveryDelta}
            series={normalized.sparklineRecovery}
          />
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.28, delay: 0.14 }}>
          <QualityCard
            value={normalized.keyMetrics.avgQuality}
            trend={normalized.keyMetrics.qualityDelta}
          />
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.28, delay: 0.2 }}>
          <TokensCard
            totalTokens={normalized.keyMetrics.totalTokens}
            inputTokens={normalized.keyMetrics.inputTokens}
            outputTokens={normalized.keyMetrics.outputTokens}
            trend={normalized.keyMetrics.tokensDelta}
          />
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.28, delay: 0.26 }}>
          <CostCard
            totalCost={normalized.keyMetrics.totalCost}
            trend={normalized.keyMetrics.costDelta}
            perModel={normalized.costByModel}
          />
        </motion.div>
      </section>

      <section className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Panel title="Execution Analysis" subtitle="Duration stack: execution + retries + reflection">
          <div className="h-[320px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={normalized.executionBreakdown} margin={{ top: 10, right: 14, left: 0, bottom: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.18)" />
                <XAxis dataKey="step" tick={{ fill: "#cbd5e1", fontSize: 11 }} axisLine={{ stroke: "rgba(148,163,184,0.42)" }} tickLine={{ stroke: "rgba(148,163,184,0.42)" }} />
                <YAxis tick={{ fill: "#cbd5e1", fontSize: 11 }} axisLine={{ stroke: "rgba(148,163,184,0.42)" }} tickLine={{ stroke: "rgba(148,163,184,0.42)" }} />
                <Tooltip content={<ExecutionTooltip />} />
                <Bar dataKey="execution_ms" stackId="time" fill="#3b82f6" radius={[4, 4, 0, 0]} />
                <Bar dataKey="retry_ms" stackId="time" fill="#f59e0b" />
                <Bar dataKey="reflection_ms" stackId="time" fill="#a855f7" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>

        <Panel title="Execution Outcome" subtitle="Success/failure distribution across steps">
          <div className="relative h-[320px]">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={normalized.statusBreakdown}
                  cx="50%"
                  cy="50%"
                  outerRadius={108}
                  innerRadius={66}
                  paddingAngle={3}
                  dataKey="value"
                >
                  {normalized.statusBreakdown.map((entry) => (
                    <Cell key={entry.name} fill={entry.color} />
                  ))}
                </Pie>
                <Tooltip content={<StatusTooltip />} />
              </PieChart>
            </ResponsiveContainer>
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
              <div className="text-center">
                <p className="text-2xl font-bold text-[var(--text-primary)]">{normalized.totalStepCount}</p>
                <p className="text-xs text-[var(--text-secondary)]">Total Steps</p>
              </div>
            </div>
          </div>
          <div className="mt-3 flex flex-wrap gap-3 text-xs">
            {normalized.statusBreakdown.map((item) => (
              <span key={item.name} className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/5 px-2.5 py-1 text-[var(--text-secondary)]">
                <span className="h-2.5 w-2.5 rounded-full" style={{ background: item.color }} />
                {item.name}: {item.value}
              </span>
            ))}
          </div>
        </Panel>
      </section>

      <section className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Panel title="Reflection Strategy Distribution" subtitle="How failures were handled by the reflector">
          <div className="h-[300px]">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={normalized.reflectionBreakdown}
                  cx="50%"
                  cy="47%"
                  outerRadius={104}
                  innerRadius={44}
                  dataKey="value"
                  paddingAngle={2}
                >
                  {normalized.reflectionBreakdown.map((entry, index) => (
                    <Cell key={entry.name} fill={REFLECTION_COLORS[index % REFLECTION_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip content={<SimplePieTooltip />} />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-1 space-y-1 text-xs text-[var(--text-secondary)]">
            {normalized.reflectionBreakdown.map((entry, index) => (
              <div key={entry.name} className="flex items-center justify-between rounded-lg border border-white/10 bg-white/5 px-2.5 py-1.5">
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-full" style={{ background: REFLECTION_COLORS[index % REFLECTION_COLORS.length] }} />
                  {entry.name}
                </span>
                <span>
                  {entry.value} ({entry.percent}%)
                </span>
              </div>
            ))}
          </div>
        </Panel>

        <Panel title="Failure Type Histogram" subtitle="Most frequent reliability failure categories">
          <div className="h-[300px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={normalized.failureHistogram} layout="vertical" margin={{ top: 8, right: 14, left: 8, bottom: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.15)" />
                <XAxis type="number" tick={{ fill: "#cbd5e1", fontSize: 11 }} axisLine={{ stroke: "rgba(148,163,184,0.42)" }} tickLine={{ stroke: "rgba(148,163,184,0.42)" }} />
                <YAxis type="category" dataKey="name" width={110} tick={{ fill: "#cbd5e1", fontSize: 11 }} axisLine={{ stroke: "rgba(148,163,184,0.42)" }} tickLine={{ stroke: "rgba(148,163,184,0.42)" }} />
                <Tooltip content={<FailureTooltip />} />
                <Bar dataKey="count" radius={[0, 6, 6, 0]}>
                  {normalized.failureHistogram.map((item) => (
                    <Cell key={item.name} fill={item.color} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-[var(--text-primary)]">Provider Health</h3>
          <p className="text-xs text-[var(--text-secondary)]">Circuit state, failure rate, and live latency trends</p>
        </div>

        <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
          {normalized.providers.map((provider, index) => (
            <motion.article
              key={provider.key}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.24, delay: 0.05 + index * 0.06 }}
              className="rounded-2xl border border-white/15 bg-white/[0.04] p-3.5 backdrop-blur-xl"
            >
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="text-sm font-semibold text-[var(--text-primary)]">{provider.displayName}</p>
                  <p className="text-[11px] text-[var(--text-secondary)]">Model provider health card</p>
                </div>
                <StatePill state={provider.circuitState} />
              </div>

              <div className="mt-3 grid grid-cols-3 gap-2 text-center text-xs">
                <StatTiny label="Calls" value={formatNumber(provider.calls)} />
                <StatTiny label="Failure" value={`${provider.failureRate.toFixed(1)}%`} />
                <StatTiny label="Latency" value={`${Math.round(provider.avgLatency)}ms`} />
              </div>

              <div className="mt-3 h-[66px] rounded-lg border border-white/10 bg-[#0b1733]/90 p-1.5">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={provider.latencyTrend.map((value, point) => ({ point, value }))}>
                    <Line type="monotone" dataKey="value" stroke={provider.sparkColor} dot={false} strokeWidth={2} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </motion.article>
          ))}
        </div>
      </section>

      {normalized.agentContributions.length > 0 ? (
        <section>
          <Panel title="Agent Contributions" subtitle="Steps handled per specialist, color-mapped by average quality">
            <div className="h-[320px]">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={normalized.agentContributions} layout="vertical" margin={{ top: 8, right: 22, left: 10, bottom: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.16)" />
                  <XAxis type="number" tick={{ fill: "#cbd5e1", fontSize: 11 }} axisLine={{ stroke: "rgba(148,163,184,0.42)" }} tickLine={{ stroke: "rgba(148,163,184,0.42)" }} />
                  <YAxis type="category" dataKey="name" width={120} tick={{ fill: "#cbd5e1", fontSize: 11 }} axisLine={{ stroke: "rgba(148,163,184,0.42)" }} tickLine={{ stroke: "rgba(148,163,184,0.42)" }} />
                  <Tooltip content={<AgentTooltip />} />
                  <Bar dataKey="stepsHandled" radius={[0, 7, 7, 0]}>
                    {normalized.agentContributions.map((entry) => (
                      <Cell key={entry.name} fill={qualityColor(entry.avgQuality)} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Panel>
        </section>
      ) : null}
    </section>
  );
}

function Panel({ title, subtitle, children }) {
  return (
    <article className="rounded-2xl border border-white/15 bg-white/[0.04] p-3.5 backdrop-blur-xl">
      <div className="mb-2">
        <h3 className="text-sm font-semibold text-[var(--text-primary)]">{title}</h3>
        <p className="text-[11px] text-[var(--text-secondary)]">{subtitle}</p>
      </div>
      {children}
    </article>
  );
}

function CompletionCard({ completed, total, trend }) {
  const percent = total > 0 ? (completed / total) * 100 : 0;
  const animatedCompleted = useCountUp(completed, 900, 0);
  const animatedTotal = useCountUp(total, 900, 0);

  return (
    <MetricCard title="Tasks Completed" subtitle="Success throughput">
      <div className="flex items-center gap-3">
        <CircularRing value={percent} />
        <div>
          <p className="text-2xl font-bold text-[var(--text-primary)]">
            {Math.round(animatedCompleted)}/{Math.max(1, Math.round(animatedTotal))}
          </p>
          <p className="text-xs text-[var(--text-secondary)]">{percent.toFixed(1)}% completion</p>
        </div>
      </div>
      <TrendTag value={trend} />
    </MetricCard>
  );
}

function RecoveryCard({ value, trend, series }) {
  const animated = useCountUp(value, 900, 1);
  return (
    <MetricCard title="Recovery Rate" subtitle="Retries + fallbacks resolved">
      <div className="flex items-center justify-between gap-2">
        <p className="text-2xl font-bold text-[var(--text-primary)]">{animated.toFixed(1)}%</p>
        <div className="h-[50px] w-[88px] rounded-lg border border-white/10 bg-[#0b1733]/80 p-1">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={series.map((item, index) => ({ index, value: item }))}>
              <Line type="monotone" dataKey="value" stroke="#22c55e" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
      <TrendTag value={trend} />
    </MetricCard>
  );
}

function QualityCard({ value, trend }) {
  const animated = useCountUp(value, 900, 1);
  return (
    <MetricCard title="Avg Quality" subtitle="Relevance/completeness consistency">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-2xl font-bold text-[var(--text-primary)]">{animated.toFixed(1)}/10</p>
          <p className="text-xs text-[var(--text-secondary)]">Weighted validator average</p>
        </div>
        <QualityGauge value={value} />
      </div>
      <TrendTag value={trend} />
    </MetricCard>
  );
}

function TokensCard({ totalTokens, inputTokens, outputTokens, trend }) {
  const animated = useCountUp(totalTokens, 900, 0);
  return (
    <MetricCard title="Total Tokens" subtitle="Input/output token split">
      <p className="text-2xl font-bold text-[var(--text-primary)]">{formatNumber(Math.round(animated))}</p>
      <div className="mt-2 space-y-1 text-xs text-[var(--text-secondary)]">
        <p>Input: {formatNumber(inputTokens)}</p>
        <p>Output: {formatNumber(outputTokens)}</p>
      </div>
      <TrendTag value={trend} />
    </MetricCard>
  );
}

function CostCard({ totalCost, trend, perModel }) {
  const animated = useCountUp(totalCost, 900, 2);
  return (
    <MetricCard title="Total Cost" subtitle="Estimated model spend">
      <div className="group relative inline-block">
        <p className="text-2xl font-bold text-[var(--text-primary)]">${animated.toFixed(2)}</p>
        <div className="pointer-events-none absolute left-0 top-[110%] z-20 hidden min-w-[180px] rounded-lg border border-white/15 bg-[#060d1f] p-2 text-xs text-slate-200 shadow-xl group-hover:block">
          {Object.entries(perModel).map(([name, value]) => (
            <div key={name} className="flex items-center justify-between gap-2">
              <span>{name}</span>
              <span>${value.toFixed(2)}</span>
            </div>
          ))}
        </div>
      </div>
      <p className="text-xs text-[var(--text-secondary)]">Hover amount for per-model breakdown</p>
      <TrendTag value={trend} />
    </MetricCard>
  );
}

function MetricCard({ title, subtitle, children }) {
  return (
    <article className="rounded-2xl border border-white/15 bg-white/[0.04] p-3.5 backdrop-blur-xl">
      <p className="text-xs uppercase tracking-wide text-[var(--text-secondary)]">{title}</p>
      <p className="mt-0.5 text-[11px] text-[var(--text-secondary)]">{subtitle}</p>
      <div className="mt-3">{children}</div>
    </article>
  );
}

function CircularRing({ value }) {
  const normalized = clamp(value, 0, 100);
  const radius = 24;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (normalized / 100) * circumference;

  return (
    <svg width="62" height="62" viewBox="0 0 62 62" className="shrink-0">
      <defs>
        <linearGradient id="ring-gradient" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#22c55e" />
          <stop offset="100%" stopColor="#38bdf8" />
        </linearGradient>
      </defs>
      <circle cx="31" cy="31" r={radius} stroke="rgba(148,163,184,0.25)" strokeWidth="6" fill="none" />
      <circle
        cx="31"
        cy="31"
        r={radius}
        stroke="url(#ring-gradient)"
        strokeWidth="6"
        fill="none"
        strokeDasharray={circumference}
        strokeDashoffset={offset}
        strokeLinecap="round"
        transform="rotate(-90 31 31)"
      />
    </svg>
  );
}

function QualityGauge({ value }) {
  const normalized = clamp(value, 0, 10);
  const percent = normalized / 10;
  const radius = 25;
  const circumference = Math.PI * radius;
  const dashOffset = circumference - percent * circumference;

  return (
    <svg width="72" height="44" viewBox="0 0 72 44" className="shrink-0">
      <defs>
        <linearGradient id="quality-gauge-gradient" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#ef4444" />
          <stop offset="55%" stopColor="#f59e0b" />
          <stop offset="100%" stopColor="#22c55e" />
        </linearGradient>
      </defs>
      <path d="M 11 36 A 25 25 0 0 1 61 36" stroke="rgba(148,163,184,0.25)" strokeWidth="7" fill="none" />
      <path
        d="M 11 36 A 25 25 0 0 1 61 36"
        stroke="url(#quality-gauge-gradient)"
        strokeWidth="7"
        fill="none"
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={dashOffset}
      />
    </svg>
  );
}

function TrendTag({ value }) {
  if (!Number.isFinite(value) || value === 0) {
    return <p className="mt-2 text-xs text-[var(--text-secondary)]">No trend delta</p>;
  }

  const positive = value > 0;
  return (
    <p className={`mt-2 inline-flex rounded-full border px-2 py-0.5 text-[11px] font-medium ${positive ? "border-emerald-400/40 bg-emerald-500/20 text-emerald-100" : "border-rose-400/40 bg-rose-500/20 text-rose-100"}`}>
      {positive ? "↑" : "↓"} {(Math.abs(value) * 100).toFixed(1)}%
    </p>
  );
}

function StatePill({ state }) {
  const normalized = String(state || "closed").toUpperCase();
  if (normalized === "OPEN") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-rose-400/45 bg-rose-500/20 px-2.5 py-1 text-[11px] text-rose-100">
        <motion.span
          className="h-2 w-2 rounded-full bg-rose-300"
          animate={{ scale: [1, 1.35, 1] }}
          transition={{ duration: 1, repeat: Number.POSITIVE_INFINITY }}
        />
        Down
      </span>
    );
  }

  if (normalized === "HALF_OPEN") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-amber-400/45 bg-amber-500/20 px-2.5 py-1 text-[11px] text-amber-100">
        <span className="h-2 w-2 rounded-full bg-amber-300" />
        Probing
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-emerald-400/45 bg-emerald-500/20 px-2.5 py-1 text-[11px] text-emerald-100">
      <span className="h-2 w-2 rounded-full bg-emerald-300" />
      Healthy
    </span>
  );
}

function StatTiny({ label, value }) {
  return (
    <div className="rounded-lg border border-white/10 bg-white/5 px-2 py-1.5">
      <p className="text-[10px] uppercase tracking-wide text-[var(--text-secondary)]">{label}</p>
      <p className="mt-0.5 text-xs font-semibold text-[var(--text-primary)]">{value}</p>
    </div>
  );
}

function ExecutionTooltip({ active, payload, label }) {
  if (!active || !payload || !payload.length) {
    return null;
  }

  const row = payload[0]?.payload;
  if (!row) {
    return null;
  }

  return (
    <TooltipCard title={label || row.step}>
      <p>Execution: {formatMilliseconds(row.execution_ms)}</p>
      <p>Retry: {formatMilliseconds(row.retry_ms)}</p>
      <p>Reflection: {formatMilliseconds(row.reflection_ms)}</p>
      <p>Status: {row.status}</p>
    </TooltipCard>
  );
}

function StatusTooltip({ active, payload }) {
  if (!active || !payload || !payload.length) {
    return null;
  }
  const row = payload[0]?.payload;
  return (
    <TooltipCard title={row?.name || "Status"}>
      <p>Count: {row?.value || 0}</p>
    </TooltipCard>
  );
}

function SimplePieTooltip({ active, payload }) {
  if (!active || !payload || !payload.length) {
    return null;
  }
  const row = payload[0]?.payload;
  return (
    <TooltipCard title={row?.name || "Segment"}>
      <p>Count: {row?.value || 0}</p>
      <p>Share: {row?.percent || 0}%</p>
    </TooltipCard>
  );
}

function FailureTooltip({ active, payload }) {
  if (!active || !payload || !payload.length) {
    return null;
  }
  const row = payload[0]?.payload;
  return (
    <TooltipCard title={row?.name || "Failure"}>
      <p>Events: {row?.count || 0}</p>
    </TooltipCard>
  );
}

function AgentTooltip({ active, payload }) {
  if (!active || !payload || !payload.length) {
    return null;
  }
  const row = payload[0]?.payload;
  return (
    <TooltipCard title={row?.name || "Agent"}>
      <p>Steps: {row?.stepsHandled || 0}</p>
      <p>Avg quality: {(row?.avgQuality || 0).toFixed(1)}/10</p>
      <p>Tokens: {formatNumber(row?.tokensUsed || 0)}</p>
    </TooltipCard>
  );
}

function TooltipCard({ title, children }) {
  return (
    <div className="rounded-lg border border-white/15 bg-[#060d1f] px-3 py-2 text-xs text-slate-100 shadow-xl">
      <p className="mb-1 font-semibold text-slate-100">{title}</p>
      <div className="space-y-0.5 text-slate-300">{children}</div>
    </div>
  );
}

function normalizeMetrics(rawMetrics, rawProviderMetrics) {
  const metrics = rawMetrics && typeof rawMetrics === "object" ? rawMetrics : MOCK_METRICS;

  const tasksTotal = pickNumber(metrics, ["total_tasks", "totalTasks"], MOCK_METRICS.total_tasks);
  const tasksCompleted = pickNumber(metrics, ["completed_tasks", "completedTasks"], MOCK_METRICS.completed_tasks);
  const completionRate = pickNumber(
    metrics,
    ["completion_rate", "completionRate"],
    tasksTotal > 0 ? tasksCompleted / tasksTotal : MOCK_METRICS.completion_rate,
  );

  const recoveryRate = ratioToPercent(pickNumber(metrics, ["avg_recovery_rate", "avgRecoveryRate", "recovery_rate"], MOCK_METRICS.avg_recovery_rate));
  const avgQuality = clamp(pickNumber(metrics, ["avg_quality_score", "avgQualityScore"], MOCK_METRICS.avg_quality_score), 0, 10);

  const totalTokens = pickNumber(metrics, ["total_tokens_consumed", "total_tokens", "totalTokens", "llm_tokens_used"], MOCK_METRICS.total_tokens_consumed);
  const inputTokens = pickNumber(metrics, ["tokens_input", "tokensInput"], Math.round(totalTokens * 0.6));
  const outputTokens = pickNumber(metrics, ["tokens_output", "tokensOutput"], Math.max(0, totalTokens - inputTokens));
  const totalCost = pickNumber(metrics, ["total_cost_usd", "estimated_cost_usd", "totalCostUsd", "estimatedCostUsd"], MOCK_METRICS.total_cost_usd);

  const executionBreakdown = normalizeExecutionBreakdown(metrics.execution_breakdown || metrics.step_durations);

  const failedSteps = pickNumber(metrics, ["failed_steps", "failedSteps", "failed_tasks", "failedTasks"], MOCK_METRICS.failed_tasks);
  const skippedSteps = pickNumber(metrics, ["skipped_steps", "skippedSteps"], MOCK_METRICS.skipped_steps);
  const retryCount = pickNumber(metrics, ["retry_count", "retryCount"], MOCK_METRICS.retry_count);

  const totalStepCount = Math.max(1, executionBreakdown.length || pickNumber(metrics, ["total_steps", "totalSteps"], tasksTotal));
  const successfulSteps = Math.max(0, pickNumber(metrics, ["successful_steps", "successfulSteps"], totalStepCount - failedSteps - skippedSteps));

  const statusBreakdown = [
    { name: "Success", value: successfulSteps, color: STATUS_COLORS.Success },
    { name: "Failed", value: failedSteps, color: STATUS_COLORS.Failed },
    { name: "Skipped", value: skippedSteps, color: STATUS_COLORS.Skipped },
    { name: "Retried", value: retryCount, color: STATUS_COLORS.Retried },
  ];

  const reflectionBreakdown = normalizeReflectionBreakdown(metrics.reflection_strategies || metrics.reflectionStrategies);
  const failureHistogram = normalizeFailureHistogram(metrics.failure_types || metrics.failureTypes);

  const trends = metrics.trends && typeof metrics.trends === "object" ? metrics.trends : MOCK_METRICS.trends;
  const sparklineRecovery = Array.isArray(trends.recovery) && trends.recovery.length ? trends.recovery : MOCK_METRICS.trends.recovery;

  const costByModel = normalizeCostByModel(metrics, totalCost);
  const providers = normalizeProviderMetrics(rawProviderMetrics || metrics.provider_metrics || metrics.providerMetrics);
  const agentContributions = normalizeAgentContributions(metrics.agent_contributions || metrics.agentContributions || metrics.agents_used || metrics.agentsUsed);

  return {
    keyMetrics: {
      tasksTotal,
      tasksCompleted,
      completionRate,
      recoveryRate,
      avgQuality,
      totalTokens,
      inputTokens,
      outputTokens,
      totalCost,
      completionDelta: pickNumber(metrics, ["success_delta", "completionDelta"], MOCK_METRICS.success_delta),
      recoveryDelta: pickNumber(metrics, ["recovery_delta", "recoveryDelta"], MOCK_METRICS.recovery_delta),
      qualityDelta: pickNumber(metrics, ["quality_delta", "qualityDelta"], MOCK_METRICS.quality_delta),
      tokensDelta: pickNumber(metrics, ["tokens_delta", "tokensDelta"], MOCK_METRICS.tokens_delta),
      costDelta: pickNumber(metrics, ["cost_delta", "costDelta"], MOCK_METRICS.cost_delta),
    },
    sparklineRecovery,
    executionBreakdown,
    statusBreakdown,
    reflectionBreakdown,
    failureHistogram,
    providers,
    costByModel,
    totalStepCount,
    agentContributions,
  };
}

function normalizeExecutionBreakdown(raw) {
  const source = Array.isArray(raw) && raw.length ? raw : MOCK_METRICS.execution_breakdown;
  return source.map((item, index) => {
    if (typeof item !== "object" || item === null) {
      return MOCK_METRICS.execution_breakdown[index % MOCK_METRICS.execution_breakdown.length];
    }

    const statusRaw = String(item.status || item.step_status || "Success").trim();
    const status = statusRaw || "Success";
    return {
      step: String(item.step || item.step_name || item.stepId || `Step ${index + 1}`),
      execution_ms: pickNumber(item, ["execution_ms", "duration_ms", "durationMs"], 0),
      retry_ms: pickNumber(item, ["retry_ms", "retryMs"], 0),
      reflection_ms: pickNumber(item, ["reflection_ms", "reflectionMs"], 0),
      status,
    };
  });
}

function normalizeReflectionBreakdown(raw) {
  const source = raw && typeof raw === "object" && Object.keys(raw).length
    ? raw
    : MOCK_METRICS.reflection_strategies;

  const total = Math.max(1, Object.values(source).reduce((sum, value) => sum + toNumber(value), 0));
  return Object.entries(source).map(([name, value]) => ({
    name,
    value: toNumber(value),
    percent: Number(((toNumber(value) / total) * 100).toFixed(1)),
  }));
}

function normalizeFailureHistogram(raw) {
  const source = raw && typeof raw === "object" && Object.keys(raw).length
    ? raw
    : MOCK_METRICS.failure_types;

  const rows = Object.entries(source).map(([name, value]) => ({
    name,
    count: toNumber(value),
  }));

  const maxCount = Math.max(1, ...rows.map((item) => item.count));
  return rows
    .sort((a, b) => b.count - a.count)
    .map((item) => ({
      ...item,
      color: failureGradient(item.count / maxCount),
    }));
}

function normalizeProviderMetrics(raw) {
  const source = raw && typeof raw === "object" && Object.keys(raw).length
    ? raw
    : MOCK_PROVIDER_METRICS;

  const rows = Object.entries(source).map(([key, item]) => {
    const payload = item && typeof item === "object" ? item : {};
    const calls = pickNumber(payload, ["calls"], 0);
    const failures = pickNumber(payload, ["failures"], 0);
    const failureRate = calls > 0 ? (failures / calls) * 100 : 0;

    return {
      key,
      displayName: String(payload.display_name || payload.provider || prettifyProviderName(key)),
      calls,
      failures,
      avgLatency: pickNumber(payload, ["avg_latency", "avgLatency"], 0),
      failureRate,
      circuitState: String(payload.circuit_state || payload.circuitState || "closed"),
      latencyTrend: Array.isArray(payload.latency_trend) && payload.latency_trend.length
        ? payload.latency_trend.map((value) => toNumber(value))
        : syntheticLatencyTrend(pickNumber(payload, ["avg_latency", "avgLatency"], 1200)),
      sparkColor: sparkColorForState(String(payload.circuit_state || payload.circuitState || "closed")),
    };
  });

  if (!rows.length) {
    return normalizeProviderMetrics(MOCK_PROVIDER_METRICS);
  }

  return rows;
}

function normalizeCostByModel(metrics, totalCost) {
  const raw = metrics.cost_by_model || metrics.costByModel;
  if (raw && typeof raw === "object" && Object.keys(raw).length) {
    return {
      "GPT-4o": toNumber(raw["GPT-4o"] || raw.gpt4o),
      "GPT-4o-mini": toNumber(raw["GPT-4o-mini"] || raw.gpt4omini),
      "Claude 3.5": toNumber(raw["Claude 3.5"] || raw.claude35 || raw.claude),
    };
  }

  const modelUsageRaw = metrics.models_used || metrics.modelsUsed;
  if (modelUsageRaw && typeof modelUsageRaw === "object" && Object.keys(modelUsageRaw).length) {
    const usage = Object.entries(modelUsageRaw).map(([name, count]) => ({
      name: normalizeModelName(name),
      count: Math.max(0, toNumber(count)),
    }));
    const totalUsage = Math.max(1, usage.reduce((sum, item) => sum + item.count, 0));

    const costByModel = {
      "GPT-4o": 0,
      "GPT-4o-mini": 0,
      "Claude 3.5": 0,
    };

    for (const item of usage) {
      if (!costByModel[item.name] && item.name !== "GPT-4o" && item.name !== "GPT-4o-mini" && item.name !== "Claude 3.5") {
        continue;
      }
      costByModel[item.name] += (item.count / totalUsage) * totalCost;
    }

    return costByModel;
  }

  return {
    ...MOCK_METRICS.cost_by_model,
    "GPT-4o": (totalCost * 0.58),
    "GPT-4o-mini": (totalCost * 0.18),
    "Claude 3.5": (totalCost * 0.24),
  };
}

function normalizeAgentContributions(raw) {
  const source = raw && typeof raw === "object" && Object.keys(raw).length
    ? raw
    : MOCK_METRICS.agent_contributions;

  const rows = Object.entries(source).map(([name, item]) => {
    if (typeof item === "number") {
      return {
        name,
        stepsHandled: item,
        avgQuality: 6.8,
        tokensUsed: 0,
      };
    }

    const payload = item && typeof item === "object" ? item : {};
    return {
      name,
      stepsHandled: pickNumber(payload, ["steps_handled", "stepsHandled"], 0),
      avgQuality: clamp(pickNumber(payload, ["avg_quality", "avgQuality"], 6.8), 0, 10),
      tokensUsed: pickNumber(payload, ["tokens_used", "total_tokens", "tokensUsed"], 0),
    };
  });

  return rows.filter((item) => item.stepsHandled > 0);
}

function useCountUp(targetValue, duration = 900, decimals = 0) {
  const [value, setValue] = useState(toNumber(targetValue));
  const previousRef = useRef(toNumber(targetValue));

  useEffect(() => {
    const from = previousRef.current;
    const to = toNumber(targetValue);

    if (from === to) {
      setValue(to);
      return undefined;
    }

    const startedAt = performance.now();
    let frame;

    const tick = (now) => {
      const progress = clamp((now - startedAt) / duration, 0, 1);
      const eased = 1 - (1 - progress) ** 3;
      const next = from + (to - from) * eased;
      setValue(Number(next.toFixed(decimals)));

      if (progress < 1) {
        frame = requestAnimationFrame(tick);
      } else {
        previousRef.current = to;
      }
    };

    frame = requestAnimationFrame(tick);

    return () => {
      if (frame) {
        cancelAnimationFrame(frame);
      }
    };
  }, [decimals, duration, targetValue]);

  return value;
}

function pickNumber(source, keys, fallback = 0) {
  for (const key of keys) {
    if (source && Object.prototype.hasOwnProperty.call(source, key)) {
      const numeric = toNumber(source[key]);
      if (Number.isFinite(numeric)) {
        return numeric;
      }
    }
  }
  return toNumber(fallback);
}

function toNumber(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
}

function ratioToPercent(value) {
  const numeric = toNumber(value);
  if (numeric <= 1) {
    return numeric * 100;
  }
  return numeric;
}

function qualityColor(quality) {
  if (quality >= 8) {
    return "#22c55e";
  }
  if (quality >= 6.5) {
    return "#f59e0b";
  }
  return "#ef4444";
}

function failureGradient(intensityRatio) {
  if (intensityRatio >= 0.8) {
    return "#ef4444";
  }
  if (intensityRatio >= 0.55) {
    return "#f97316";
  }
  if (intensityRatio >= 0.3) {
    return "#f59e0b";
  }
  return "#94a3b8";
}

function sparkColorForState(state) {
  const normalized = String(state || "").toLowerCase();
  if (normalized === "open") {
    return "#ef4444";
  }
  if (normalized === "half_open") {
    return "#f59e0b";
  }
  return "#22c55e";
}

function syntheticLatencyTrend(avg) {
  const baseline = Math.max(300, toNumber(avg));
  return [
    baseline * 1.08,
    baseline * 1.03,
    baseline * 0.97,
    baseline * 1.01,
    baseline * 0.95,
    baseline * 1.04,
    baseline,
  ].map((item) => Math.round(item));
}

function normalizeModelName(name) {
  const normalized = String(name || "").toLowerCase();
  if (normalized.includes("mini")) {
    return "GPT-4o-mini";
  }
  if (normalized.includes("claude")) {
    return "Claude 3.5";
  }
  return "GPT-4o";
}

function prettifyProviderName(key) {
  const normalized = String(key || "").toLowerCase();
  if (normalized.includes("mini")) {
    return "OpenAI GPT-4o-mini";
  }
  if (normalized.includes("claude") || normalized.includes("anthropic")) {
    return "Claude 3.5";
  }
  return "OpenAI GPT-4o";
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function formatNumber(value) {
  return Math.round(toNumber(value)).toLocaleString();
}

function formatMilliseconds(value) {
  const ms = Math.max(0, toNumber(value));
  if (ms < 1000) {
    return `${ms}ms`;
  }
  return `${(ms / 1000).toFixed(2)}s`;
}
