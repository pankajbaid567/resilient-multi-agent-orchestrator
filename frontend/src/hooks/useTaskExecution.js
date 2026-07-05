import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  createTask,
  executeTask,
  getAggregateMetrics,
  getProviderMetrics,
  getRuntimeConfig,
  getTask,
  getTaskMetrics,
  getTrace,
  getTraceSummary,
  updateRuntimeConfig,
} from "../services/api";
import useWebSocket from "./useWebSocket";

const SETTINGS_STORAGE_KEY = "raamst.runtime.settings.v1";

const DEFAULT_RUNTIME_CONFIG = {
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

const REFRESH_EVENT_TYPES = new Set([
  "planning_complete",
  "step_started",
  "step_completed",
  "step_failed",
  "retry_triggered",
  "reflection_started",
  "reflection_completed",
  "task_completed",
  "task_failed",
  "agent_assigned",
]);

export default function useTaskExecution() {
  const [taskId, setTaskId] = useState(null);
  const [task, setTask] = useState(null);
  const [steps, setSteps] = useState([]);
  const [results, setResults] = useState([]);
  const [trace, setTrace] = useState([]);
  const [traceSummary, setTraceSummary] = useState(null);
  const [taskMetrics, setTaskMetrics] = useState(null);
  const [aggregateMetrics, setAggregateMetrics] = useState(null);
  const [providerMetrics, setProviderMetrics] = useState(null);
  const [runtimeConfig, setRuntimeConfig] = useState(() => loadStoredRuntimeConfig());
  const [parallelLevels, setParallelLevels] = useState([]);
  const [agentAssignments, setAgentAssignments] = useState([]);
  const [agentContributions, setAgentContributions] = useState({});
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);

  const processedEventsRef = useRef(0);
  const syncInFlightRef = useRef(false);
  const refreshTimerRef = useRef(null);

  const { events, isConnected, error: socketError } = useWebSocket(taskId);

  const hydrateFromState = useCallback((state) => {
    if (!state || typeof state !== "object") {
      return;
    }

    const normalizedTask = {
      ...state,
      task_id: state.task_id || taskId,
    };

    setTask(normalizedTask);
    const nextSteps = Array.isArray(state.steps) ? state.steps : [];
    const nextResults = Array.isArray(state.step_results) ? state.step_results : [];
    const stateTrace = (Array.isArray(state.execution_trace) ? state.execution_trace : []).map((entry) => ({
      ...(entry || {}),
      source: entry?.source || "state",
    }));

    setSteps(nextSteps);
    setResults(nextResults);
    setTrace((prev) => mergeTraceEvents(prev, stateTrace));

    const normalizedAssignments = normalizeAgentAssignments({
      rawAssignments: state.agent_assignments,
      steps: nextSteps,
      results: nextResults,
      trace: stateTrace,
    });
    setAgentAssignments(normalizedAssignments);

    const normalizedContributions = normalizeAgentContributions({
      rawContributions: state.agent_contributions,
      assignments: normalizedAssignments,
      results: nextResults,
    });
    setAgentContributions(normalizedContributions);

    setParallelLevels(deriveParallelLevels({ steps: nextSteps, results: nextResults, trace: stateTrace }));
  }, [taskId]);

  const refreshGlobalMetrics = useCallback(async () => {
    const [aggregateResponse, providerResponse] = await Promise.all([
      getAggregateMetrics(),
      getProviderMetrics(),
    ]);

    if (aggregateResponse.success) {
      setAggregateMetrics(aggregateResponse.data || null);
    }
    if (providerResponse.success) {
      setProviderMetrics(providerResponse.data || null);
    }
  }, []);

  const refreshTaskSnapshot = useCallback(async (id) => {
    if (!id || syncInFlightRef.current) {
      return;
    }

    syncInFlightRef.current = true;
    try {
      const [taskResponse, traceResponse, summaryResponse, taskMetricsResponse] = await Promise.all([
        getTask(id),
        getTrace(id),
        getTraceSummary(id),
        getTaskMetrics(id),
      ]);

      if (taskResponse.success && taskResponse.data) {
        hydrateFromState(taskResponse.data);
      }

      if (traceResponse.success) {
        const incomingTrace = Array.isArray(traceResponse.data?.trace)
          ? traceResponse.data.trace
          : Array.isArray(traceResponse.trace)
            ? traceResponse.trace
            : [];
        if (incomingTrace.length > 0) {
          setTrace((prev) => mergeTraceEvents(prev, incomingTrace));
        }
      }

      if (summaryResponse.success) {
        setTraceSummary(summaryResponse.data || null);
      }

      if (taskMetricsResponse.success) {
        setTaskMetrics(taskMetricsResponse.data || null);
      }
    } finally {
      syncInFlightRef.current = false;
    }
  }, [hydrateFromState]);

  const loadRuntimeSettings = useCallback(async () => {
    const response = await getRuntimeConfig();
    if (response.success && response.data) {
      const merged = {
        ...DEFAULT_RUNTIME_CONFIG,
        ...loadStoredRuntimeConfig(),
        ...response.data,
      };
      setRuntimeConfig(merged);
      persistRuntimeConfig(merged);
      return;
    }

    setRuntimeConfig((prev) => ({
      ...DEFAULT_RUNTIME_CONFIG,
      ...prev,
    }));
  }, []);

  const updateRuntimeSettings = useCallback(async (patch) => {
    if (!patch || typeof patch !== "object") {
      return {
        success: false,
        error: "Settings patch is required",
      };
    }

    const optimistic = {
      ...runtimeConfig,
      ...patch,
    };
    setRuntimeConfig(optimistic);
    persistRuntimeConfig(optimistic);

    const response = await updateRuntimeConfig(patch);
    if (!response.success) {
      setError(response.error || "Failed to update runtime settings");
      return response;
    }

    if (response.data && typeof response.data === "object") {
      const merged = {
        ...optimistic,
        ...response.data,
      };
      setRuntimeConfig(merged);
      persistRuntimeConfig(merged);
    }

    return response;
  }, [runtimeConfig]);

  const submitTask = useCallback(async (descriptionOrPayload) => {
    setIsLoading(true);
    setError(null);

    try {
      let currentTaskId = null;
      let initialSteps = [];
      let initialTaskData = null;

      if (descriptionOrPayload && typeof descriptionOrPayload === "object" && descriptionOrPayload.task_id) {
        currentTaskId = String(descriptionOrPayload.task_id);
        initialSteps = Array.isArray(descriptionOrPayload.steps) ? descriptionOrPayload.steps : [];
        initialTaskData = descriptionOrPayload;
      } else {
        const createResponse = await createTask(descriptionOrPayload);
        if (!createResponse.success) {
          throw new Error(createResponse.error || "Failed to create task");
        }

        currentTaskId = String(createResponse.data?.task_id || createResponse.task_id || "");
        initialSteps = Array.isArray(createResponse.data?.steps)
          ? createResponse.data.steps
          : Array.isArray(createResponse.steps)
            ? createResponse.steps
            : [];
        initialTaskData = createResponse.data || createResponse;
      }

      if (!currentTaskId) {
        throw new Error("Task id missing from create response");
      }

      setTaskId(currentTaskId);
      setTask((prev) => ({
        ...(prev || {}),
        ...(initialTaskData || {}),
        task_id: currentTaskId,
      }));
      setSteps(initialSteps);
      setResults([]);
      setTrace([]);
      setTraceSummary(null);
      setTaskMetrics(null);
      setParallelLevels([]);
      setAgentAssignments([]);
      setAgentContributions({});
      processedEventsRef.current = 0;

      const executeResponse = await executeTask(currentTaskId);
      if (!executeResponse.success) {
        throw new Error(executeResponse.error || "Failed to start task execution");
      }

      await Promise.all([
        refreshTaskSnapshot(currentTaskId),
        refreshGlobalMetrics(),
      ]);

      return {
        success: true,
        taskId: currentTaskId,
      };
    } catch (submitError) {
      setError(submitError?.message || "Task submission failed");
      return {
        success: false,
        error: submitError?.message || "Task submission failed",
      };
    } finally {
      setIsLoading(false);
    }
  }, [refreshTaskSnapshot]);

  useEffect(() => {
    loadRuntimeSettings();
    refreshGlobalMetrics();
  }, [loadRuntimeSettings, refreshGlobalMetrics]);

  useEffect(() => () => {
    if (refreshTimerRef.current) {
      window.clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = null;
    }
  }, []);

  const scheduleRefresh = useCallback((id) => {
    if (!id) {
      return;
    }

    if (refreshTimerRef.current) {
      window.clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = null;
    }

    refreshTimerRef.current = window.setTimeout(() => {
      refreshTaskSnapshot(id);
      refreshTimerRef.current = null;
    }, 180);
  }, [refreshTaskSnapshot]);

  useEffect(() => {
    if (!events.length) {
      return;
    }

    const newEvents = events.slice(processedEventsRef.current);
    if (!newEvents.length) {
      return;
    }

    processedEventsRef.current = events.length;

    const normalizedEvents = newEvents
      .filter((entry) => entry?.event_type !== "ping")
      .map((entry) => ({ ...entry, source: entry?.source || "stream" }));

    setTrace((prev) => mergeTraceEvents(prev, normalizedEvents));

    const mergedTrace = mergeTraceEvents(trace, normalizedEvents);
    setParallelLevels((current) => {
      const derived = deriveParallelLevels({ steps, results, trace: mergedTrace });
      return derived.length > 0 ? derived : current;
    });

    const derivedAssignments = normalizeAgentAssignments({
      rawAssignments: task?.agent_assignments,
      steps,
      results,
      trace: mergedTrace,
    });
    if (derivedAssignments.length > 0) {
      setAgentAssignments(derivedAssignments);
      setAgentContributions((current) => {
        const normalized = normalizeAgentContributions({
          rawContributions: task?.agent_contributions || current,
          assignments: derivedAssignments,
          results,
        });
        return Object.keys(normalized).length > 0 ? normalized : current;
      });
    }

    const latest = newEvents[newEvents.length - 1];
    if (!latest) {
      return;
    }

    const eventType = String(latest.event_type || "").toLowerCase();
    if (eventType === "poll_state") {
      const state = latest?.data?.state;
      if (state) {
        hydrateFromState(state);
      }
      return;
    }

    const needsRefresh = newEvents.some((entry) => REFRESH_EVENT_TYPES.has(String(entry?.event_type || "").toLowerCase()));

    if (needsRefresh) {
      scheduleRefresh(taskId);
    }

    if (eventType === "task_completed" || eventType === "task_failed") {
      refreshGlobalMetrics();
    }
  }, [events, hydrateFromState, refreshGlobalMetrics, results, scheduleRefresh, steps, task, taskId, trace]);

  useEffect(() => {
    if (socketError) {
      setError(socketError);
    }
  }, [socketError]);

  const metrics = useMemo(() => {
    const baseMetrics = taskMetrics && typeof taskMetrics === "object" ? taskMetrics : null;

    const totalTokens = Number(task?.llm_tokens_used || 0) || results.reduce((sum, item) => sum + Number(item?.tokens_used || 0), 0);

    const estimatedCost = Number(
      baseMetrics?.total_cost_usd ||
      task?.final_output?.summary?.estimated_cost_usd ||
      0,
    );
    const fallbackEstimatedCost = estimatedCost > 0 ? estimatedCost : Number((totalTokens / 1_000_000) * 3.5).toFixed(6);

    const startedAt = task?.started_at ? new Date(task.started_at).getTime() : null;
    const completedAt = task?.completed_at ? new Date(task.completed_at).getTime() : null;
    const endTime = completedAt || Date.now();
    const totalTime = startedAt ? Math.max(0, Math.round((endTime - startedAt) / 1000)) : 0;

    const retryCount = baseMetrics?.retry_count ?? (
      task?.retry_counts
        ? Object.values(task.retry_counts).reduce((sum, value) => sum + Number(value || 0), 0)
        : trace.filter((event) => String(event?.event_type || "") === "retry_triggered").length
    );

    const reflectionCount = baseMetrics?.reflection_count ?? (
      task?.reflection_counts
        ? Object.values(task.reflection_counts).reduce((sum, value) => sum + Number(value || 0), 0)
        : trace.filter((event) => String(event?.event_type || "") === "reflection_completed").length
    );

    const fallbackCount = trace.filter((event) => String(event?.event_type || "") === "fallback_triggered").length;

    const activeLevels = parallelLevels.filter((level) => (level.stepIds || []).length > 1);
    const parallelLaneCount = activeLevels.reduce((max, level) => Math.max(max, (level.stepIds || []).length), 1);

    const agentCount = Object.keys(agentContributions || {}).length;

    return {
      totalTokens,
      estimatedCost: Number(fallbackEstimatedCost),
      totalTime,
      retryCount: Number(retryCount || 0),
      fallbackCount,
      reflectionCount: Number(reflectionCount || 0),
      isConnected,
      parallelLevelCount: parallelLevels.length,
      parallelLaneCount,
      activeParallelLevelCount: activeLevels.length,
      agentCount,
      completionRate: Number(aggregateMetrics?.completion_rate || 0),
      qualityScore: Number(aggregateMetrics?.avg_quality_score || 0),
    };
  }, [aggregateMetrics, agentContributions, isConnected, parallelLevels, results, task, taskMetrics, trace]);

  const clearError = useCallback(() => {
    setError(null);
  }, []);

  return {
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
    refreshTaskSnapshot,
    refreshGlobalMetrics,
    updateRuntimeSettings,
    clearError,
    isLoading,
    isConnected,
    error,
  };
}

