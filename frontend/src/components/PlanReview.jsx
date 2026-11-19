import { motion } from "framer-motion";

/**
 * The planner's proposed steps, shown before anything runs. The backend pauses after planning,
 * so the user approves the plan (Run plan) or discards it (Start over).
 */
export default function PlanReview({ steps = [], levels = [], isLoading = false, onRun, onReset }) {
  const waveOf = new Map();
  (Array.isArray(levels) ? levels : []).forEach((level, index) => {
    (level || []).forEach((stepId) => waveOf.set(String(stepId), index));
  });
  const waveCount = Math.max(1, (levels || []).length);
  const nameOf = new Map(steps.map((step, index) => [String(step.step_id), `Step ${index + 1}`]));

  return (
    <section
      aria-labelledby="plan-heading"
      className="rounded-2xl border border-sky-400/30 bg-sky-500/[0.06] p-4 sm:p-5"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="plan-heading" className="text-lg font-semibold text-[var(--text-primary)]">
          Proposed plan
        </h2>
        <p className="text-xs text-[var(--text-secondary)]">
          {steps.length} {steps.length === 1 ? "step" : "steps"} · {waveCount} {waveCount === 1 ? "wave" : "waves"}
        </p>
      </div>
      <p className="mt-1 text-sm text-[var(--text-secondary)]">
        Nothing has run yet. Steps in the same wave run in parallel; each step is checked by a reviewer
        model and retried or repaired if it falls short.
      </p>

      <ol className="mt-4 space-y-2">
        {steps.map((step, index) => {
          const deps = (step.dependencies || []).map((dep) => nameOf.get(String(dep)) || dep);
          const wave = waveOf.has(String(step.step_id)) ? waveOf.get(String(step.step_id)) + 1 : null;
          return (
            <motion.li
              key={step.step_id || index}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: index * 0.04 }}
              className="rounded-xl border border-white/10 bg-[#0c142a]/80 p-3"
            >
              <div className="flex items-start gap-3">
                <span className="mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-sky-300/40 text-xs font-semibold text-sky-100">
                  {index + 1}
                </span>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-[var(--text-primary)]">{step.name}</p>
                  <p className="mt-0.5 line-clamp-2 text-xs text-[var(--text-secondary)]">{step.description}</p>
                  <p className="mt-1.5 flex flex-wrap gap-1.5 text-[11px] text-[var(--text-secondary)]">
                    {wave ? <span className="rounded-full border border-white/10 px-2 py-0.5">Wave {wave}</span> : null}
                    <span className="rounded-full border border-white/10 px-2 py-0.5">
                      {deps.length ? `After ${deps.join(", ")}` : "No dependencies"}
                    </span>
                  </p>
                </div>
              </div>
            </motion.li>
          );
        })}
      </ol>

      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={onRun}
          disabled={isLoading || steps.length === 0}
          className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-[var(--accent-primary)] to-[var(--accent-secondary)] px-5 py-2.5 text-sm font-semibold text-white shadow-lg shadow-[var(--accent-primary)]/25 transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isLoading ? "Starting…" : "Run plan"}
        </button>
        <button
          type="button"
          onClick={onReset}
          disabled={isLoading}
          className="rounded-xl border border-white/15 bg-white/5 px-4 py-2.5 text-sm text-[var(--text-secondary)] transition hover:border-white/30 hover:text-[var(--text-primary)] disabled:opacity-60"
        >
          Start over
        </button>
      </div>
    </section>
  );
}
