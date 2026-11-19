/**
 * The backend's runtime configuration, read from GET /config. Changing it needs the admin key
 * (POST /config with X-Admin-Key), so the public demo shows it read-only instead of offering
 * toggles the server would refuse.
 */

function Row({ label, value, hint }) {
  return (
    <div className="flex items-start justify-between gap-4 border-t border-white/5 py-3 first:border-t-0">
      <div>
        <p className="text-sm font-medium text-[var(--text-primary)]">{label}</p>
        {hint ? <p className="mt-0.5 text-xs text-[var(--text-secondary)]">{hint}</p> : null}
      </div>
      <p className="shrink-0 text-sm font-semibold text-[var(--text-primary)]">{value}</p>
    </div>
  );
}

const onOff = (value) => (value ? "On" : "Off");

export default function ConfigView({ config }) {
  const settings = config || {};
  const chain = Array.isArray(settings.providers) ? settings.providers : [];

  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <section className="rounded-2xl border border-white/10 bg-[var(--bg-card)]/70 p-4 sm:p-5">
        <h2 className="text-base font-semibold text-[var(--text-primary)]">Execution</h2>
        <p className="mt-0.5 text-xs text-[var(--text-secondary)]">
          Set on the server. Read-only here: changing it needs the admin key.
        </p>
        <div className="mt-3">
          <Row label="Parallel steps" value={onOff(settings.parallel_mode)} hint="Independent steps of a plan run at the same time." />
          <Row label="Specialist agents" value={onOff(settings.multi_agent_mode)} hint="A supervisor assigns each step to a research, code, analysis or writing agent." />
          <Row label="Max retries per step" value={settings.max_retries ?? "—"} hint="After that, a reflector rewrites, splits or skips the step." />
          <Row label="Model call timeout" value={settings.step_timeout ? `${settings.step_timeout} s` : "—"} />
          <Row label="Chaos mode" value={onOff(settings.chaos_mode)} hint="Fault injection for testing recovery. Off on the public demo." />
        </div>
      </section>

      <section className="rounded-2xl border border-white/10 bg-[var(--bg-card)]/70 p-4 sm:p-5">
        <h2 className="text-base font-semibold text-[var(--text-primary)]">Models</h2>
        <p className="mt-0.5 text-xs text-[var(--text-secondary)]">
          Tried in order: if a model fails after retries, the next one takes the call.
        </p>
        <ol className="mt-3 space-y-2">
          {chain.length === 0 ? (
            <li className="text-sm text-[var(--text-secondary)]">Not reported by the server.</li>
          ) : (
            chain.map((entry, index) => (
              <li key={`${entry.model}-${index}`} className="flex items-center gap-3 rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2">
                <span className="inline-flex h-6 w-6 items-center justify-center rounded-full border border-white/15 text-xs text-[var(--text-secondary)]">
                  {index + 1}
                </span>
                <span className="font-mono text-xs text-[var(--text-primary)]">{entry.model}</span>
              </li>
            ))
          )}
        </ol>

        <h3 className="mt-5 text-sm font-semibold text-[var(--text-primary)]">Public demo limits</h3>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-[var(--text-secondary)]">
          <li>5 runs per visitor per hour and 50 runs per day in total.</li>
          <li>Each run has a token budget and a deadline; a run that hits either stops with partial results.</li>
          <li>No web search or code execution on the public demo, so tasks should not need live data.</li>
        </ul>
      </section>
    </div>
  );
}