function loadStoredRuntimeConfig() {
  try {
    const raw = window.localStorage.getItem(SETTINGS_STORAGE_KEY);
    if (!raw) {
      return { ...DEFAULT_RUNTIME_CONFIG };
    }
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") {
      return { ...DEFAULT_RUNTIME_CONFIG };
    }
    return {
      ...DEFAULT_RUNTIME_CONFIG,
      ...parsed,
    };
  } catch {
    return { ...DEFAULT_RUNTIME_CONFIG };
  }
}

function persistRuntimeConfig(config) {
  try {
    window.localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(config || DEFAULT_RUNTIME_CONFIG));
  } catch {
    // no-op
  }
}

function mergeTraceEvents(existingEvents, incomingEvents) {
  const safeExisting = Array.isArray(existingEvents) ? existingEvents : [];
  const safeIncoming = Array.isArray(incomingEvents) ? incomingEvents : [];

  if (safeIncoming.length === 0) {
    return safeExisting;
  }

  const seen = new Set();
  const merged = [];

  for (const item of [...safeExisting, ...safeIncoming]) {
    if (!item || typeof item !== "object") {
      continue;
    }
    const key = traceEventKey(item);
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    merged.push(item);
  }

  return merged.sort((left, right) => {
    const a = Date.parse(left.timestamp || "");
    const b = Date.parse(right.timestamp || "");
    if (!Number.isFinite(a) && !Number.isFinite(b)) {
      return 0;
    }
    if (!Number.isFinite(a)) {
      return 1;
    }
    if (!Number.isFinite(b)) {
      return -1;
    }
    return a - b;
  });
}

