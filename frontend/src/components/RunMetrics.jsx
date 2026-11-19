import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

/**
 * Metrics for the current run, computed by the backend from the provider-reported usage of
 * every model call (tokens, latency, model, success). Nothing here is estimated or sampled:
 * a value the run does not have is shown as "—", never filled with example data.
 */

const NODE_LABEL = {
  planner: "Planner",
  execute: "Agents (incl. supervisor)",
  validate: "Reviewer",
  reflect: "Reflector",
  finalizer: "Finalizer",
};

function fmtNum(value) {
  return value === null || value === undefined || Number.isNaN(Number(value)) ? "—" : Number(value).toLocaleString();
}

function fmtMs(value) {
  const v = Number(value || 0);
  if (!v) return "—";
  return v < 1000 ? `${Math.round(v)} ms` : `${(v / 1000).toFixed(1)} s`;
}

function fmtCost(value) {
  if (value === null || value === undefined) return "not priced";
  const v = Number(value);
  return v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`;
}

function Tile({ label, value, hint }) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2.5">
      <p className="text-[11px] uppercase tracking-wide text-[var(--text-secondary)]">{label}</p>
      <p className="mt-1 text-lg font-semibold text-[var(--text-primary)]">{value}</p>
      {hint ? <p className="mt-0.5 text-[11px] text-[var(--text-secondary)]">{hint}</p> : null}
    </div>
  );
}

function Panel({ title, subtitle, children }) {
  return (
    <section className="rounded-2xl border border-white/10 bg-[var(--bg-card)]/70 p-4 sm:p-5">
      <h3 className="text-base font-semibold text-[var(--text-primary)]">{title}</h3>
      {subtitle ? <p className="mt-0.5 text-xs text-[var(--text-secondary)]">{subtitle}</p> : null}
      <div className="mt-3">{children}</div>
    </section>
  );
}

export default function RunMetrics({ task, aggregateMetrics }) {
  const summary = task?.final_output?.summary;
  const usage = summary?.llm_usage;

  if (!usage) {
    return (
      <Panel title="Run metrics" subtitle="Model usage, latency and cost for the current run.">
        <p className="rounded-xl border border-white/10 bg-[#0c142a]/80 p-4 text-sm text-[var(--text-secondary)]">
          Metrics appear when a run finishes. Run a task on the Run tab.
        </p>
        <ServiceRow aggregateMetrics={aggregateMetrics} />
      </Panel>
    );
  }

  const byNode = Object.entries(usage.by_node || {}).map(([node, entry]) => ({
    node: NODE_LABEL[node] || node,
    calls: entry.calls,
    failed: entry.failed,
    tokens: entry.tokens,
  }));
  const byModel = Object.entries(usage.by_model || {});
  const results = new Map((task?.step_results || []).map((result) => [result.step_id, result]));
  const steps = task?.steps || [];
  const retries = Object.values(task?.retry_counts || {}).reduce((a, b) => a + Number(b || 0), 0);
  const reflections = Object.values(task?.reflection_counts || {}).reduce((a, b) => a + Number(b || 0), 0);

  return (
    <div className="space-y-5">
      <Panel
        title="Run metrics"
        subtitle="From the provider-reported usage of every model call in this run."
      >
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
          <Tile label="Wall time" value={fmtMs(summary.total_duration_ms)} />
          <Tile label="Model calls" value={fmtNum(usage.calls)} hint={`${fmtNum(usage.failed_calls)} failed`} />
          <Tile label="Input tokens" value={fmtNum(usage.input_tokens)} />
          <Tile label="Output tokens" value={fmtNum(usage.output_tokens)} />
          <Tile label="Cost" value={fmtCost(usage.cost_usd)} hint="tokens × model price" />
          <Tile label="Latency p50 / p95" value={`${fmtMs(usage.latency_ms?.p50)} / ${fmtMs(usage.latency_ms?.p95)}`} />
          <Tile label="Output speed" value={usage.output_tokens_per_second ? `${usage.output_tokens_per_second} tok/s` : "—"} />
          <Tile label="Recovery" value={`${retries} retries`} hint={`${reflections} reflections · ${fmtNum(usage.fallbacks)} fallbacks`} />
        </div>
      </Panel>

      <div className="grid gap-5 xl:grid-cols-2">
        <Panel title="Model calls by role" subtitle="Where the run spent its calls and tokens.">
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={byNode} layout="vertical" margin={{ left: 24, right: 16 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.15)" horizontal={false} />
                <XAxis type="number" allowDecimals={false} tick={{ fill: "#94a3b8", fontSize: 11 }} />
                <YAxis type="category" dataKey="node" width={150} tick={{ fill: "#cbd5e1", fontSize: 11 }} />
                <Tooltip
                  contentStyle={{ background: "#0b1733", border: "1px solid rgba(255,255,255,0.12)", borderRadius: 10 }}
                  formatter={(value, name) => [fmtNum(value), name]}
                />
                <Bar dataKey="calls" name="Calls" fill="#38bdf8" radius={[0, 6, 6, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>

        <Panel title="Models" subtitle="Calls and tokens per model (the fallback chain is tried in order).">
          <table className="w-full text-left text-sm">
            <thead className="text-[11px] uppercase tracking-wide text-[var(--text-secondary)]">
              <tr><th className="py-1.5 font-medium">Model</th><th className="font-medium">Calls</th><th className="font-medium">Failed</th><th className="font-medium">Tokens</th></tr>
            </thead>
            <tbody>
              {byModel.map(([model, entry]) => (
                <tr key={model} className="border-t border-white/5 text-[var(--text-primary)]">
                  <td className="py-2 pr-2 font-mono text-xs">{model}</td>
                  <td>{fmtNum(entry.calls)}</td>
                  <td>{fmtNum(entry.failed)}</td>
                  <td>{fmtNum(entry.tokens)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      </div>

      <Panel title="Steps" subtitle="Each step's final result after review, retries and reflection.">
        <div tabIndex={0} role="region" aria-label="Steps table" className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead className="text-[11px] uppercase tracking-wide text-[var(--text-secondary)]">
              <tr>
                <th className="py-1.5 font-medium">Step</th><th className="font-medium">Agent</th><th className="font-medium">Status</th>
                <th className="font-medium">Retries</th><th className="font-medium">Tokens</th><th className="font-medium">Time</th>
              </tr>
            </thead>
            <tbody>
              {steps.map((step, index) => {
                const result = results.get(step.step_id);
                return (
                  <tr key={step.step_id} className="border-t border-white/5 text-[var(--text-primary)]">
                    <td className="py-2 pr-3">{index + 1}. {step.name}</td>
                    <td className="pr-3 text-[var(--text-secondary)]">{result?.agent_name || "—"}</td>
                    <td className="pr-3">{result?.status || "not run"}</td>
                    <td className="pr-3">{fmtNum(task?.retry_counts?.[step.step_id] || 0)}</td>
                    <td className="pr-3">{fmtNum(result?.tokens_used)}</td>
                    <td>{fmtMs(result?.latency_ms)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="This server" subtitle="Totals since the backend process started (resets on restart).">
        <ServiceRow aggregateMetrics={aggregateMetrics} />
      </Panel>
    </div>
  );
}

function ServiceRow({ aggregateMetrics }) {
  const agg = aggregateMetrics || {};
  if (!agg || agg.total_tasks === undefined) return null;
  return (
    <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-4">
      <Tile label="Runs" value={fmtNum(agg.total_tasks)} />
      <Tile label="Completed" value={fmtNum(agg.completed_tasks)} />
      <Tile label="Tokens" value={fmtNum(agg.total_tokens_consumed)} />
      <Tile label="Cost" value={fmtCost(agg.total_cost_usd)} />
    </div>
  );
}
