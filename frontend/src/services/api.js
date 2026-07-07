import axios from "axios";

let apiUrl = import.meta.env.VITE_API_URL || "http://localhost:8000";
if (apiUrl && !apiUrl.startsWith("http")) {
  apiUrl = `https://${apiUrl}`;
}

const apiClient = axios.create({
  baseURL: apiUrl,
  timeout: 30000,
  headers: {
    "Content-Type": "application/json",
  },
});

function normalizeSuccess(payload) {
  const hasEnvelope =
    payload &&
    typeof payload === "object" &&
    (Object.prototype.hasOwnProperty.call(payload, "success") ||
      Object.prototype.hasOwnProperty.call(payload, "data") ||
      Object.prototype.hasOwnProperty.call(payload, "error"));

  if (hasEnvelope) {
    const success = typeof payload?.success === "boolean" ? payload.success : true;
    const data = payload?.data ?? null;
    const error = payload?.error ?? null;

    if (data && typeof data === "object" && !Array.isArray(data)) {
      return {
        success,
        data,
        error,
        ...data,
      };
    }

    return { success, data, error };
  }

  if (Array.isArray(payload)) {
    return {
      success: true,
      data: payload,
      error: null,
    };
  }

  if (payload && typeof payload === "object") {
    return {
      success: true,
      data: payload,
      error: null,
      ...payload,
    };
  }

  return {
    success: true,
    data: payload ?? null,
    error: null,
  };
}

function normalizeError(err) {
  const message =
    err?.response?.data?.error ||
    err?.response?.data?.detail ||
    err?.message ||
    "Unexpected API error";
  return {
    success: false,
    data: null,
    error: String(message),
  };
}

async function safeRequest(requestFn) {
  try {
    const response = await requestFn();
    return normalizeSuccess(response?.data);
  } catch (err) {
    return normalizeError(err);
  }
}

function ensureTaskId(taskId) {
  return String(taskId || "").trim();
}

function readLocalBool(key, fallbackValue) {
  try {
    const raw = window.localStorage.getItem(key);
    if (raw === null) {
      return Boolean(fallbackValue);
    }
    return raw === "true";
  } catch {
    return Boolean(fallbackValue);
  }
}

function readLocalNumber(key, fallbackValue) {
  try {
    const raw = window.localStorage.getItem(key);
    if (raw === null) {
      return Number(fallbackValue);
    }
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? parsed : Number(fallbackValue);
  } catch {
    return Number(fallbackValue);
  }
}

function readLocalJson(key, fallbackValue) {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) {
      return fallbackValue;
    }
    const parsed = JSON.parse(raw);
    return parsed ?? fallbackValue;
  } catch {
    return fallbackValue;
  }
}

const DEFAULT_PROVIDER_CHAIN = [
  { provider: "openai", model: "gpt-4o", label: "Primary" },
  { provider: "openai", model: "gpt-4o-mini", label: "Fast Fallback" },
  { provider: "anthropic", model: "claude-3-5-sonnet-20241022", label: "Writing Fallback" },
];

function normalizeTaskDescription(taskDescription) {
  if (typeof taskDescription === "string") {
    return taskDescription.trim();
  }

  if (taskDescription && typeof taskDescription === "object") {
    const candidate = taskDescription.task ?? taskDescription.input ?? "";
    return String(candidate).trim();
  }

  return "";
}

export async function createTask(taskDescription) {
  const task = normalizeTaskDescription(taskDescription);
  if (!task) {
    return {
      success: false,
      data: null,
      error: "Task description is required",
    };
  }

  return safeRequest(() => apiClient.post("/tasks", { task }));
}

export async function getTask(taskId) {
  const normalizedTaskId = ensureTaskId(taskId);
  if (!normalizedTaskId) {
    return {
      success: false,
      data: null,
      error: "Task id is required",
    };
  }

  return safeRequest(() => apiClient.get(`/tasks/${normalizedTaskId}`));
}

export async function executeTask(taskId) {
  const normalizedTaskId = ensureTaskId(taskId);
  if (!normalizedTaskId) {
    return {
      success: false,
      data: null,
      error: "Task id is required",
    };
  }

  return safeRequest(() => apiClient.post(`/tasks/${normalizedTaskId}/execute`));
}

export async function getTrace(taskId) {
  const normalizedTaskId = ensureTaskId(taskId);
  if (!normalizedTaskId) {
    return {
      success: false,
      data: null,
      error: "Task id is required",
    };
  }

  return safeRequest(() => apiClient.get(`/traces/${normalizedTaskId}`));
}