function traceEventKey(event) {
  const payload = event || {};
  const eventType = String(payload.event_type || payload.type || "unknown");
  const timestamp = String(payload.timestamp || "no-time");
  const stepId = String(payload.step_id || payload?.data?.step_id || payload?.details?.step_id || "no-step");
  const attempt = String(payload?.data?.attempt || payload?.details?.attempt || payload?.details?.retry_count || "");
  return `${timestamp}::${eventType}::${stepId}::${attempt}`;
}

function normalizeAgentAssignments({ rawAssignments, steps, results, trace }) {
  const assignmentMap = new Map();
  const stepNameById = new Map();

  (Array.isArray(steps) ? steps : []).forEach((step, index) => {
    const stepId = String(step?.step_id || step?.id || `step_${index + 1}`);
    const stepName = String(step?.name || step?.step_name || `Step ${index + 1}`);
    stepNameById.set(stepId, stepName);
  });

  if (Array.isArray(rawAssignments)) {
    rawAssignments.forEach((assignment) => {
      const stepId = String(assignment?.step_id || assignment?.id || "");
      if (!stepId) {
        return;
      }
      assignmentMap.set(stepId, {
        step_id: stepId,
        step_name: String(assignment?.step_name || stepNameById.get(stepId) || stepId),
        agent_name: String(assignment?.agent_name || assignment?.agent || "Unassigned"),
        agent_role: String(assignment?.agent_role || assignment?.role || ""),
        reason: String(assignment?.reason || assignment?.routing_reason || ""),
      });
    });
  } else if (rawAssignments && typeof rawAssignments === "object") {
    Object.entries(rawAssignments).forEach(([stepId, payload]) => {
      const assignment = payload && typeof payload === "object" ? payload : {};
      assignmentMap.set(String(stepId), {
        step_id: String(stepId),
        step_name: String(assignment.step_name || stepNameById.get(String(stepId)) || stepId),
        agent_name: String(assignment.agent_name || assignment.agent || "Unassigned"),
        agent_role: String(assignment.agent_role || assignment.role || ""),
        reason: String(assignment.reason || assignment.routing_reason || ""),
      });
    });
  }

  (Array.isArray(results) ? results : []).forEach((result, index) => {
    const stepId = String(result?.step_id || `step_${index + 1}`);
    if (!stepId) {
      return;
    }
    const existing = assignmentMap.get(stepId) || {
      step_id: stepId,
      step_name: stepNameById.get(stepId) || stepId,
      agent_name: "Unassigned",
      agent_role: "",
      reason: "",
    };
    const agentName = String(result?.agent_name || existing.agent_name || "Unassigned");
    if (!agentName || agentName === "Unassigned") {
      return;
    }

    assignmentMap.set(stepId, {
      ...existing,
      agent_name: agentName,
      agent_role: String(result?.agent_role || existing.agent_role || ""),
    });
  });

  (Array.isArray(trace) ? trace : []).forEach((event) => {
    const eventType = String(event?.event_type || "").toLowerCase();
    if (eventType !== "agent_assigned") {
      return;
    }

    const data = event?.data && typeof event.data === "object" ? event.data : {};
    const details = event?.details && typeof event.details === "object" ? event.details : {};
    const merged = { ...details, ...data };

    const stepId = String(event?.step_id || merged.step_id || "");
    if (!stepId) {
      return;
    }

    const existing = assignmentMap.get(stepId) || {
      step_id: stepId,
      step_name: String(merged.step_name || stepNameById.get(stepId) || stepId),
      agent_name: "Unassigned",
      agent_role: "",
      reason: "",
    };

    assignmentMap.set(stepId, {
      ...existing,
      step_name: String(merged.step_name || existing.step_name || stepNameById.get(stepId) || stepId),
      agent_name: String(merged.agent_name || merged.agent || existing.agent_name || "Unassigned"),
      agent_role: String(merged.agent_role || merged.role || existing.agent_role || ""),
      reason: String(merged.reason || merged.routing_reason || existing.reason || ""),
    });
  });

  const stepOrder = new Map();
  (Array.isArray(steps) ? steps : []).forEach((step, index) => {
    const stepId = String(step?.step_id || step?.id || `step_${index + 1}`);
    stepOrder.set(stepId, index);
  });

  return [...assignmentMap.values()].sort((left, right) => {
    const leftOrder = stepOrder.has(left.step_id) ? stepOrder.get(left.step_id) : Number.POSITIVE_INFINITY;
    const rightOrder = stepOrder.has(right.step_id) ? stepOrder.get(right.step_id) : Number.POSITIVE_INFINITY;

    if (leftOrder !== rightOrder) {
      return leftOrder - rightOrder;
    }

    return String(left.step_id).localeCompare(String(right.step_id));
  });
}

