import { AnimatePresence, motion } from "framer-motion";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import ReactMarkdown from "react-markdown";
import ExecutionFlowGraph from "./components/ExecutionFlowGraph";
import LiveLogs from "./components/LiveLogs";
import MetricsDashboard from "./components/MetricsDashboard";
import StepCard from "./components/StepCard";
import StepDetailModal from "./components/StepDetailModal";
import TaskInput from "./components/TaskInput";
import ToastStack from "./components/ToastStack";
import TraceTimeline from "./components/TraceTimeline";
import TraceWaterfall from "./components/TraceWaterfall";
import useTaskExecution from "./hooks/useTaskExecution";

const NAV_ITEMS = [
  { id: "execute", label: "▶️ Execute" },
  { id: "trace", label: "📊 Trace" },
  { id: "metrics", label: "📈 Metrics" },
  { id: "settings", label: "⚙️ Settings" },
];

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
  providers: [
    { provider: "open_source", model: "meta-llama/Llama-3.1-8B-Instruct", label: "Primary (Llama 3.1)" },
    { provider: "open_source", model: "Qwen/Qwen2.5-7B-Instruct", label: "Fallback (Qwen 2.5)" },
    { provider: "open_source", model: "mistralai/Mistral-7B-Instruct-v0.3", label: "Fallback (Mistral 7B)" },
  ],
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
    updateRuntimeSettings,
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
  const [isSettingsSaving, setIsSettingsSaving] = useState(false);
  const [providerDraft, setProviderDraft] = useState(SETTINGS_DEFAULT.providers);

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
    setProviderDraft(Array.isArray(settings.providers) ? settings.providers : SETTINGS_DEFAULT.providers);
  }, [settings.providers]);

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

  const pushToast = useCallback((message, variant = "info", ttl = 3800) => {
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

  const metricsSummary = useMemo(() => {
    return [
      { label: "Tokens", value: Number(metrics?.totalTokens || 0).toLocaleString() },
      { label: "Est. Cost", value: `$${Number(metrics?.estimatedCost || 0).toFixed(4)}` },
      { label: "Retries", value: Number(metrics?.retryCount || 0) },
      { label: "Fallbacks", value: Number(metrics?.fallbackCount || 0) },
      { label: "Reflections", value: Number(metrics?.reflectionCount || 0) },
      { label: "Agents", value: Number(metrics?.agentCount || 0) },
      { label: "Parallel Levels", value: Number(metrics?.activeParallelLevelCount || 0) },
      { label: "Confidence", value: confidence },
    ];
  }, [confidence, metrics]);

  const dashboardMetrics = useMemo(() => {
    const aggregate = aggregateMetrics && typeof aggregateMetrics === "object" ? aggregateMetrics : {};
    const taskSpecific = taskMetrics && typeof taskMetrics === "object" ? taskMetrics : {};

    return {
      ...aggregate,
      ...taskSpecific,
      retry_count: Number(taskSpecific.retry_count || metrics?.retryCount || 0),
      reflection_count: Number(taskSpecific.reflection_count || metrics?.reflectionCount || 0),
      total_tokens_consumed: Number(
        taskSpecific.total_tokens_consumed ||
          taskSpecific.total_tokens ||
          metrics?.totalTokens ||
          0,
      ),
      total_cost_usd: Number(taskSpecific.total_cost_usd || metrics?.estimatedCost || 0),
      agent_contributions:
        Object.keys(agentContributions || {}).length > 0
          ? agentContributions
          : aggregate.agent_contributions,
    };
  }, [agentContributions, aggregateMetrics, metrics, taskMetrics]);

  const applyRuntimePatch = useCallback(async (patch) => {
    setIsSettingsSaving(true);
    const response = await updateRuntimeSettings(patch);
    setIsSettingsSaving(false);

    if (!response.success) {
      pushToast(response.error || "Failed to update settings", "error", 4200);
      return response;
    }

    pushToast("Runtime settings updated", "success", 1800);
    return response;
  }, [pushToast, updateRuntimeSettings]);

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

    const allErrors = Array.isArray(task?.error_log) ? task.error_log : [];
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
  }, [resultByStepId, steps, task?.error_log, trace]);

  const handleTaskSubmit = useCallback(async (input) => {
    setActiveView("execute");
    const response = await submitTask(input);

    if (!response?.success) {
      pushToast(response?.error || "Failed to submit task", "error", 4200);
      return response;
    }

    pushToast("Task submitted and execution started", "success", 2200);
    return response;
  }, [pushToast, submitTask]);

  const handleTraceEventClick = useCallback((event) => {
    const payload = {
      ...(event?.rawPayload || {}),
      step_id: event?.stepId || event?.rawPayload?.step_id,
      step_name: event?.stepName || event?.rawPayload?.step_name,
      name: event?.stepName || event?.rawPayload?.step_name,
    };
    openStepModalFromAny(payload);
  }, [openStepModalFromAny]);

  const renderActiveView = () => {
    if (activeView === "execute") {
      return (
        <ExecuteView
          task={task}
          steps={steps}
          results={results}
          trace={trace}
          metricsSummary={metricsSummary}
          currentStepIndex={currentStepIndex}
          isLoading={isLoading}
          isStepSheetOpen={isStepSheetOpen}
          onToggleStepSheet={() => setIsStepSheetOpen((previous) => !previous)}
          onTaskSubmit={handleTaskSubmit}
          onStepClick={openStepModalFromAny}
          resultByStepId={resultByStepId}
        />
      );
    }

    if (activeView === "trace") {
      return (
        <TraceView
          trace={trace}
          traceSummary={traceSummary}
          steps={steps}
          results={results}
          onTraceEventClick={handleTraceEventClick}
          onStepClick={openStepModalFromAny}
        />
      );
    }

    if (activeView === "metrics") {
      return (
        <MetricsView
          dashboardMetrics={dashboardMetrics}
          providerMetrics={providerMetrics}
          agentAssignments={agentAssignments}
          parallelLevels={parallelLevels}
        />
      );
    }

    return (
      <SettingsView
        settings={settings}
        providerDraft={providerDraft}
        setProviderDraft={setProviderDraft}
        isSaving={isSettingsSaving}
        onPatch={applyRuntimePatch}
      />
    );
  };

  return (
    <main className="relative min-h-screen overflow-hidden pb-12">
      <AnimatedBackground />

      <div className="relative z-10 mx-auto w-full max-w-[1500px] px-4 py-4 sm:px-6 lg:px-8">
        <header className="glass rounded-2xl border border-white/15 p-4 sm:p-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">
                <span className="bg-gradient-to-r from-sky-300 via-cyan-200 to-emerald-200 bg-clip-text text-transparent">
                  ⚡ Reliable AI Agent
                </span>
              </h1>
              <p className="mt-1 max-w-3xl text-sm text-[var(--text-secondary)]">
                Mission control for execution trace, reliability observability, parallel DAG flow, and multi-agent operations.
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge status={executionStatusLabel} statusMeta={statusMeta} />
              <ConnectionBadge isConnected={isConnected} />

              {settings.parallel_mode ? (
                <MetaBadge value={`${Number(metrics?.activeParallelLevelCount || 0)} parallel levels`} />
              ) : null}

              <MetaBadge value={`${Number(metrics?.agentCount || 0)} active agents`} />
            </div>
          </div>

          <nav className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {NAV_ITEMS.map((item) => {
              const isActive = item.id === activeView;
              return (
                <button
                  key={item.id}
                  type="button"
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
            {renderActiveView()}
          </motion.section>
        </AnimatePresence>

        {hasFinalResult ? (
          <motion.section
            className="mt-5 rounded-2xl border border-emerald-400/35 bg-emerald-500/10 p-4 sm:p-5"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2 }}
          >
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="text-base font-semibold text-emerald-100">Final Result Available</h3>
                <p className="text-sm text-emerald-50/90">
                  Confidence: {confidence}. Open any step card, graph node, or trace event for deep diagnostics.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setActiveView("trace")}
                className="rounded-lg border border-emerald-300/45 bg-emerald-500/20 px-3 py-2 text-sm font-medium text-emerald-100 transition hover:bg-emerald-500/30"
              >
                Inspect Full Trace
              </button>
            </div>
          </motion.section>
        ) : null}
      </div>

      <StepDetailModal
        isOpen={isStepModalOpen}
        step={selectedStep}
        stepTrace={selectedStepTrace}
        errors={selectedStepErrors}
        onClose={() => setIsStepModalOpen(false)}
      />

      <ToastStack toasts={toasts} onDismiss={dismissToast} />
    </main>
  );
}