export async function getTraceSummary(taskId) {
  const normalizedTaskId = ensureTaskId(taskId);
  if (!normalizedTaskId) {
    return {
      success: false,
      data: null,
      error: "Task id is required",
    };
  }

  return safeRequest(() => apiClient.get(`/traces/${normalizedTaskId}/summary`));
}

export async function getTaskMetrics(taskId) {
  const normalizedTaskId = ensureTaskId(taskId);
  if (!normalizedTaskId) {
    return {
      success: false,
      data: null,
      error: "Task id is required",
    };
  }

  return safeRequest(() => apiClient.get(`/metrics/${normalizedTaskId}`));
}

export async function getAggregateMetrics() {
  return safeRequest(() => apiClient.get("/metrics"));
}

export async function getProviderMetrics() {
  return safeRequest(() => apiClient.get("/metrics/providers"));
}

export async function getChaosMode() {
  const primary = await safeRequest(() => apiClient.get("/config/chaos"));
  if (primary.success) {
    return primary;
  }
  return safeRequest(() => apiClient.get("/config/chaos-mode"));
}

export async function setChaosMode(enabled) {
  const primary = await safeRequest(() => apiClient.post("/config/chaos", { enabled: Boolean(enabled) }));
  if (primary.success) {
    return primary;
  }
  return safeRequest(() => apiClient.post("/config/chaos-mode", { enabled: Boolean(enabled) }));
}

export async function getRuntimeConfig() {
  const configResponse = await safeRequest(() => apiClient.get("/config"));
  if (configResponse.success && configResponse.data) {
    return configResponse;
  }

  const chaosResponse = await getChaosMode();
  const fallbackData = {
    chaos_mode: Boolean(chaosResponse.data?.chaos_mode),
    parallel_mode: readLocalBool("parallel_mode", true),
    multi_agent_mode: readLocalBool("multi_agent_mode", true),
    max_retries: readLocalNumber("max_retries", 3),
    step_timeout: readLocalNumber("step_timeout", 60),
    providers: readLocalJson("provider_chain", DEFAULT_PROVIDER_CHAIN),
  };

  return {
    success: true,
    data: fallbackData,
    error: configResponse.error || chaosResponse.error || null,
  };
}

export async function updateRuntimeConfig(partialConfig) {
  if (!partialConfig || typeof partialConfig !== "object") {
    return {
      success: false,
      data: null,
      error: "Config payload is required",
    };
  }

  const configResponse = await safeRequest(() => apiClient.post("/config", partialConfig));
  if (configResponse.success) {
    return configResponse;
  }

  const results = [];

  if (Object.prototype.hasOwnProperty.call(partialConfig, "chaos_mode")) {
    const chaosResult = await setChaosMode(Boolean(partialConfig.chaos_mode));
    results.push(chaosResult);
  }

  if (Object.prototype.hasOwnProperty.call(partialConfig, "parallel_mode")) {
    results.push(
      await safeRequest(() =>
        apiClient.post("/config/parallel", { enabled: Boolean(partialConfig.parallel_mode) }),
      ),
    );
  }

  if (Object.prototype.hasOwnProperty.call(partialConfig, "multi_agent_mode")) {
    results.push(
      await safeRequest(() =>
        apiClient.post("/config/agents", { enabled: Boolean(partialConfig.multi_agent_mode) }),
      ),
    );
  }

  if (Object.prototype.hasOwnProperty.call(partialConfig, "max_retries")) {
    results.push(
      await safeRequest(() =>
        apiClient.post("/config/retries", { value: Number(partialConfig.max_retries) }),
      ),
    );
  }

  if (Object.prototype.hasOwnProperty.call(partialConfig, "step_timeout")) {
    results.push(
      await safeRequest(() =>
        apiClient.post("/config/timeout", { value: Number(partialConfig.step_timeout) }),
      ),
    );
  }

  if (Object.prototype.hasOwnProperty.call(partialConfig, "providers")) {
    results.push(
      await safeRequest(() =>
        apiClient.post("/config/providers", { providers: partialConfig.providers }),
      ),
    );
  }

  const successfulResult = results.find((result) => result?.success);
  if (successfulResult) {
    return {
      success: true,
      data: {
        ...partialConfig,
        ...(successfulResult.data || {}),
      },
      error: null,
    };
  }

  return {
    success: false,
    data: null,
    error: configResponse.error || results.find((item) => item?.error)?.error || "Config update failed",
  };
}

export default apiClient;