function normalizeAgentContributions({ rawContributions, assignments, results }) {
  if (rawContributions && typeof rawContributions === "object" && Object.keys(rawContributions).length > 0) {
    return rawContributions;
  }

  const contributions = {};
  const resultsByStepId = new Map();
  (Array.isArray(results) ? results : []).forEach((result, index) => {
    const stepId = String(result?.step_id || `step_${index + 1}`);
    resultsByStepId.set(stepId, result || {});
  });

  (Array.isArray(assignments) ? assignments : []).forEach((assignment) => {
    const agentName = String(assignment?.agent_name || "").trim();
    if (!agentName || agentName === "Unassigned") {
      return;
    }

    if (!contributions[agentName]) {
      contributions[agentName] = {
        steps_handled: 0,
        successful_steps: 0,
        failed_steps: 0,
        tokens_used: 0,
      };
    }

    const result = resultsByStepId.get(String(assignment.step_id)) || {};
    const status = String(result?.status || "").toLowerCase();

    contributions[agentName].steps_handled += 1;
    contributions[agentName].tokens_used += Number(result?.tokens_used || 0);

    if (status === "success" || status === "completed") {
      contributions[agentName].successful_steps += 1;
    }

    if (status === "failed") {
      contributions[agentName].failed_steps += 1;
    }
  });

  return contributions;
}

