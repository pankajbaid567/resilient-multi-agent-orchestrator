import { AnimatePresence, motion } from "framer-motion";
import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";

import AboutDialog from "./components/AboutDialog";
import ConfigView from "./components/ConfigView";
import ExecutionFlowGraph from "./components/ExecutionFlowGraph";
import LiveLogs from "./components/LiveLogs";
import PlanReview from "./components/PlanReview";
import ResultCard from "./components/ResultCard";
import StepCard from "./components/StepCard";
import TaskInput from "./components/TaskInput";
import ToastStack from "./components/ToastStack";
import useTaskExecution from "./hooks/useTaskExecution";

// Charts (recharts) only load when the Metrics or Trace tab is opened.
const RunMetrics = lazy(() => import("./components/RunMetrics"));
const TraceTimeline = lazy(() => import("./components/TraceTimeline"));
const TraceWaterfall = lazy(() => import("./components/TraceWaterfall"));
const StepDetailModal = lazy(() => import("./components/StepDetailModal"));

const NAV_ITEMS = [
  { id: "execute", label: "Run" },
  { id: "trace", label: "Trace" },
  { id: "metrics", label: "Metrics" },
  { id: "settings", label: "Configuration" },
];

// Internal events that add noise to logs and timelines without telling the user anything.
const HIDDEN_EVENT_TYPES = new Set(["step_status", "ping", "checkpoint_saved"]);

const STATUS_META = {
  Idle: {
    dotClass: "bg-slate-300",
    badgeClass: "border-white/15 bg-white/10 text-[var(--text-secondary)]",
    pulse: false,
  },
  Planning: {
    dotClass: "bg-[var(--accent-warning)]",
    badgeClass: "border-amber-400/45 bg-amber-500/20 text-amber-100",
    pulse: true,
  },
  Executing: {
    dotClass: "bg-[var(--accent-info)]",
    badgeClass: "border-sky-400/45 bg-sky-500/20 text-sky-100",
    pulse: true,
  },
  "Plan ready": {
    dotClass: "bg-sky-300",
    badgeClass: "border-sky-400/45 bg-sky-500/15 text-sky-100",
    pulse: false,
  },
  Failed: {
    dotClass: "bg-red-300",
    badgeClass: "border-red-400/45 bg-red-500/15 text-red-100",
    pulse: false,
  },
  Complete: {
    dotClass: "bg-[var(--accent-success)]",
    badgeClass: "border-emerald-400/45 bg-emerald-500/20 text-emerald-100",
    pulse: false,
  },
};

const SETTINGS_DEFAULT = {
  chaos_mode: false,
  parallel_mode: true,
  multi_agent_mode: true,
  max_retries: 3,
  step_timeout: 60,
  providers: [],
};

