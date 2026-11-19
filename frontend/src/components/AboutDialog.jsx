import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef } from "react";

const REPO = "https://github.com/pankajbaid567/resilient-multi-agent-orchestrator";

export default function AboutDialog({ open, onClose }) {
  const closeRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    closeRef.current?.focus();
    const onKey = (event) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open ? (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={onClose}
        >
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-labelledby="about-title"
            className="max-h-[85vh] w-full max-w-xl overflow-y-auto rounded-2xl border border-white/15 bg-[#0b1430] p-5 text-sm text-[var(--text-secondary)] shadow-2xl"
            initial={{ y: 12, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: 8, opacity: 0 }}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-4">
              <h2 id="about-title" className="text-lg font-semibold text-[var(--text-primary)]">About this demo</h2>
              <button
                ref={closeRef}
                type="button"
                onClick={onClose}
                className="rounded-md border border-white/15 px-2 py-1 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
              >
                Close
              </button>
            </div>

            <p className="mt-3">
              A multi-step task runner built on <strong className="text-[var(--text-primary)]">LangGraph</strong>.
              You describe a task; a planner splits it into steps; a supervisor hands each step to a specialist
              agent; steps without dependencies run in parallel; a reviewer model checks every answer, and failing
              steps are retried with its feedback or rewritten by a reflector. A finalizer combines the results.
            </p>

            <h3 className="mt-4 font-semibold text-[var(--text-primary)]">Reliability</h3>
            <ul className="mt-1 list-disc space-y-1 pl-5">
              <li>Every model call goes through retries with backoff, a fallback chain of models and circuit breakers.</li>
              <li>Run state is checkpointed after every wave of steps, so an interrupted run resumes.</li>
              <li>Each run has a token budget and a deadline.</li>
            </ul>

            <h3 className="mt-4 font-semibold text-[var(--text-primary)]">Where it runs</h3>
            <p className="mt-1">
              UI on Cloudflare Pages; API on AWS EC2 in Mumbai calling Amazon Bedrock models with the server's
              IAM role (no stored API keys). A typical run costs well under one US cent in model tokens.
            </p>

            <h3 className="mt-4 font-semibold text-[var(--text-primary)]">What has been measured</h3>
            <p className="mt-1">
              On 40 tasks, AI-graded, the full system and a single model call tie on answer quality; the full system
              costs about 3x. Under simulated provider failures and outages, the full system keeps answering where a
              single call fails. Details, raw results and limits are in the repository.
            </p>

            <a
              href={REPO}
              target="_blank"
              rel="noreferrer"
              className="mt-5 inline-flex rounded-lg border border-sky-400/40 bg-sky-500/15 px-3 py-2 text-xs font-medium text-sky-100 hover:bg-sky-500/25"
            >
              Source, README and eval results on GitHub
            </a>
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