function deriveParallelLevels({ steps, results, trace }) {
  const safeSteps = Array.isArray(steps) ? steps : [];
  const safeResults = Array.isArray(results) ? results : [];
  const safeTrace = Array.isArray(trace) ? trace : [];

  const levelByStepId = new Map();

  safeSteps.forEach((step, index) => {
    const stepId = String(step?.step_id || step?.id || `step_${index + 1}`);
    const explicitLevel = Number(step?.parallel_level ?? step?.parallel_group ?? step?.level);
    if (Number.isFinite(explicitLevel) && explicitLevel >= 0) {
      levelByStepId.set(stepId, explicitLevel);
    }
  });

  safeResults.forEach((result, index) => {
    const stepId = String(result?.step_id || `step_${index + 1}`);
    const level = Number(result?.parallel_level ?? result?.parallel_group ?? result?.level);
    if (Number.isFinite(level) && level >= 0 && !levelByStepId.has(stepId)) {
      levelByStepId.set(stepId, level);
    }
  });

  safeTrace.forEach((event) => {
    const eventType = String(event?.event_type || "").toLowerCase();
    if (!eventType.includes("step") && eventType !== "agent_assigned") {
      return;
    }

    const data = event?.data && typeof event.data === "object" ? event.data : {};
    const details = event?.details && typeof event.details === "object" ? event.details : {};
    const merged = { ...details, ...data };

    const stepId = String(event?.step_id || merged.step_id || "");
    if (!stepId) {
      return;
    }

    const level = Number(merged.parallel_level ?? merged.parallel_group ?? merged.level);
    if (Number.isFinite(level) && level >= 0) {
      levelByStepId.set(stepId, level);
    }
  });

  const grouped = new Map();
  safeSteps.forEach((step, index) => {
    const stepId = String(step?.step_id || step?.id || `step_${index + 1}`);
    const level = levelByStepId.has(stepId) ? levelByStepId.get(stepId) : index;
    const bucket = grouped.get(level) || [];
    bucket.push(stepId);
    grouped.set(level, bucket);
  });

  return [...grouped.entries()]
    .sort((left, right) => Number(left[0]) - Number(right[0]))
    .map(([level, stepIds]) => ({
      level: Number(level),
      stepIds,
      stepCount: stepIds.length,
    }));
}