export default function App() {
  const {
    taskId,
    task,
    steps,
    results,
    trace,
    traceSummary,
    taskMetrics,
    aggregateMetrics,
    providerMetrics,
    runtimeConfig,
    parallelLevels,
    agentAssignments,
    agentContributions,
    metrics,
    submitTask,
    runPlannedTask,
    resetTask,
    clearError,
    isLoading,
    isConnected,
    error,
  } = useTaskExecution();

  const [activeView, setActiveView] = useState("execute");
  const [isStepSheetOpen, setIsStepSheetOpen] = useState(true);
  const [selectedStep, setSelectedStep] = useState(null);
  const [selectedStepTrace, setSelectedStepTrace] = useState([]);
  const [selectedStepErrors, setSelectedStepErrors] = useState([]);
  const [isStepModalOpen, setIsStepModalOpen] = useState(false);
  const [toasts, setToasts] = useState([]);
  const [isAboutOpen, setIsAboutOpen] = useState(false);

  const processedToastKeysRef = useRef(new Set());
  const toastTimersRef = useRef(new Map());
  const lastTaskIdRef = useRef("");
  const lastErrorRef = useRef("");

  const settings = useMemo(
    () => ({
      ...SETTINGS_DEFAULT,
      ...(runtimeConfig || {}),
    }),
    [runtimeConfig],
  );

  useEffect(() => {
    return () => {
      for (const timerId of toastTimersRef.current.values()) {
        window.clearTimeout(timerId);
      }
      toastTimersRef.current.clear();
    };
  }, []);

  const dismissToast = useCallback((id) => {
    setToasts((previous) => previous.filter((toast) => toast.id !== id));

    const timerId = toastTimersRef.current.get(id);
    if (timerId) {
      window.clearTimeout(timerId);
      toastTimersRef.current.delete(id);
    }
  }, []);

  const recentToastsRef = useRef(new Map());

  const pushToast = useCallback((message, variant = "info", ttl = 3800) => {
    // The same event can arrive twice (live WebSocket and trace refresh); show it once.
    const now = Date.now();
    const lastShown = recentToastsRef.current.get(message);
    if (lastShown && now - lastShown < 10000) {
      return;
    }
    recentToastsRef.current.set(message, now);
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const nextToast = { id, message, variant };

    setToasts((previous) => [...previous, nextToast].slice(-6));

    const timerId = window.setTimeout(() => {
      setToasts((previous) => previous.filter((toast) => toast.id !== id));
      toastTimersRef.current.delete(id);
    }, ttl);

    toastTimersRef.current.set(id, timerId);
  }, []);

  useEffect(() => {
    const normalizedTaskId = String(taskId || "");
    if (!normalizedTaskId) {
      return;
    }

    if (normalizedTaskId !== lastTaskIdRef.current) {
      lastTaskIdRef.current = normalizedTaskId;
      processedToastKeysRef.current.clear();
      setActiveView("execute");
      setIsStepSheetOpen(true);
      setIsStepModalOpen(false);
    }
  }, [taskId]);

  useEffect(() => {
    const nextError = String(error || "").trim();
    if (!nextError) {
      lastErrorRef.current = "";
      return;
    }

    if (nextError === lastErrorRef.current) {
      return;
    }

    lastErrorRef.current = nextError;
    pushToast(nextError, "error", 4200);
  }, [error, pushToast]);

  useEffect(() => {
    const events = Array.isArray(trace) ? trace : [];
    if (!events.length) {
      return;
    }

    for (const event of events) {
      const eventKey = buildEventKey(event);
      if (processedToastKeysRef.current.has(eventKey)) {
        continue;
      }
      processedToastKeysRef.current.add(eventKey);

      const toastPayload = mapTraceEventToToast(event);
      if (toastPayload) {
        pushToast(toastPayload.message, toastPayload.variant, toastPayload.ttl);
      }
    }
  }, [pushToast, trace]);

  const resultByStepId = useMemo(() => {
    const map = new Map();
    for (const result of Array.isArray(results) ? results : []) {
      const stepId = String(result?.step_id || "").trim();
      if (stepId) {
        map.set(stepId, result);
      }
    }
    return map;
  }, [results]);

  const currentStepIndex = useMemo(
    () => deriveCurrentStepIndex(task, steps, results),
    [task, steps, results],
  );

  const executionStatus = useMemo(
    () => deriveExecutionStatus({ task, isLoading, hasTaskStarted: Boolean(taskId || steps.length || results.length || trace.length) }),
    [isLoading, results.length, steps.length, task, taskId, trace.length],
  );

  const statusMeta = STATUS_META[executionStatus] || STATUS_META.Idle;

  const executionStatusLabel = useMemo(() => {
    if (executionStatus === "Executing" && Boolean(settings.parallel_mode)) {
      const levelCount = Number(metrics?.activeParallelLevelCount || 0);
      if (levelCount > 0) {
        return `Executing (L${levelCount})`;
      }
    }
    return executionStatus;
  }, [executionStatus, metrics?.activeParallelLevelCount, settings.parallel_mode]);

  const confidence = String(task?.confidence_score || task?.final_output?.summary?.confidence || "Unknown");
  const hasFinalResult = Boolean(task?.final_output?.result);

  const visibleTrace = useMemo(() => {
    let completionSeen = false;
    return (Array.isArray(trace) ? trace : []).filter((event) => {
      const type = String(event?.event_type || "").toLowerCase();
      if (HIDDEN_EVENT_TYPES.has(type) || type.startsWith("ws_")) {
        return false;
      }
      if (type === "task_completed") {
        // The finalizer's trace entry and the live completion event describe the same moment.
        if (completionSeen) return false;
        completionSeen = true;
      }
      return true;
    });
  }, [trace]);

  const openStepModalFromAny = useCallback((payload) => {
    const identity = extractStepIdentity(payload);
    if (!identity.stepId && !identity.stepName) {
      return;
    }

    const matchedStep = findStepByIdentity(steps, identity);
    const normalizedStepId = String(identity.stepId || matchedStep?.step_id || "");
    const matchedResult = normalizedStepId ? resultByStepId.get(normalizedStepId) : null;

    const modalStep = {
      ...(matchedStep || {}),
      ...(matchedResult || {}),
      ...(identity.rawPayload || {}),
      step_id: normalizedStepId || matchedStep?.step_id || "",
      name: String(
        matchedStep?.name ||
          identity.stepName ||
          matchedResult?.step_name ||
          normalizedStepId ||
          "Step",
      ),
    };

    const stepTrace = filterStepTrace(trace, {
      stepId: normalizedStepId,
      stepName: modalStep.name,
    });

    // Validator verdicts moved to validation_log; non-passing ones still belong in the step's issue list.
    const validationIssues = (Array.isArray(task?.validation_log) ? task.validation_log : []).filter(
      (item) => item && String(item.validator_verdict || "").toLowerCase() !== "pass",
    );
    const allErrors = [...(Array.isArray(task?.error_log) ? task.error_log : []), ...validationIssues];
    const stepErrors = allErrors.filter((item) => {
      if (!item || typeof item !== "object") {
        return false;
      }
      const itemStepId = String(item.step_id || item.stepId || "");
      const itemStepName = String(item.step_name || item.stepName || "").toLowerCase();
      if (normalizedStepId && itemStepId === normalizedStepId) {
        return true;
      }
      if (modalStep.name && itemStepName === String(modalStep.name).toLowerCase()) {
        return true;
      }
      return false;
    });

    setSelectedStep(modalStep);
    setSelectedStepTrace(stepTrace);
    setSelectedStepErrors(stepErrors);
    setIsStepModalOpen(true);
  }, [resultByStepId, steps, task?.error_log, task?.validation_log, trace]);

  const handleTaskSubmit = useCallback(async (input, options = {}) => {
    setActiveView("execute");
    const response = await submitTask(input, options);

    if (!response?.success) {
      pushToast(response?.error || "Failed to submit task", "error", 4200);
      return response;
    }

    if (!response.planned) {
      pushToast("Run started", "success", 2200);
    }
    return response;
  }, [pushToast, submitTask]);

  const handleRunPlan = useCallback(async () => {
    const response = await runPlannedTask();
    if (!response?.success) {
      pushToast(response?.error || "Could not start the run", "error", 4200);
    }
  }, [pushToast, runPlannedTask]);

  const handleTraceEventClick = useCallback((event) => {
    const payload = {
      ...(event?.rawPayload || {}),
      step_id: event?.stepId || event?.rawPayload?.step_id,
      step_name: event?.stepName || event?.rawPayload?.step_name,
      name: event?.stepName || event?.rawPayload?.step_name,
    };
    openStepModalFromAny(payload);
  }, [openStepModalFromAny]);

  const isPlanned = String(task?.status || "") === "planned" && results.length === 0;

  const renderActiveView = () => {
    if (activeView === "execute") {
      return (
        <ExecuteView
          task={task}
          steps={steps}
          results={results}
          trace={visibleTrace}
          currentStepIndex={currentStepIndex}
          isLoading={isLoading}
          isPlanned={isPlanned}
          isStepSheetOpen={isStepSheetOpen}
          onToggleStepSheet={() => setIsStepSheetOpen((previous) => !previous)}
          onTaskSubmit={handleTaskSubmit}
          onRunPlan={handleRunPlan}
          onReset={resetTask}
          onStepClick={openStepModalFromAny}
          resultByStepId={resultByStepId}
        />
      );
    }

    if (activeView === "trace") {
      return (
        <TraceView
          trace={visibleTrace}
          steps={steps}
          results={results}
          onTraceEventClick={handleTraceEventClick}
          onStepClick={openStepModalFromAny}
        />
      );
    }

    if (activeView === "metrics") {
      return <RunMetrics task={task} aggregateMetrics={aggregateMetrics} />;
    }

    return <ConfigView config={settings} />;
  };

  return (
    <main className="relative min-h-screen overflow-hidden pb-12">
      <AnimatedBackground />

      <div className="relative z-10 mx-auto w-full max-w-[1500px] px-4 py-4 sm:px-6 lg:px-8">
        <header className="glass rounded-2xl border border-white/15 p-4 sm:p-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0">
              <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">
                <span className="bg-gradient-to-r from-sky-300 via-cyan-200 to-emerald-200 bg-clip-text text-transparent">
                  Reliable AI Agent
                </span>
              </h1>
              <p className="mt-1 max-w-3xl text-sm text-[var(--text-secondary)]">
                Plans a task, runs specialist agents in parallel, checks every answer and recovers from model failures.
                LangGraph on Amazon Bedrock.
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge status={executionStatusLabel} statusMeta={statusMeta} />
              {isConnected ? <LiveBadge /> : null}
              <button
                type="button"
                onClick={() => setIsAboutOpen(true)}
                className="rounded-full border border-white/15 bg-white/5 px-3 py-1 text-xs text-[var(--text-secondary)] transition hover:border-sky-400/40 hover:text-[var(--text-primary)]"
              >
                About this demo
              </button>
            </div>
          </div>

          <nav className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {NAV_ITEMS.map((item) => {
              const isActive = item.id === activeView;
              return (
                <button
                  key={item.id}
                  type="button"
                  aria-current={isActive ? "page" : undefined}
                  onClick={() => setActiveView(item.id)}
                  className={`rounded-xl border px-3 py-2 text-sm font-medium transition ${isActive
                    ? "border-sky-400/55 bg-sky-500/20 text-sky-100"
                    : "border-white/15 bg-white/5 text-[var(--text-secondary)] hover:border-sky-400/40 hover:text-[var(--text-primary)]"}`}
                >
                  {item.label}
                </button>
              );
            })}
          </nav>
        </header>

        {error ? (
          <div className="mt-4 rounded-xl border border-red-400/40 bg-red-500/10 px-4 py-3 text-sm text-red-100">
            <div className="flex items-start justify-between gap-3">
              <p>{String(error)}</p>
              <button
                type="button"
                onClick={clearError}
                className="rounded-md border border-white/20 bg-black/25 px-2 py-1 text-xs text-white/90 transition hover:bg-black/35"
              >
                Dismiss
              </button>
            </div>
          </div>
        ) : null}

        <AnimatePresence mode="wait">
          <motion.section
            key={activeView}
            className="mt-5"
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.22 }}
          >
            <Suspense fallback={<div className="rounded-2xl border border-white/10 bg-[var(--bg-card)]/70 p-5 text-sm text-[var(--text-secondary)]">Loading…</div>}>
              {renderActiveView()}
            </Suspense>
          </motion.section>
        </AnimatePresence>

      </div>

      {isStepModalOpen ? (
        <Suspense fallback={null}>
          <StepDetailModal
            isOpen={isStepModalOpen}
            step={selectedStep}
            stepTrace={selectedStepTrace}
            errors={selectedStepErrors}
            onClose={() => setIsStepModalOpen(false)}
          />
        </Suspense>
      ) : null}

      <AboutDialog open={isAboutOpen} onClose={() => setIsAboutOpen(false)} />
      <ToastStack toasts={toasts} onDismiss={dismissToast} />
    </main>
  );
}