function ExecuteView({
  task,
  steps,
  results,
  trace,
  metricsSummary,
  currentStepIndex,
  isLoading,
  isStepSheetOpen,
  onToggleStepSheet,
  onTaskSubmit,
  onStepClick,
  resultByStepId,
}) {
  return (
    <div className="space-y-5">
      <div className="grid gap-5 xl:grid-cols-[360px_minmax(0,1fr)]">
        <div className="space-y-5">
          <section className="rounded-2xl border border-white/10 bg-[var(--bg-card)]/70 p-4 sm:p-5">
            <h2 className="text-lg font-semibold text-[var(--text-primary)]">Launch Task</h2>
            <p className="mb-3 text-xs text-[var(--text-secondary)]">
              Submit a multi-step goal to trigger planner, executor, reliability loops, and finalizer.
            </p>
            <TaskInput onSubmit={onTaskSubmit} isLoading={isLoading} />
          </section>

          <QuickMetricsPanel metricsSummary={metricsSummary} />

          <LiveLogs events={trace} />
        </div>

        <div className="space-y-5">
          <ExecutionFlowGraph
            steps={steps}
            results={results}
            trace={trace}
            currentStepIndex={currentStepIndex}
            onStepClick={(stepNode) => onStepClick(stepNode)}
          />

          {task?.final_output?.result ? (
            <section className="rounded-2xl border border-white/10 bg-[var(--bg-card)]/70 p-4">
              <p className="mb-2 text-xs uppercase tracking-wide text-[var(--text-secondary)]">Final Output Preview</p>
              <div className="prose prose-sm prose-invert max-w-none max-h-96 overflow-y-auto leading-relaxed">
                <ReactMarkdown>{String(task.final_output.result)}</ReactMarkdown>
              </div>
            </section>
          ) : null}
        </div>
      </div>

      <section className="rounded-2xl border border-white/10 bg-[var(--bg-card)]/70 p-3 sm:p-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h3 className="text-sm font-semibold text-[var(--text-primary)]">Step Cards</h3>
          <button
            type="button"
            onClick={onToggleStepSheet}
            className="rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 text-xs text-[var(--text-secondary)] transition hover:border-sky-400/40 hover:text-[var(--text-primary)]"
          >
            {isStepSheetOpen ? "Collapse" : "Expand"}
          </button>
        </div>

        <AnimatePresence initial={false}>
          {isStepSheetOpen ? (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.2 }}
              className="overflow-x-auto"
            >
              {steps.length === 0 ? (
                <div className="rounded-xl border border-white/10 bg-[#0c142a]/80 p-4 text-sm text-[var(--text-secondary)]">
                  Step cards will appear once planning completes.
                </div>
              ) : (
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
              )}
            </motion.div>
          ) : null}
        </AnimatePresence>
      </section>
    </div>
  );
}

