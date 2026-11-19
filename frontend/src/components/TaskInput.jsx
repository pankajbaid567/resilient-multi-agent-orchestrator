import { useMemo, useState } from "react";
import { motion } from "framer-motion";

const MAX_CHARS = 2000;

// Examples that work without live data (the public demo has no web search).
const EXAMPLE_TASKS = [
  "Compare PostgreSQL and MongoDB for an online store's order system, then recommend one.",
  "Plan a two-week study schedule for a DSA-heavy internship interview.",
  "Explain how HTTPS keeps a connection secure, step by step, for a beginner.",
  "Write a short, polite email asking a professor for a recommendation letter.",
];

export default function TaskInput({ onSubmit, isLoading = false, onTaskCreated }) {
  const [taskText, setTaskText] = useState("");
  const [reviewPlan, setReviewPlan] = useState(true);
  const [error, setError] = useState("");

  const submitHandler = onSubmit || onTaskCreated;
  const trimmedTask = taskText.trim();
  const isDisabled = isLoading || trimmedTask.length === 0;

  const characterCountTone = useMemo(() => {
    if (taskText.length > MAX_CHARS) {
      return "text-[var(--accent-error)]";
    }
    if (taskText.length > MAX_CHARS * 0.85) {
      return "text-[var(--accent-warning)]";
    }
    return "text-[var(--text-secondary)]";
  }, [taskText.length]);

  const handleSubmit = async (event) => {
    event.preventDefault();
    setError("");

    if (!trimmedTask) {
      setError("Task description cannot be empty.");
      return;
    }

    if (trimmedTask.length > MAX_CHARS) {
      setError(`Task description must be ${MAX_CHARS} characters or fewer.`);
      return;
    }

    if (typeof submitHandler !== "function") {
      setError("Submit handler is not configured.");
      return;
    }

    const response = await Promise.resolve(submitHandler(trimmedTask, { autoRun: !reviewPlan }));
    if (response?.success === false) {
      setError(response.error || "Failed to submit task.");
      return;
    }

    setTaskText("");
  };

  const applyExample = (example) => {
    if (isLoading) {
      return;
    }
    setTaskText(example);
    setError("");
  };

  return (
    <form className="space-y-4" onSubmit={handleSubmit}>
      <div>
        <label className="mb-2 block text-sm font-medium text-[var(--text-primary)]" htmlFor="task-input">
          What should the agents do?
        </label>
        <div className="glass rounded-2xl border border-white/10 bg-[var(--bg-card)]/70 p-3">
          <textarea
            id="task-input"
            className="h-36 w-full resize-none rounded-xl border border-white/10 bg-[#0e1733]/80 px-4 py-3 text-sm text-[var(--text-primary)] outline-none transition focus:border-[var(--accent-secondary)] focus:ring-2 focus:ring-[var(--accent-secondary)]/30"
            maxLength={MAX_CHARS}
            placeholder="Describe a task, e.g. 'Compare renting and buying a flat in Pune for a 26-year-old engineer and recommend one.'"
            value={taskText}
            onChange={(event) => setTaskText(event.target.value)}
          />

          <div className="mt-2 flex items-center justify-between">
            <p className={`text-xs ${characterCountTone}`}>
              {taskText.length}/{MAX_CHARS}
            </p>
            {error ? <p className="text-xs text-[var(--accent-error)]">{error}</p> : null}
          </div>
        </div>
      </div>

      <div>
        <p className="mb-2 text-xs uppercase tracking-wide text-[var(--text-secondary)]">Try an example</p>
        <div className="flex flex-wrap gap-2">
          {EXAMPLE_TASKS.map((task) => (
            <button
              key={task}
              type="button"
              onClick={() => applyExample(task)}
              className="rounded-full border border-white/15 bg-white/5 px-3 py-1.5 text-xs text-[var(--text-secondary)] transition hover:border-[var(--accent-info)]/60 hover:bg-[var(--accent-info)]/10 hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-60"
              disabled={isLoading}
            >
              {task}
            </button>
          ))}
        </div>
      </div>

      <label className="flex cursor-pointer items-center gap-2 text-xs text-[var(--text-secondary)]">
        <input
          type="checkbox"
          checked={reviewPlan}
          onChange={(event) => setReviewPlan(event.target.checked)}
          className="h-4 w-4 rounded border-white/30 bg-transparent accent-sky-400"
        />
        Show me the plan before it runs
      </label>

      <motion.button
        type="submit"
        disabled={isDisabled}
        whileHover={isDisabled ? {} : { scale: 1.01, y: -1 }}
        whileTap={isDisabled ? {} : { scale: 0.985 }}
        className="inline-flex items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-[var(--accent-primary)] to-[var(--accent-secondary)] px-5 py-2.5 text-sm font-semibold text-white shadow-lg shadow-[var(--accent-primary)]/30 transition disabled:cursor-not-allowed disabled:from-slate-600 disabled:to-slate-700 disabled:opacity-60"
      >
        {isLoading ? (
          <>
            <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.3" strokeWidth="2" />
              <path d="M12 3a9 9 0 0 1 9 9" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
            Planning...
          </>
        ) : (
          reviewPlan ? "Plan task" : "Plan and run"
        )}
      </motion.button>
    </form>
  );
}
