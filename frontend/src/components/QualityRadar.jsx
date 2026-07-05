import { useMemo } from "react";
import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";

const DIMENSIONS = ["Relevance", "Completeness", "Consistency", "Plausibility"];

const DEFAULT_CURRENT = {
  name: "Current Step",
  relevance: 7.8,
  completeness: 7.1,
  consistency: 7.4,
  plausibility: 8.2,
};

const DEFAULT_COMPARISONS = [
  {
    name: "Average",
    relevance: 7.2,
    completeness: 6.8,
    consistency: 7.0,
    plausibility: 7.5,
  },
];

const SERIES_COLORS = [
  { stroke: "#22c55e", fill: "rgba(34,197,94,0.24)" },
  { stroke: "#3b82f6", fill: "rgba(59,130,246,0.18)" },
  { stroke: "#a855f7", fill: "rgba(168,85,247,0.16)" },
  { stroke: "#f59e0b", fill: "rgba(245,158,11,0.14)" },
  { stroke: "#ef4444", fill: "rgba(239,68,68,0.14)" },
];

export default function QualityRadar({
  current = null,
  comparisons = null,
  title = "Quality Radar",
  subtitle = "Validator quality dimensions on a 0-10 scale",
}) {
  const normalizedCurrent = useMemo(
    () => normalizeSeries(current || DEFAULT_CURRENT),
    [current],
  );

  const normalizedComparisons = useMemo(() => {
    if (!Array.isArray(comparisons) || comparisons.length === 0) {
      return DEFAULT_COMPARISONS.map((entry) => normalizeSeries(entry));
    }
    return comparisons.map((entry, index) => normalizeSeries({ name: entry?.name || `Series ${index + 1}`, ...entry }));
  }, [comparisons]);

  const chartData = useMemo(() => {
    return DIMENSIONS.map((dimension) => {
      const key = dimension.toLowerCase();
      const base = { dimension };
      base[normalizedCurrent.name] = toScore(normalizedCurrent[key]);

      for (const series of normalizedComparisons) {
        base[series.name] = toScore(series[key]);
      }
      return base;
    });
  }, [normalizedComparisons, normalizedCurrent]);

  const allSeries = [normalizedCurrent, ...normalizedComparisons];

  return (
    <section className="rounded-2xl border border-white/10 bg-[linear-gradient(145deg,rgba(15,23,42,0.78),rgba(30,41,59,0.56))] p-4 sm:p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-base font-semibold text-[var(--text-primary)]">{title}</h3>
          <p className="text-xs text-[var(--text-secondary)]">{subtitle}</p>
        </div>
      </div>

      <div className="h-[320px] rounded-xl border border-white/10 bg-[#0a162f]/90 p-2">
        <ResponsiveContainer width="100%" height="100%">
          <RadarChart data={chartData} outerRadius="74%">
            <PolarGrid stroke="rgba(148,163,184,0.28)" />
            <PolarAngleAxis dataKey="dimension" tick={{ fill: "#cbd5e1", fontSize: 11 }} />
            <PolarRadiusAxis domain={[0, 10]} tickCount={6} tick={{ fill: "#94a3b8", fontSize: 10 }} />
            <Tooltip content={<RadarTooltip />} />
            {allSeries.map((series, index) => {
              const colorMeta = SERIES_COLORS[index % SERIES_COLORS.length];
              return (
                <Radar
                  key={series.name}
                  name={series.name}
                  dataKey={series.name}
                  stroke={colorMeta.stroke}
                  fill={colorMeta.fill}
                  fillOpacity={index === 0 ? 0.5 : 0.35}
                  strokeWidth={index === 0 ? 2.4 : 1.8}
                  dot={{ r: 2 }}
                />
              );
            })}
          </RadarChart>
        </ResponsiveContainer>
      </div>

      <div className="mt-3 flex flex-wrap gap-2 text-xs text-[var(--text-secondary)]">
        {allSeries.map((series, index) => {
          const colorMeta = SERIES_COLORS[index % SERIES_COLORS.length];
          return (
            <span key={series.name} className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/5 px-2.5 py-1">
              <span className="h-2.5 w-2.5 rounded-full" style={{ background: colorMeta.stroke }} />
              {series.name}
            </span>
          );
        })}
      </div>
    </section>
  );
}

function RadarTooltip({ active, payload, label }) {
  if (!active || !payload || !payload.length) {
    return null;
  }

  return (
    <div className="rounded-lg border border-white/15 bg-[#060d1f] px-3 py-2 text-xs text-slate-100 shadow-xl">
      <p className="mb-1 font-semibold text-slate-100">{label}</p>
      <div className="space-y-0.5 text-slate-300">
        {payload.map((entry) => (
          <p key={entry.name}>
            {entry.name}: {toScore(entry.value).toFixed(1)}/10
          </p>
        ))}
      </div>
    </div>
  );
}

function normalizeSeries(input) {
  const source = input && typeof input === "object" ? input : DEFAULT_CURRENT;
  return {
    name: String(source.name || "Series"),
    relevance: toScore(source.relevance),
    completeness: toScore(source.completeness),
    consistency: toScore(source.consistency),
    plausibility: toScore(source.plausibility),
  };
}

function toScore(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return 0;
  }
  return Math.max(0, Math.min(10, numeric));
}
