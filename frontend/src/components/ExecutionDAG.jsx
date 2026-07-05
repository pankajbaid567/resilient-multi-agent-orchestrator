import { AnimatePresence, motion } from "framer-motion";
import { useMemo } from "react";

import StepCard from "./StepCard";

const PIPELINE_STAGES = ["Planning", "Executing", "Complete"];

export default function ExecutionDAG({ steps = [], results = [], currentStepIndex = 0 }) {
  const safeSteps = Array.isArray(steps) ? steps : [];
  const safeResults = Array.isArray(results) ? results : [];

  const resultByStepId = useMemo(() => {
    const index = new Map();
    for (const result of safeResults) {
      const stepId = String(result?.step_id || "").trim();
      if (!stepId) {
        continue;
      }
      index.set(stepId, result);
    }
    return index;
  }, [safeResults]);

  const executionComplete = useMemo(() => {
    if (safeSteps.length === 0) {
      return false;
    }
    return safeSteps.every((step, index) => {
      const status = resolveStepStatus(step, resultByStepId.get(step?.step_id), index, currentStepIndex, safeSteps.length);
      return isTerminalStatus(status);
    });
  }, [currentStepIndex, resultByStepId, safeSteps]);

  const activeStage = safeSteps.length === 0 ? 0 : executionComplete ? 2 : 1;
  const progressWidth = `${(activeStage / (PIPELINE_STAGES.length - 1)) * 100}%`;

  return (
    <section className="rounded-2xl border border-white/10 bg-[var(--bg-card)]/70 p-4 sm:p-5">
      <h2 className="text-lg font-semibold text-[var(--text-primary)]">Execution Pipeline</h2>
      <p className="mt-1 text-xs text-[var(--text-secondary)]">Planning {"->"} Executing {"->"} Complete</p>

      <div className="mt-4">
        <div className="grid grid-cols-3 gap-2">
          {PIPELINE_STAGES.map((stage, index) => {
            const isReached = index <= activeStage;
            return (
              <div
                key={stage}
                className={`rounded-lg border px-2 py-1.5 text-center text-[11px] font-medium transition ${isReached
                  ? "border-[var(--accent-success)]/50 bg-[var(--accent-success)]/20 text-[var(--text-primary)]"
                  : "border-white/10 bg-white/5 text-[var(--text-secondary)]"}`}
              >
                {stage}
              </div>
            );
          })}
        </div>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/10">
          <motion.div
            className="h-full rounded-full bg-gradient-to-r from-[var(--accent-info)] via-[var(--accent-secondary)] to-[var(--accent-success)]"
            initial={{ width: 0 }}
            animate={{ width: progressWidth }}
            transition={{ duration: 0.45, ease: "easeOut" }}
          />
        </div>
      </div>

      {safeSteps.length === 0 ? (
        <div className="mt-5 rounded-xl border border-white/10 bg-[#0c142a]/80 p-4">
          <p className="mb-3 text-sm text-[var(--text-secondary)]">Waiting for agent to plan...</p>
          <div className="space-y-3 animate-pulse">
            <div className="h-16 rounded-xl bg-white/10" />
            <div className="h-16 rounded-xl bg-white/10" />
            <div className="h-16 rounded-xl bg-white/10" />
          </div>
        </div>
      ) : (
        <div className="mt-5">
          <AnimatePresence initial={false}>
            {safeSteps.map((step, index) => {
              const stepId = String(step?.step_id || `step_${index + 1}`);
              const stepResult = resultByStepId.get(stepId) || null;
              const status = resolveStepStatus(step, stepResult, index, currentStepIndex, safeSteps.length);
              const isCurrent = !executionComplete && index === currentStepIndex;
              const showRetryLoop = hasRetryMarker(step, stepResult, status);
              const showReflection = hasReflectionMarker(step, stepResult, status);
              const connectorColor = connectorColorClass(status, showRetryLoop);

              return (
                <motion.div
                  key={stepId}
                  initial={{ opacity: 0, y: 14 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -8 }}
                  transition={{ duration: 0.28, delay: Math.min(index * 0.08, 0.42) }}
                  className="relative"
                >
                  <div className={`rounded-2xl transition ${isCurrent
                    ? "ring-1 ring-[var(--accent-info)]/60 shadow-[0_0_30px_rgba(59,130,246,0.22)]"
                    : ""}`}
                  >
                    <StepCard
                      step={step}
                      result={stepResult}
                      index={index}
                      isActive={isCurrent}
                    />
                  </div>

                  {index < safeSteps.length - 1 ? (
                    <div className="relative ml-4 mt-1 h-6">
                      <div className={`h-full w-0.5 rounded-full ${connectorColor}`} />

                      {showRetryLoop ? (
                        <div className="absolute left-2 top-1 flex items-center gap-1 rounded-full border border-amber-400/30 bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-200">
                          <CurvedArrowIcon />
                          retry
                        </div>
                      ) : null}

                      {showReflection ? (
                        <div className="absolute left-20 top-1 flex items-center gap-1 rounded-full border border-violet-400/30 bg-violet-500/15 px-1.5 py-0.5 text-[10px] text-violet-200">
                          <BranchIcon />
                          reflection
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                </motion.div>
              );
            })}
          </AnimatePresence>
        </div>
      )}
    </section>
  );
}

function resolveStepStatus(step, result, index, currentStepIndex, totalSteps) {
  const raw = String(result?.status || step?.status || "").toLowerCase();
  if (raw) {
    return raw;
  }

  if (index > Math.max(totalSteps - 1, 0)) {
    return "pending";
  }
  if (index < currentStepIndex) {
    return "success";
  }
  if (index === currentStepIndex) {
    return "running";
  }
  return "pending";
}

function isTerminalStatus(status) {
  return status === "success" || status === "failed" || status === "skipped" || status === "completed";
}

function hasRetryMarker(step, result, status) {
  const retryCount = Number(result?.retry_count || step?.retry_count || 0);
  if (status === "retrying") {
    return true;
  }
  return retryCount > 0;
}

function hasReflectionMarker(step, result, status) {
  if (status === "reflecting") {
    return true;
  }
  const validation = String(result?.validation || step?.validation || "").toLowerCase();
  return status === "skipped" || validation === "reflect" || Boolean(step?.was_reflected || result?.was_reflected);
}

function connectorColorClass(status, wasRetried) {
  if (status === "retrying" || wasRetried) {
    return "bg-amber-400/80";
  }
  if (status === "success" || status === "completed" || status === "skipped") {
    return "bg-emerald-400/80";
  }
  return "bg-slate-500/60";
}

function CurvedArrowIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M9 7H5v4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M5 11a7 7 0 1 0 2-5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function BranchIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M6 4v7m0 0a3 3 0 1 0 0 6m0-6h8m0 0a3 3 0 1 0 0-6m0 6v7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