function TraceView({
  trace,
  traceSummary,
  steps,
  results,
  onTraceEventClick,
  onStepClick,
}) {
  const summaryCards = useMemo(() => {
    const summary = traceSummary && typeof traceSummary === "object" ? traceSummary : {};
    return [
      { label: "Total Events", value: Number(summary.total_events || trace.length || 0) },
      { label: "Step Events", value: Number(summary.step_events || 0) },
      { label: "Retries", value: Number(summary.retry_events || 0) },
      { label: "Fallbacks", value: Number(summary.fallback_events || 0) },
      { label: "Reflections", value: Number(summary.reflection_events || 0) },
      { label: "Duration", value: formatTraceDuration(summary.total_duration_ms) },
    ];
  }, [trace.length, traceSummary]);

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

function MetricsView({ dashboardMetrics, providerMetrics, agentAssignments, parallelLevels }) {
  const assignmentCount = Array.isArray(agentAssignments) ? agentAssignments.length : 0;
  const levelCount = Array.isArray(parallelLevels) ? parallelLevels.length : 0;

  return (
    <div className="space-y-5">
      <section className="rounded-2xl border border-white/10 bg-[var(--bg-card)]/70 p-4 sm:p-5">
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          <MetricBlurb label="Agent Assignments" value={assignmentCount} />
          <MetricBlurb label="Parallel Levels" value={levelCount} />
          <MetricBlurb label="Provider Metrics" value={Object.keys(providerMetrics || {}).length} />
        </div>
      </section>

      <MetricsDashboard metrics={dashboardMetrics} providerMetrics={providerMetrics} />
    </div>
  );
}

function SettingsView({ settings, providerDraft, setProviderDraft, isSaving, onPatch }) {
  const [retryDraft, setRetryDraft] = useState(Number(settings.max_retries || SETTINGS_DEFAULT.max_retries));
  const [timeoutDraft, setTimeoutDraft] = useState(Number(settings.step_timeout || SETTINGS_DEFAULT.step_timeout));

  useEffect(() => {
    setRetryDraft(Number(settings.max_retries || SETTINGS_DEFAULT.max_retries));
  }, [settings.max_retries]);

  useEffect(() => {
    setTimeoutDraft(Number(settings.step_timeout || SETTINGS_DEFAULT.step_timeout));
  }, [settings.step_timeout]);

  const handleProviderField = (index, field, value) => {
    setProviderDraft((previous) => {
      const next = [...previous];
      next[index] = {
        ...(next[index] || {}),
        [field]: value,
      };
      return next;
    });
  };

  const addProvider = () => {
    setProviderDraft((previous) => [
      ...previous,
      { provider: "openai", model: "", label: `Fallback ${previous.length + 1}` },
    ]);
  };

  const removeProvider = (index) => {
    setProviderDraft((previous) => previous.filter((_, itemIndex) => itemIndex !== index));
  };

  const moveProvider = (index, direction) => {
    setProviderDraft((previous) => {
      const target = index + direction;
      if (target < 0 || target >= previous.length) {
        return previous;
      }
      const next = [...previous];
      const [entry] = next.splice(index, 1);
      next.splice(target, 0, entry);
      return next;
    });
  };

  return (
    <div className="space-y-5">
      <section className="rounded-2xl border border-white/10 bg-[var(--bg-card)]/70 p-4 sm:p-5">
        <h2 className="text-lg font-semibold text-[var(--text-primary)]">Execution Strategy</h2>
        <p className="mt-1 text-xs text-[var(--text-secondary)]">Runtime reliability controls are persisted locally and synced to backend when supported.</p>

        <div className="mt-4 grid gap-3 md:grid-cols-3">
          <ToggleCard
            title="Chaos Mode"
            description="Inject random faults to validate recovery behavior."
            enabled={Boolean(settings.chaos_mode)}
            onToggle={(value) => onPatch({ chaos_mode: value })}
          />

          <ToggleCard
            title="Parallel Mode"
            description="Allow independent steps to run in parallel lanes."
            enabled={Boolean(settings.parallel_mode)}
            onToggle={(value) => onPatch({ parallel_mode: value })}
          />

          <ToggleCard
            title="Multi-Agent Mode"
            description="Route each step to a specialist agent profile."
            enabled={Boolean(settings.multi_agent_mode)}
            onToggle={(value) => onPatch({ multi_agent_mode: value })}
          />
        </div>
      </section>

      <section className="rounded-2xl border border-white/10 bg-[var(--bg-card)]/70 p-4 sm:p-5">
        <h2 className="text-lg font-semibold text-[var(--text-primary)]">Guardrails</h2>

        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <SliderCard
            label="Max Retries"
            value={retryDraft}
            min={0}
            max={8}
            step={1}
            suffix="attempts"
            onChange={setRetryDraft}
            onCommit={(value) => onPatch({ max_retries: value })}
          />

          <SliderCard
            label="Step Timeout"
            value={timeoutDraft}
            min={15}
            max={240}
            step={5}
            suffix="seconds"
            onChange={setTimeoutDraft}
            onCommit={(value) => onPatch({ step_timeout: value })}
          />
        </div>
      </section>

      <section className="rounded-2xl border border-white/10 bg-[var(--bg-card)]/70 p-4 sm:p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-[var(--text-primary)]">Provider Fallback Chain</h2>
            <p className="text-xs text-[var(--text-secondary)]">Order determines automatic failover precedence.</p>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={addProvider}
              className="rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 text-xs text-[var(--text-secondary)] transition hover:border-sky-400/40 hover:text-[var(--text-primary)]"
            >
              Add Provider
            </button>
            <button
              type="button"
              onClick={() => onPatch({ providers: providerDraft })}
              disabled={isSaving}
              className="rounded-lg border border-emerald-400/45 bg-emerald-500/20 px-3 py-1.5 text-xs font-medium text-emerald-100 transition hover:bg-emerald-500/30 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isSaving ? "Saving..." : "Save Chain"}
            </button>
          </div>
        </div>

        <div className="mt-4 space-y-3">
          {providerDraft.map((entry, index) => (
            <div key={`${entry.provider}-${entry.model}-${index}`} className="rounded-xl border border-white/10 bg-white/5 p-3">
              <div className="grid gap-2 lg:grid-cols-[150px_minmax(0,1fr)_minmax(0,1fr)_auto]">
                <select
                  value={String(entry.provider || "")}
                  onChange={(event) => handleProviderField(index, "provider", event.target.value)}
                  className="rounded-lg border border-white/15 bg-[#0f1a36] px-3 py-2 text-sm text-[var(--text-primary)] outline-none transition focus:border-sky-400/45"
                >
                  <option value="openai">openai</option>
                  <option value="anthropic">anthropic</option>
                  <option value="custom">custom</option>
                </select>

                <input
                  value={String(entry.model || "")}
                  onChange={(event) => handleProviderField(index, "model", event.target.value)}
                  placeholder="model name"
                  className="rounded-lg border border-white/15 bg-[#0f1a36] px-3 py-2 text-sm text-[var(--text-primary)] outline-none transition focus:border-sky-400/45"
                />

                <input
                  value={String(entry.label || "")}
                  onChange={(event) => handleProviderField(index, "label", event.target.value)}
                  placeholder="label"
                  className="rounded-lg border border-white/15 bg-[#0f1a36] px-3 py-2 text-sm text-[var(--text-primary)] outline-none transition focus:border-sky-400/45"
                />

                <div className="flex items-center justify-end gap-1">
                  <button
                    type="button"
                    onClick={() => moveProvider(index, -1)}
                    className="rounded border border-white/20 bg-white/5 px-2 py-1 text-xs text-[var(--text-secondary)] transition hover:text-[var(--text-primary)]"
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    onClick={() => moveProvider(index, 1)}
                    className="rounded border border-white/20 bg-white/5 px-2 py-1 text-xs text-[var(--text-secondary)] transition hover:text-[var(--text-primary)]"
                  >
                    ↓
                  </button>
                  <button
                    type="button"
                    onClick={() => removeProvider(index)}
                    className="rounded border border-red-400/40 bg-red-500/15 px-2 py-1 text-xs text-red-100 transition hover:bg-red-500/25"
                  >
                    Remove
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function QuickMetricsPanel({ metricsSummary }) {
  return (
    <section className="rounded-2xl border border-white/10 bg-[var(--bg-card)]/70 p-4 sm:p-5">
      <h2 className="text-lg font-semibold text-[var(--text-primary)]">Quick Metrics</h2>
      <div className="mt-3 grid grid-cols-2 gap-2">
        {metricsSummary.map((item) => (
          <div key={item.label} className="rounded-xl border border-white/10 bg-white/5 px-3 py-2">
            <p className="text-[11px] uppercase tracking-wide text-[var(--text-secondary)]">{item.label}</p>
            <p className="mt-1 text-sm font-semibold text-[var(--text-primary)]">{String(item.value)}</p>
          </div>
        ))}
      </div>
    </section>
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

function ConnectionBadge({ isConnected }) {
  return (
    <span className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs ${isConnected
      ? "border-emerald-400/40 bg-emerald-500/20 text-emerald-100"
      : "border-rose-400/40 bg-rose-500/20 text-rose-100"}`}>
      <span className={`h-2.5 w-2.5 rounded-full ${isConnected ? "bg-emerald-300" : "bg-rose-300"}`} />
      {isConnected ? "🟢 Connected" : "🔴 Disconnected"}
    </span>
  );
}

function MetaBadge({ value }) {
  return (
    <span className="inline-flex items-center rounded-full border border-white/15 bg-white/5 px-3 py-1 text-xs text-[var(--text-secondary)]">
      {String(value)}
    </span>
  );
}

function MetricBlurb({ label, value }) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/5 px-3 py-2">
      <p className="text-[11px] uppercase tracking-wide text-[var(--text-secondary)]">{label}</p>
      <p className="mt-1 text-sm font-semibold text-[var(--text-primary)]">{String(value)}</p>
    </div>
  );
}

function ToggleCard({ title, description, enabled, onToggle }) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/5 p-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-[var(--text-primary)]">{title}</p>
          <p className="text-xs text-[var(--text-secondary)]">{description}</p>
        </div>

        <button
          type="button"
          onClick={() => onToggle(!enabled)}
          className={`relative inline-flex h-7 w-14 items-center rounded-full border transition ${enabled
            ? "border-emerald-400/55 bg-emerald-500/30"
            : "border-slate-400/30 bg-slate-700/40"}`}
        >
          <span
            className={`absolute h-5 w-5 rounded-full bg-white transition ${enabled ? "translate-x-8" : "translate-x-1"}`}
          />
        </button>
      </div>
    </div>
  );
}

function SliderCard({ label, value, min, max, step, suffix, onChange, onCommit }) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/5 p-3">
      <div className="mb-2 flex items-center justify-between">
        <p className="text-sm font-medium text-[var(--text-primary)]">{label}</p>
        <p className="text-xs text-[var(--text-secondary)]">
          {value} {suffix}
        </p>
      </div>

      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        onMouseUp={(event) => onCommit(Number(event.currentTarget.value))}
        onTouchEnd={(event) => onCommit(Number(event.currentTarget.value))}
        className="w-full accent-sky-400"
      />
    </div>
  );
}

function deriveExecutionStatus({ task, isLoading, hasTaskStarted }) {
  const status = String(task?.status || "").toLowerCase();

  if (status === "completed" || status === "failed") {
    return "Complete";
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
