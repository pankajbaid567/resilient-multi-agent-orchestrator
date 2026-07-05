import { useMemo } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const MODEL_KEYS = {
  "GPT-4o": "gpt4o",
  "GPT-4o-mini": "gpt4omini",
  "Claude 3.5": "claude35",
};

const MODEL_COLORS = {
  gpt4o: "#3b82f6",
  gpt4omini: "#22c55e",
  claude35: "#8b5cf6",
};

const DEFAULT_STEPS = [
  { step: "Plan", model: "GPT-4o-mini", input_tokens: 1200, output_tokens: 340, cost_usd: 0.0004 },
  { step: "Research", model: "GPT-4o", input_tokens: 6800, output_tokens: 1820, cost_usd: 0.0401 },
  { step: "Analyze", model: "GPT-4o", input_tokens: 5100, output_tokens: 1460, cost_usd: 0.0306 },
  { step: "Fallback Draft", model: "Claude 3.5", input_tokens: 4200, output_tokens: 2100, cost_usd: 0.0462 },
  { step: "Refine", model: "GPT-4o-mini", input_tokens: 2400, output_tokens: 720, cost_usd: 0.0008 },
  { step: "Final", model: "GPT-4o", input_tokens: 3300, output_tokens: 1100, cost_usd: 0.0207 },
];

export default function CostBreakdown({
  steps = null,
  title = "Cost Breakdown",
  subtitle = "Cumulative token usage and spend by model",
}) {
  const normalized = useMemo(
    () => normalizeCostData(Array.isArray(steps) && steps.length ? steps : DEFAULT_STEPS),
    [steps],
  );

  return (
    <section className="rounded-2xl border border-white/10 bg-[linear-gradient(145deg,rgba(15,23,42,0.78),rgba(30,41,59,0.56))] p-4 sm:p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-base font-semibold text-[var(--text-primary)]">{title}</h3>
          <p className="text-xs text-[var(--text-secondary)]">{subtitle}</p>
        </div>
        <div className="rounded-full border border-white/15 bg-white/5 px-3 py-1 text-xs text-[var(--text-secondary)]">
          Total cost: ${normalized.totalCost.toFixed(2)}
        </div>
      </div>

      <div className="h-[360px] rounded-xl border border-white/10 bg-[#0a162f]/90 p-2">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={normalized.rows} margin={{ top: 12, right: 16, left: 4, bottom: 10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.15)" />
            <XAxis dataKey="step" tick={{ fill: "#cbd5e1", fontSize: 11 }} axisLine={{ stroke: "rgba(148,163,184,0.42)" }} tickLine={{ stroke: "rgba(148,163,184,0.42)" }} />
            <YAxis yAxisId="tokens" tick={{ fill: "#cbd5e1", fontSize: 11 }} axisLine={{ stroke: "rgba(148,163,184,0.42)" }} tickLine={{ stroke: "rgba(148,163,184,0.42)" }} />
            <YAxis yAxisId="cost" orientation="right" tick={{ fill: "#f59e0b", fontSize: 11 }} axisLine={{ stroke: "rgba(245,158,11,0.4)" }} tickLine={{ stroke: "rgba(245,158,11,0.4)" }} tickFormatter={(value) => `$${Number(value).toFixed(2)}`} />
            <Tooltip content={<CostTooltip />} />
            <Legend wrapperStyle={{ fontSize: 11, color: "#cbd5e1" }} />

            <Area yAxisId="tokens" type="monotone" dataKey="gpt4o" name="GPT-4o Tokens" stackId="models" stroke={MODEL_COLORS.gpt4o} fill="rgba(59,130,246,0.35)" strokeWidth={1.5} />
            <Area yAxisId="tokens" type="monotone" dataKey="gpt4omini" name="GPT-4o-mini Tokens" stackId="models" stroke={MODEL_COLORS.gpt4omini} fill="rgba(34,197,94,0.30)" strokeWidth={1.5} />
            <Area yAxisId="tokens" type="monotone" dataKey="claude35" name="Claude 3.5 Tokens" stackId="models" stroke={MODEL_COLORS.claude35} fill="rgba(139,92,246,0.30)" strokeWidth={1.5} />

            <Line yAxisId="tokens" type="monotone" dataKey="cumulativeInput" name="Input Tokens" stroke="#38bdf8" strokeWidth={2} dot={false} strokeDasharray="6 4" />
            <Line yAxisId="tokens" type="monotone" dataKey="cumulativeOutput" name="Output Tokens" stroke="#f472b6" strokeWidth={2} dot={false} strokeDasharray="2 4" />
            <Line yAxisId="cost" type="monotone" dataKey="cumulativeCost" name="Cumulative Cost" stroke="#f59e0b" strokeWidth={2.2} dot={{ r: 2 }} />

            {normalized.fallbackMarkers.map((marker) => (
              <ReferenceDot
                key={`${marker.step}-${marker.model}`}
                yAxisId="cost"
                x={marker.step}
                y={marker.cumulativeCost}
                r={5}
                fill="#ef4444"
                stroke="#fee2e2"
                label={{
                  value: `Fallback -> ${marker.model}`,
                  fill: "#fecaca",
                  fontSize: 10,
                  position: "top",
                }}
              />
            ))}
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <div className="mt-3 flex flex-wrap gap-2 text-xs text-[var(--text-secondary)]">
        <LegendChip label="GPT-4o" color={MODEL_COLORS.gpt4o} />
        <LegendChip label="GPT-4o-mini" color={MODEL_COLORS.gpt4omini} />
        <LegendChip label="Claude 3.5" color={MODEL_COLORS.claude35} />
        <LegendChip label="Input Tokens" color="#38bdf8" />
        <LegendChip label="Output Tokens" color="#f472b6" />
        <LegendChip label="Cost (USD)" color="#f59e0b" />
      </div>
    </section>
  );
}

function CostTooltip({ active, payload, label }) {
  if (!active || !payload || !payload.length) {
    return null;
  }

  const row = payload[0]?.payload;
  if (!row) {
    return null;
  }

  return (
    <div className="rounded-lg border border-white/15 bg-[#060d1f] px-3 py-2 text-xs text-slate-100 shadow-xl">
      <p className="mb-1 font-semibold text-slate-100">{label}</p>
      <div className="space-y-0.5 text-slate-300">
        <p>Model: {row.model}</p>
        <p>Step input: {formatNumber(row.stepInput)} tokens</p>
        <p>Step output: {formatNumber(row.stepOutput)} tokens</p>
        <p>Cumulative input: {formatNumber(row.cumulativeInput)}</p>
        <p>Cumulative output: {formatNumber(row.cumulativeOutput)}</p>
        <p>Cumulative cost: ${row.cumulativeCost.toFixed(4)}</p>
      </div>
    </div>
  );
}

function LegendChip({ label, color }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/5 px-2.5 py-1">
      <span className="h-2.5 w-2.5 rounded-full" style={{ background: color }} />
      {label}
    </span>
  );
}