function ExecuteView({
  task,
  steps,
  results,
  trace,
  currentStepIndex,
  isLoading,
  isPlanned,
  isStepSheetOpen,
  onToggleStepSheet,
  onTaskSubmit,
  onRunPlan,
  onReset,
  onStepClick,
  resultByStepId,
}) {
  const status = String(task?.status || "");
  const isRunning = ["executing", "running", "validating", "reflecting", "resumed"].includes(status);
  const isDone = status === "completed" || status === "failed";
  const doneCount = steps.filter((step) => resultByStepId.has(String(step?.step_id || ""))).length;

  return (
    <div className="space-y-5">
      <div className="grid gap-5 xl:grid-cols-[400px_minmax(0,1fr)]">
        <div className="space-y-5">
          {isPlanned ? (
            <PlanReview
              steps={steps}
              levels={task?.execution_levels || []}
              isLoading={isLoading}
              onRun={onRunPlan}
              onReset={onReset}
            />
          ) : (
            <section className="rounded-2xl border border-white/10 bg-[var(--bg-card)]/70 p-4 sm:p-5">
              <div className="flex items-baseline justify-between gap-2">
                <h2 className="text-lg font-semibold text-[var(--text-primary)]">{isDone ? "New task" : "Task"}</h2>
                {isDone ? (
                  <button type="button" onClick={onReset} className="text-xs text-sky-200 underline-offset-2 hover:underline">
                    Clear current run
                  </button>
                ) : null}
              </div>
              {isRunning ? (
                <div className="mt-3 rounded-xl border border-sky-400/25 bg-sky-500/10 p-3 text-sm text-sky-100" role="status" aria-live="polite">
                  Running: {doneCount} of {steps.length} steps finished. Answers are reviewed as they arrive.
                </div>
              ) : (
                <div className="mt-3">
                  <TaskInput onSubmit={onTaskSubmit} isLoading={isLoading} />
                </div>
              )}
            </section>
          )}

          <details className="group rounded-2xl border border-white/10 bg-[var(--bg-card)]/70 p-4 sm:p-5" open={isRunning}>
            <summary className="cursor-pointer list-none text-sm font-semibold text-[var(--text-primary)]">
              Live log <span className="font-normal text-[var(--text-secondary)]">({trace.length} events)</span>
            </summary>
            <div className="mt-3">
              <LiveLogs events={trace} />
            </div>
          </details>
        </div>

        <div className="space-y-5">
          <ResultCard task={task} />

          {steps.length > 0 && !isPlanned ? (
            <ExecutionFlowGraph
              steps={steps}
              results={results}
              trace={trace}
              currentStepIndex={currentStepIndex}
              onStepClick={(stepNode) => onStepClick(stepNode)}
            />
          ) : !isPlanned ? (
            <EmptyRunState />
          ) : null}
        </div>
      </div>

      {steps.length > 0 && !isPlanned ? (
        <section className="rounded-2xl border border-white/10 bg-[var(--bg-card)]/70 p-3 sm:p-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h3 className="text-sm font-semibold text-[var(--text-primary)]">Steps</h3>
            <button
              type="button"
              onClick={onToggleStepSheet}
              aria-expanded={isStepSheetOpen}
              className="rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 text-xs text-[var(--text-secondary)] transition hover:border-sky-400/40 hover:text-[var(--text-primary)]"
            >
              {isStepSheetOpen ? "Hide" : "Show"}
            </button>
          </div>
          {isStepSheetOpen ? (
            <div tabIndex={0} role="region" aria-label="Step cards" className="overflow-x-auto">
              <div className="flex min-w-max gap-3 pb-1">
                {steps.map((step, index) => {
                  const stepId = String(step?.step_id || `step_${index + 1}`);
                  return (
                    <div key={stepId} className="w-[320px] shrink-0">
                      <StepCard
                        step={step}
                        result={resultByStepId.get(stepId) || null}
                        index={index}
                        isActive={index === currentStepIndex}
                        onClick={(selected) => onStepClick(selected || step)}
                      />
                    </div>
                  );
                })}
              </div>
            </div>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

function EmptyRunState() {
  const stages = [
    ["Plan", "A planner splits your task into steps and finds which can run in parallel."],
    ["Run", "A supervisor hands each step to a specialist agent; independent steps run together."],
    ["Review", "A reviewer model checks every answer; weak ones are retried with its feedback or rewritten."],
    ["Combine", "A finalizer writes one answer from the reviewed steps."],
  ];
  return (
    <section className="rounded-2xl border border-white/10 bg-[var(--bg-card)]/70 p-4 sm:p-5">
      <h2 className="text-base font-semibold text-[var(--text-primary)]">How a run works</h2>
      <ol className="mt-3 grid gap-3 sm:grid-cols-2">
        {stages.map(([title, text], index) => (
          <li key={title} className="rounded-xl border border-white/10 bg-[#0c142a]/70 p-3">
            <p className="text-sm font-medium text-[var(--text-primary)]">{index + 1}. {title}</p>
            <p className="mt-1 text-xs text-[var(--text-secondary)]">{text}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

function TraceView({
  trace,
  steps,
  results,
  onTraceEventClick,
  onStepClick,
}) {
  const summaryCards = useMemo(() => {
    const count = (types) => trace.filter((event) => types.includes(String(event?.event_type || ""))).length;
    const times = trace.map((event) => Date.parse(event?.timestamp || "")).filter((value) => !Number.isNaN(value));
    const durationMs = times.length > 1 ? Math.max(...times) - Math.min(...times) : 0;
    return [
      { label: "Events", value: trace.length },
      { label: "Steps finished", value: count(["step_completed", "step_failed"]) },
      { label: "Retries", value: count(["retry_triggered"]) },
      { label: "Tool calls", value: count(["tool_called", "tool_failed"]) },
      { label: "Reflections", value: count(["reflection_completed"]) },
      { label: "Duration", value: formatTraceDuration(durationMs) },
    ];
  }, [trace]);

  return (
    <div className="space-y-5">
      <section className="rounded-2xl border border-white/10 bg-[var(--bg-card)]/70 p-4 sm:p-5">
        <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
          {summaryCards.map((item) => (
            <div key={item.label} className="rounded-xl border border-white/10 bg-white/5 px-3 py-2">
              <p className="text-[11px] uppercase tracking-wide text-[var(--text-secondary)]">{item.label}</p>
              <p className="mt-1 text-sm font-semibold text-[var(--text-primary)]">{item.value}</p>
            </div>
          ))}
        </div>
      </section>

      <TraceWaterfall
        steps={steps}
        results={results}
        trace={trace}
        onStepClick={(row) => onStepClick(row)}
      />

      <TraceTimeline trace={trace} onEventClick={onTraceEventClick} />
    </div>
  );
}

function LiveBadge() {
  return (
    <span className="inline-flex items-center gap-2 rounded-full border border-emerald-400/40 bg-emerald-500/15 px-3 py-1 text-xs text-emerald-100">
      <span className="running-pulse h-2 w-2 rounded-full bg-emerald-300" />
      Live
    </span>
  );
}

function StatusBadge({ status, statusMeta }) {
  return (
    <div className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-semibold ${statusMeta.badgeClass}`}>
      <span className={`inline-block h-2.5 w-2.5 rounded-full ${statusMeta.dotClass} ${statusMeta.pulse ? "running-pulse" : ""}`} />
      {status}
    </div>
  );
}

function deriveExecutionStatus({ task, isLoading, hasTaskStarted }) {
  const status = String(task?.status || "").toLowerCase();

  if (status === "completed") {
    return "Complete";
  }
  if (status === "failed") {
    return "Failed";
  }
  if (status === "planned" && !isLoading) {
    return "Plan ready";
  }
  if (isLoading || status === "planning" || status === "planned") {
    return "Planning";
  }
  if (
    status === "executing" ||
    status === "validating" ||
    status === "reflecting" ||
    status === "resumed" ||
    (hasTaskStarted && status && status !== "completed")
  ) {
    return "Executing";
  }
  return "Idle";
}

function deriveCurrentStepIndex(task, steps, results) {
  const taskIndex = Number(task?.current_step_index);
  if (Number.isFinite(taskIndex) && taskIndex >= 0) {
    return taskIndex;
  }

  if (!Array.isArray(steps) || steps.length === 0) {
    return 0;
  }

  const resultByStepId = new Map();
  for (const result of Array.isArray(results) ? results : []) {
    const stepId = String(result?.step_id || "").trim();
    if (stepId) {
      resultByStepId.set(stepId, result);
    }
  }

  const nextIndex = steps.findIndex((step, index) => {
    const stepId = String(step?.step_id || `step_${index + 1}`);
    const result = resultByStepId.get(stepId);
    const status = String(result?.status || "").toLowerCase();
    return !status || (status !== "success" && status !== "completed" && status !== "failed");
  });

  if (nextIndex >= 0) {
    return nextIndex;
  }

  return Math.max(0, steps.length - 1);
}

function extractStepIdentity(payload) {
  const raw = payload && typeof payload === "object" ? payload : {};
  const data = raw?.data && typeof raw.data === "object" ? raw.data : {};
  const details = raw?.details && typeof raw.details === "object" ? raw.details : {};

  const stepId = String(
    raw.step_id ||
      raw.stepId ||
      data.step_id ||
      data.stepId ||
      details.step_id ||
      details.stepId ||
      "",
  ).trim();

  const stepName = String(
    raw.step_name ||
      raw.stepName ||
      raw.name ||
      data.step_name ||
      data.stepName ||
      details.step_name ||
      details.stepName ||
      raw.label ||
      "",
  ).trim();

  return {
    stepId,
    stepName,
    rawPayload: raw,
  };
}

function findStepByIdentity(steps, identity) {
  const safeSteps = Array.isArray(steps) ? steps : [];
  if (identity.stepId) {
    const matched = safeSteps.find((step, index) => {
      const stepId = String(step?.step_id || step?.id || `step_${index + 1}`);
      return stepId === identity.stepId;
    });
    if (matched) {
      return matched;
    }
  }

  if (identity.stepName) {
    const normalizedName = identity.stepName.toLowerCase();
    return safeSteps.find((step) => String(step?.name || "").toLowerCase() === normalizedName) || null;
  }

  return null;
}

function filterStepTrace(trace, { stepId, stepName }) {
  const safeTrace = Array.isArray(trace) ? trace : [];
  const normalizedName = String(stepName || "").toLowerCase();

  return safeTrace.filter((event) => {
    if (!event || typeof event !== "object") {
      return false;
    }

    const data = event?.data && typeof event.data === "object" ? event.data : {};
    const details = event?.details && typeof event.details === "object" ? event.details : {};

    const eventStepId = String(event.step_id || data.step_id || details.step_id || "").trim();
    const eventStepName = String(event.step_name || data.step_name || details.step_name || "").toLowerCase();

    if (stepId && eventStepId === stepId) {
      return true;
    }
    if (normalizedName && eventStepName === normalizedName) {
      return true;
    }
    return false;
  });
}

function mapTraceEventToToast(event) {
  const payload = event && typeof event === "object" ? event : {};
  const data = payload?.data && typeof payload.data === "object" ? payload.data : {};
  const details = payload?.details && typeof payload.details === "object" ? payload.details : {};
  const merged = { ...details, ...data, ...payload };

  const eventType = String(payload.event_type || payload.type || "").toLowerCase();

  if (eventType === "planning_complete") {
    const steps = Number(merged.step_count || 0);
    return {
      message: steps > 0 ? `Planning complete: ${steps} steps` : "Planning complete",
      variant: "success",
      ttl: 2200,
    };
  }

  if (eventType === "retry_triggered") {
    const stepLabel = stepLabelFromAny(merged.step_name, merged.step_id);
    return {
      message: `${stepLabel || "Step"} retry triggered`,
      variant: "warning",
      ttl: 2600,
    };
  }

  // Fallback transitions are logged in the trace timeline but no longer
  // surface as repetitive toast notifications — open-source models are the
  // primary provider chain, so fallback events are routine and non-actionable.

  if (eventType === "task_completed") {
    const confidence = String(merged?.summary?.confidence || merged?.confidence || "Unknown");
    return {
      message: `Task completed. Confidence: ${confidence}`,
      variant: "success",
      ttl: 3600,
    };
  }

  if (eventType === "task_failed") {
    const reason = String(merged.error || merged.reason || "Task failed");
    return {
      message: truncateText(reason, 100),
      variant: "error",
      ttl: 4200,
    };
  }

  return null;
}

function stepLabelFromAny(stepName, stepId) {
  if (stepName) {
    return String(stepName);
  }
  const rawStepId = String(stepId || "");
  const match = rawStepId.match(/step_(\d+)/i);
  if (match) {
    return `Step ${match[1]}`;
  }
  return rawStepId;
}

function buildEventKey(event) {
  const payload = event && typeof event === "object" ? event : {};
  return [
    String(payload.timestamp || ""),
    String(payload.event_type || payload.type || ""),
    String(payload.step_id || payload?.data?.step_id || payload?.details?.step_id || ""),
    String(payload?.data?.attempt || payload?.details?.attempt || ""),
  ].join("|");
}

function formatTraceDuration(value) {
  const duration = Number(value || 0);
  if (!Number.isFinite(duration) || duration <= 0) {
    return "-";
  }
  return `${(duration / 1000).toFixed(1)}s`;
}

function truncateText(value, maxLength = 360) {
  const text = String(value || "");
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength)}...`;
}

function AnimatedBackground() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      <motion.div
        className="absolute -top-24 -left-20 h-72 w-72 rounded-full bg-[var(--accent-primary)]/16 blur-3xl"
        animate={{ x: [0, 40, -22, 0], y: [0, 24, 8, 0] }}
        transition={{ duration: 18, repeat: Infinity, ease: "easeInOut" }}
      />
      <motion.div
        className="absolute top-20 right-[-100px] h-80 w-80 rounded-full bg-sky-500/14 blur-3xl"
        animate={{ x: [0, -58, 16, 0], y: [0, -14, 18, 0] }}
        transition={{ duration: 20, repeat: Infinity, ease: "easeInOut" }}
      />
      <motion.div
        className="absolute bottom-[-140px] left-[26%] h-96 w-96 rounded-full bg-emerald-500/12 blur-3xl"
        animate={{ x: [0, 34, -28, 0], y: [0, -24, 12, 0] }}
        transition={{ duration: 22, repeat: Infinity, ease: "easeInOut" }}
      />
    </div>
  );
}