function normalizeCostData(steps) {
  let cumulativeInput = 0;
  let cumulativeOutput = 0;
  let cumulativeCost = 0;

  const cumulativeByModel = {
    gpt4o: 0,
    gpt4omini: 0,
    claude35: 0,
  };

  const rows = [];
  const fallbackMarkers = [];
  let previousModelKey = "";

  for (const [index, raw] of steps.entries()) {
    const item = raw && typeof raw === "object" ? raw : {};
    const step = String(item.step || item.step_name || `Step ${index + 1}`);
    const model = normalizeModelName(String(item.model || item.model_used || "GPT-4o"));
    const modelKey = MODEL_KEYS[model] || "gpt4o";

    const stepInput = toNumber(item.input_tokens ?? item.tokens_input ?? item.tokensIn);
    const stepOutput = toNumber(item.output_tokens ?? item.tokens_output ?? item.tokensOut);

    const stepCost = toNumber(item.cost_usd ?? item.costUsd ?? estimateCost(model, stepInput, stepOutput));

    cumulativeInput += stepInput;
    cumulativeOutput += stepOutput;
    cumulativeCost += stepCost;

    cumulativeByModel[modelKey] += stepInput + stepOutput;

    if (previousModelKey && previousModelKey !== modelKey) {
      fallbackMarkers.push({
        step,
        model,
        cumulativeCost,
      });
    }

    previousModelKey = modelKey;

    rows.push({
      step,
      model,
      stepInput,
      stepOutput,
      cumulativeInput,
      cumulativeOutput,
      cumulativeCost,
      gpt4o: cumulativeByModel.gpt4o,
      gpt4omini: cumulativeByModel.gpt4omini,
      claude35: cumulativeByModel.claude35,
    });
  }

  return {
    rows,
    fallbackMarkers,
    totalCost: cumulativeCost,
  };
}

function estimateCost(model, inputTokens, outputTokens) {
  const rates = {
    "GPT-4o": { input: 2.5, output: 10.0 },
    "GPT-4o-mini": { input: 0.15, output: 0.6 },
    "Claude 3.5": { input: 3.0, output: 15.0 },
  };

  const modelRates = rates[model] || rates["GPT-4o"];
  return (inputTokens / 1_000_000) * modelRates.input + (outputTokens / 1_000_000) * modelRates.output;
}

function normalizeModelName(model) {
  const normalized = String(model || "").toLowerCase();
  if (normalized.includes("mini")) {
    return "GPT-4o-mini";
  }
  if (normalized.includes("claude")) {
    return "Claude 3.5";
  }
  return "GPT-4o";
}

function formatNumber(value) {
  return Math.round(toNumber(value)).toLocaleString();
}

function toNumber(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
}
