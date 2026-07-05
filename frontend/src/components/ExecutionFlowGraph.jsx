import { AnimatePresence, motion } from "framer-motion";
import { useMemo } from "react";

const NODE_WIDTH = 238;
const NODE_HEIGHT = 108;
const LEVEL_GAP_Y = 132;
const NODE_GAP_X = 86;
const CANVAS_PADDING_X = 72;
const CANVAS_PADDING_TOP = 48;
const CANVAS_PADDING_BOTTOM = 64;

const STATUS_META = {
  success: {
    label: "Success",
    badge: "bg-emerald-500/20 text-emerald-200 border-emerald-400/45",
    stroke: "#22c55e",
  },
  completed: {
    label: "Success",
    badge: "bg-emerald-500/20 text-emerald-200 border-emerald-400/45",
    stroke: "#22c55e",
  },
  failed: {
    label: "Failed",
    badge: "bg-rose-500/20 text-rose-200 border-rose-400/50",
    stroke: "#ef4444",
  },
  retrying: {
    label: "Retry",
    badge: "bg-amber-500/20 text-amber-200 border-amber-400/50",
    stroke: "#f59e0b",
  },
  reflecting: {
    label: "Reflect",
    badge: "bg-fuchsia-500/20 text-fuchsia-200 border-fuchsia-400/50",
    stroke: "#a855f7",
  },
  running: {
    label: "Running",
    badge: "bg-sky-500/20 text-sky-100 border-sky-400/50",
    stroke: "#3b82f6",
  },
  pending: {
    label: "Pending",
    badge: "bg-slate-500/20 text-slate-200 border-slate-400/40",
    stroke: "#64748b",
  },
  skipped: {
    label: "Skipped",
    badge: "bg-slate-500/20 text-slate-200 border-slate-400/40",
    stroke: "#94a3b8",
  },
};

const AGENT_META = {
  research: { label: "Research", icon: "🔬", badge: "bg-sky-500/20 text-sky-200 border-sky-400/40" },
  code: { label: "Code", icon: "💻", badge: "bg-emerald-500/20 text-emerald-200 border-emerald-400/40" },
  analysis: { label: "Analysis", icon: "📊", badge: "bg-amber-500/20 text-amber-200 border-amber-400/40" },
  writing: { label: "Writing", icon: "✍️", badge: "bg-violet-500/20 text-violet-200 border-violet-400/40" },
};

const DEFAULT_STATUS = STATUS_META.pending;

export default function ExecutionFlowGraph({
  steps = [],
  results = [],
  trace = [],
  currentStepIndex = 0,
  onStepClick,
}) {
  const graph = useMemo(
    () => buildGraphModel({ steps, results, trace, currentStepIndex }),
    [currentStepIndex, results, steps, trace],
  );

  const isScrollableY = graph.levels.length > 6;

  return (
    <section className="rounded-2xl border border-white/10 bg-[var(--bg-card)]/70 p-4 sm:p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-[var(--text-primary)]">Execution Flow Graph</h2>
          <p className="text-xs text-[var(--text-secondary)]">
            DAG trace with retries, reflection branches, fallback switches, and parallel lanes.
          </p>
        </div>
        <span className="rounded-full border border-white/15 bg-white/5 px-3 py-1 text-xs text-[var(--text-secondary)]">
          {graph.nodes.length} step{graph.nodes.length === 1 ? "" : "s"}
        </span>
      </div>

      {graph.nodes.length === 0 ? (
        <div className="rounded-xl border border-white/10 bg-[#0b1328]/80 p-5 text-sm text-[var(--text-secondary)]">
          Waiting for execution plan to render flow graph.
        </div>
      ) : (
        <div className={`overflow-x-auto ${isScrollableY ? "max-h-[820px] overflow-y-auto pr-1" : ""}`}>
          <svg
            viewBox={`0 0 ${graph.width} ${graph.height}`}
            className="min-w-[920px]"
            style={{ width: graph.width, height: graph.height }}
          >
            <defs>
              <marker id="edge-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M0,0 L8,4 L0,8 z" fill="#94a3b8" />
              </marker>
              <marker id="retry-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M0,0 L8,4 L0,8 z" fill="#f59e0b" />
              </marker>
              <marker id="reflection-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M0,0 L8,4 L0,8 z" fill="#a855f7" />
              </marker>
              <filter id="active-glow" x="-30%" y="-30%" width="160%" height="160%">
                <feGaussianBlur stdDeviation="3" result="blur" />
                <feMerge>
                  <feMergeNode in="blur" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
            </defs>

            {graph.levelBadges.map((badge) => (
              <g key={badge.id}>
                <path
                  d={badge.path}
                  stroke="rgba(148,163,184,0.38)"
                  strokeWidth="1.5"
                  fill="none"
                  strokeDasharray="4 4"
                />
                <foreignObject x={badge.labelX} y={badge.labelY} width={130} height={24}>
                  <div className="inline-flex items-center gap-1 rounded-full border border-sky-400/40 bg-sky-500/15 px-2 py-0.5 text-[11px] font-semibold text-sky-200">
                    ⚡ Parallel ({badge.size} steps)
                  </div>
                </foreignObject>
              </g>
            ))}

            {graph.edges.map((edge, index) => (
              <motion.path
                key={edge.id}
                d={edge.path}
                stroke="rgba(148,163,184,0.9)"
                strokeWidth="2"
                fill="none"
                markerEnd="url(#edge-arrow)"
                initial={{ pathLength: 0, opacity: 0.2 }}
                animate={{ pathLength: 1, opacity: 1 }}
                transition={{ duration: 0.42, delay: Math.min(index * 0.06, 0.52) }}
              />
            ))}

            {graph.retryLoops.map((loop, index) => (
              <g key={loop.id}>
                <motion.path
                  d={loop.path}
                  stroke="#f59e0b"
                  strokeWidth="2"
                  fill="none"
                  strokeDasharray="6 6"
                  markerEnd="url(#retry-arrow)"
                  initial={{ pathLength: 0, opacity: 0.2 }}
                  animate={{ pathLength: 1, opacity: 1 }}
                  transition={{ duration: 0.4, delay: Math.min(index * 0.08, 0.48) }}
                />
                <foreignObject x={loop.labelX} y={loop.labelY} width={86} height={20}>
                  <div className="inline-flex rounded-full border border-amber-400/40 bg-amber-500/15 px-2 py-0.5 text-[10px] font-semibold text-amber-200">
                    Retry {loop.retryCount}/3
                  </div>
                </foreignObject>
              </g>
            ))}

            {graph.reflectionBranches.map((branch, index) => (
              <g key={branch.id}>
                <motion.path
                  d={branch.path}
                  stroke="#a855f7"
                  strokeWidth="2"
                  fill="none"
                  markerEnd="url(#reflection-arrow)"
                  initial={{ pathLength: 0, opacity: 0.25 }}
                  animate={{ pathLength: 1, opacity: 1 }}
                  transition={{ duration: 0.4, delay: Math.min(index * 0.08, 0.6) }}
                />

                <foreignObject x={branch.nodeX} y={branch.nodeY} width={110} height={46}>
                  <div className="h-full rounded-lg border border-fuchsia-400/35 bg-fuchsia-500/15 px-2 py-1 text-[10px] text-fuchsia-100">
                    <p className="font-semibold">Reflector</p>
                    <p className="opacity-80">{branch.reflectionLabel}</p>
                  </div>
                </foreignObject>
              </g>
            ))}

            {graph.fallbackMarkers.map((marker) => (
              <g key={marker.id}>
                <text x={marker.x} y={marker.y} fontSize="14" fill="#ef4444" textAnchor="middle">
                  ✖
                </text>
                <text x={marker.x + 34} y={marker.y + 1} fontSize="10" fill="#fecaca" textAnchor="start">
                  → {marker.toProvider}
                </text>
              </g>
            ))}

            <AnimatePresence>
              {graph.nodes.map((node) => {
                const statusMeta = STATUS_META[node.status] || DEFAULT_STATUS;
                const agentMeta = resolveAgentMeta(node.agentName);
                const nodeDelay = Math.min(node.level * 0.14 + node.indexInLevel * 0.07, 0.75);

                return (
                  <motion.g
                    key={node.id}
                    initial={{ opacity: 0, y: 14 }}
                    animate={{
                      opacity: 1,
                      y: 0,
                      x: node.status === "failed" ? [0, -3, 4, -2, 3, 0] : 0,
                    }}
                    transition={{ duration: 0.34, delay: nodeDelay }}
                    onClick={() => {
                      if (typeof onStepClick === "function") {
                        onStepClick(node.rawStep, node);
                      }
                    }}
                    style={{ cursor: typeof onStepClick === "function" ? "pointer" : "default" }}
                  >
                    <rect
                      x={node.x}
                      y={node.y}
                      rx="16"
                      ry="16"
                      width={NODE_WIDTH}
                      height={NODE_HEIGHT}
                      fill="rgba(10,18,39,0.92)"
                      stroke={statusMeta.stroke}
                      strokeOpacity="0.45"
                      strokeWidth="1.6"
                    />

                    {node.isActive ? (
                      <motion.rect
                        x={node.x - 2}
                        y={node.y - 2}
                        rx="18"
                        ry="18"
                        width={NODE_WIDTH + 4}
                        height={NODE_HEIGHT + 4}
                        fill="none"
                        stroke="#60a5fa"
                        strokeWidth="2"
                        filter="url(#active-glow)"
                        animate={{ opacity: [0.3, 0.95, 0.3] }}
                        transition={{ duration: 1.15, repeat: Number.POSITIVE_INFINITY }}
                      />
                    ) : null}

                    <foreignObject x={node.x + 10} y={node.y + 8} width={NODE_WIDTH - 20} height={NODE_HEIGHT - 16}>
                      <div className="flex h-full flex-col justify-between text-[11px] text-[var(--text-primary)]">
                        <div className="flex items-start justify-between gap-2">
                          <p className="line-clamp-2 text-sm font-semibold leading-tight text-[var(--text-primary)]">
                            {node.name}
                          </p>
                          <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${statusMeta.badge}`}>
                            {statusMeta.label}
                          </span>
                        </div>

                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="rounded-full border border-white/15 bg-white/5 px-2 py-0.5 text-[10px] text-[var(--text-secondary)]">
                            {formatDuration(node.durationMs)}
                          </span>

                          {agentMeta ? (
                            <span className={`rounded-full border px-2 py-0.5 text-[10px] ${agentMeta.badge}`}>
                              {agentMeta.icon} {agentMeta.label}
                            </span>
                          ) : null}

                          {node.modelLabel ? (
                            <span className="rounded-full border border-violet-400/35 bg-violet-500/15 px-2 py-0.5 text-[10px] text-violet-100">
                              {node.modelLabel}
                            </span>
                          ) : null}
                        </div>
                      </div>
                    </foreignObject>

                    {node.isComplete ? (
                      <motion.g
                        initial={{ scale: 0, opacity: 0 }}
                        animate={{ scale: 1, opacity: 1 }}
                        transition={{ delay: nodeDelay + 0.2, duration: 0.2 }}
                      >
                        <circle cx={node.x + NODE_WIDTH - 14} cy={node.y + 14} r="9" fill="#10b981" />
                        <path
                          d={`M ${node.x + NODE_WIDTH - 18} ${node.y + 14} L ${node.x + NODE_WIDTH - 15} ${node.y + 17} L ${node.x + NODE_WIDTH - 10} ${node.y + 11}`}
                          stroke="white"
                          strokeWidth="1.6"
                          fill="none"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        />
                      </motion.g>
                    ) : null}
                  </motion.g>
                );
              })}
            </AnimatePresence>
          </svg>
        </div>
      )}
    </section>
  );
}

function buildGraphModel({ steps, results, trace, currentStepIndex }) {
  const normalizedSteps = (Array.isArray(steps) ? steps : []).map((step, index) => ({
    ...step,
    step_id: String(step?.step_id || `step_${index + 1}`),
    name: String(step?.name || `Step ${index + 1}`),
    dependencies: Array.isArray(step?.dependencies)
      ? step.dependencies.map((dep) => String(dep)).filter(Boolean)
      : [],
  }));

  if (!normalizedSteps.length) {
    return {
      nodes: [],
      edges: [],
      levels: [],
      retryLoops: [],
      reflectionBranches: [],
      fallbackMarkers: [],
      levelBadges: [],
      width: 960,
      height: 420,
    };
  }

  const resultById = new Map();
  for (const result of Array.isArray(results) ? results : []) {
    const stepId = String(result?.step_id || "").trim();
    if (stepId) {
      resultById.set(stepId, result);
    }
  }

  const traceByStep = new Map();
  for (const event of Array.isArray(trace) ? trace : []) {
    const stepId = String(event?.step_id || event?.data?.step_id || "").trim();
    if (!stepId) {
      continue;
    }

    if (!traceByStep.has(stepId)) {
      traceByStep.set(stepId, []);
    }
    traceByStep.get(stepId).push(event);
  }

  for (const events of traceByStep.values()) {
    events.sort((a, b) => toTimestamp(a?.timestamp) - toTimestamp(b?.timestamp));
  }

  const { levels, levelByStepId } = computeExecutionLevels(normalizedSteps);

  const nodes = normalizedSteps.map((step, index) => {
    const stepTrace = traceByStep.get(step.step_id) || [];
    const result = resultById.get(step.step_id) || null;

    const retryCountFromTrace = stepTrace.filter((event) => String(event?.event_type || "") === "retry_triggered").length;
    const reflectionEvents = stepTrace.filter((event) => {
      const type = String(event?.event_type || "");
      return type === "reflection_started" || type === "reflection_completed";
    });
    const fallbackEvent = [...stepTrace]
      .reverse()
      .find((event) => String(event?.event_type || "") === "fallback_triggered");

    const status = resolveStepStatus({
      step,
      result,
      index,
      currentStepIndex,
      traceEvents: stepTrace,
      totalSteps: normalizedSteps.length,
    });

    const latestEventWithModel = [...stepTrace]
      .reverse()
      .find((event) => event?.model_used || event?.details?.model_used || event?.data?.model_used);

    const latestEventWithAgent = [...stepTrace]
      .reverse()
      .find((event) => event?.agent_name || event?.details?.agent_name || event?.data?.agent_name);

    const level = Number.isFinite(Number(step?.level))
      ? Number(step.level)
      : levelByStepId.get(step.step_id) || 0;

    return {
      id: step.step_id,
      name: step.name,
      level,
      index,
      rawStep: step,
      dependencies: step.dependencies.filter((dependencyId) => normalizedSteps.some((item) => item.step_id === dependencyId)),
      status,
      durationMs: toNumber(
        result?.latency_ms
          ?? step?.duration_ms
          ?? latestEventWithModel?.duration_ms
          ?? latestEventWithModel?.details?.duration_ms,
      ),
      agentName: String(
        result?.agent_name
          ?? step?.agent_name
          ?? latestEventWithAgent?.agent_name
          ?? latestEventWithAgent?.details?.agent_name
          ?? "",
      ).trim(),
      modelLabel: normalizeModelLabel(
        String(
          result?.model_used
            ?? step?.model_used
            ?? latestEventWithModel?.model_used
            ?? latestEventWithModel?.details?.model_used
            ?? "",
        ),
      ),
      retryCount: Math.max(toNumber(result?.retry_count), retryCountFromTrace),
      reflectionCount: reflectionEvents.length,
      reflectionLabel: normalizeReflectionLabel(reflectionEvents),
      fallbackToProvider: String(
        fallbackEvent?.to_provider
          ?? fallbackEvent?.details?.to_provider
          ?? fallbackEvent?.data?.to_provider
          ?? "",
      ).trim(),
      isComplete: status === "success" || status === "completed",
      isActive: status === "running",
    };
  });

  const levelsSorted = levels
    .map((levelItems, levelIndex) => ({
      id: `level-${levelIndex}`,
      index: levelIndex,
      stepIds: levelItems,
    }))
    .filter((level) => level.stepIds.length > 0);

  const maxInLevel = Math.max(...levelsSorted.map((level) => level.stepIds.length));
  const width = Math.max(
    940,
    CANVAS_PADDING_X * 2 + maxInLevel * NODE_WIDTH + (maxInLevel - 1) * NODE_GAP_X + 180,
  );
  const height = Math.max(
    360,
    CANVAS_PADDING_TOP + levelsSorted.length * (NODE_HEIGHT + LEVEL_GAP_Y) - LEVEL_GAP_Y + CANVAS_PADDING_BOTTOM,
  );

  const positionById = new Map();

  for (const level of levelsSorted) {
    const span = level.stepIds.length * NODE_WIDTH + Math.max(0, level.stepIds.length - 1) * NODE_GAP_X;
    const levelStartX = (width - span) / 2;
    const y = CANVAS_PADDING_TOP + level.index * (NODE_HEIGHT + LEVEL_GAP_Y);

    for (let i = 0; i < level.stepIds.length; i += 1) {
      const stepId = level.stepIds[i];
      const x = levelStartX + i * (NODE_WIDTH + NODE_GAP_X);
      positionById.set(stepId, { x, y, level: level.index, indexInLevel: i, levelSize: level.stepIds.length });
    }
  }

  for (const node of nodes) {
    const position = positionById.get(node.id);
    if (position) {
      node.x = position.x;
      node.y = position.y;
      node.indexInLevel = position.indexInLevel;
    }
  }

  const edges = [];
  const addedEdgeIds = new Set();

  for (const node of nodes) {
    const targetPosition = positionById.get(node.id);
    if (!targetPosition) {
      continue;
    }

    let dependencies = [...node.dependencies];

    if (dependencies.length === 0 && node.level > 0) {
      const fallbackParent = [...nodes]
        .slice(0, node.index)
        .reverse()
        .find((candidate) => candidate.level < node.level);

      if (fallbackParent) {
        dependencies = [fallbackParent.id];
      }
    }

    for (const dependencyId of dependencies) {
      const sourcePosition = positionById.get(dependencyId);
      if (!sourcePosition) {
        continue;
      }

      const edgeId = `${dependencyId}->${node.id}`;
      if (addedEdgeIds.has(edgeId)) {
        continue;
      }
      addedEdgeIds.add(edgeId);

      edges.push({
        id: edgeId,
        from: dependencyId,
        to: node.id,
        path: buildEdgePath(sourcePosition, targetPosition),
      });
    }
  }

  const retryLoops = nodes
    .filter((node) => node.retryCount > 0)
    .map((node) => ({
      id: `retry-${node.id}`,
      retryCount: node.retryCount,
      path: buildRetryLoopPath(node),
      labelX: node.x + NODE_WIDTH + 18,
      labelY: node.y + 20,
    }));

  const reflectionBranches = nodes
    .filter((node) => node.reflectionCount > 0)
    .map((node) => {
      const branchNodeX = node.x + NODE_WIDTH + 52;
      const branchNodeY = node.y + Math.max(8, NODE_HEIGHT / 2 - 22);
      return {
        id: `reflect-${node.id}`,
        nodeX: branchNodeX,
        nodeY: branchNodeY,
        reflectionLabel: node.reflectionLabel,
        path: `M ${node.x + NODE_WIDTH} ${node.y + NODE_HEIGHT / 2} C ${node.x + NODE_WIDTH + 20} ${node.y + NODE_HEIGHT / 2} ${branchNodeX - 16} ${branchNodeY + 22} ${branchNodeX} ${branchNodeY + 22}`,
      };
    });

  const fallbackMarkers = nodes
    .filter((node) => node.fallbackToProvider)
    .map((node) => ({
      id: `fallback-${node.id}`,
      x: node.x + NODE_WIDTH - 2,
      y: node.y + NODE_HEIGHT + 18,
      toProvider: normalizeModelLabel(node.fallbackToProvider) || node.fallbackToProvider,
    }));

  const levelBadges = levelsSorted
    .filter((level) => level.stepIds.length > 1)
    .map((level) => {
      const levelNodes = level.stepIds
        .map((stepId) => positionById.get(stepId))
        .filter(Boolean);

      const minX = Math.min(...levelNodes.map((position) => position.x)) - 18;
      const maxX = Math.max(...levelNodes.map((position) => position.x + NODE_WIDTH)) + 18;
      const topY = levelNodes[0].y - 12;
      const bottomY = levelNodes[0].y + NODE_HEIGHT + 12;

      return {
        id: `badge-${level.id}`,
        size: level.stepIds.length,
        labelX: (minX + maxX) / 2 - 65,
        labelY: topY - 22,
        path: `M ${minX} ${topY} V ${bottomY} M ${maxX} ${topY} V ${bottomY} M ${minX} ${topY} H ${maxX} M ${minX} ${bottomY} H ${maxX}`,
      };
    });

  return {
    width,
    height,
    nodes,
    edges,
    levels: levelsSorted,
    retryLoops,
    reflectionBranches,
    fallbackMarkers,
    levelBadges,
  };
}

function resolveStepStatus({ step, result, index, currentStepIndex, traceEvents, totalSteps }) {
  const rawStatus = String(result?.status || step?.status || "").toLowerCase().trim();
  if (rawStatus) {
    return rawStatus;
  }

  if (traceEvents.some((event) => String(event?.event_type || "") === "step_failed")) {
    return "failed";
  }
  if (traceEvents.some((event) => String(event?.event_type || "") === "reflection_started")) {
    return "reflecting";
  }
  if (traceEvents.some((event) => String(event?.event_type || "") === "retry_triggered")) {
    return "retrying";
  }

  if (index < currentStepIndex) {
    return "success";
  }
  if (index === currentStepIndex && currentStepIndex < totalSteps) {
    return "running";
  }
  return "pending";
}

function computeExecutionLevels(steps) {
  const ids = steps.map((step) => step.step_id);
  const idSet = new Set(ids);
  const dependencyMap = new Map();

  for (const step of steps) {
    dependencyMap.set(
      step.step_id,
      new Set((step.dependencies || []).filter((dependencyId) => idSet.has(dependencyId))),
    );
  }

  const assigned = new Set();
  const levels = [];

  while (assigned.size < steps.length) {
    const ready = [];

    for (const stepId of ids) {
      if (assigned.has(stepId)) {
        continue;
      }

      const dependencies = dependencyMap.get(stepId) || new Set();
      const allSatisfied = [...dependencies].every((dependencyId) => assigned.has(dependencyId));
      if (allSatisfied) {
        ready.push(stepId);
      }
    }

    if (ready.length === 0) {
      const unresolved = ids.filter((stepId) => !assigned.has(stepId));
      if (!unresolved.length) {
        break;
      }

      levels.push([unresolved[0]]);
      assigned.add(unresolved[0]);
      continue;
    }

    for (const stepId of ready) {
      assigned.add(stepId);
    }
    levels.push(ready);
  }

  const levelByStepId = new Map();
  levels.forEach((stepIds, levelIndex) => {
    stepIds.forEach((stepId) => levelByStepId.set(stepId, levelIndex));
  });

  return { levels, levelByStepId };
}

function buildEdgePath(from, to) {
  const fromX = from.x + NODE_WIDTH / 2;
  const fromY = from.y + NODE_HEIGHT;
  const toX = to.x + NODE_WIDTH / 2;
  const toY = to.y;

  const deltaY = Math.max(42, (toY - fromY) * 0.46);
  return `M ${fromX} ${fromY} C ${fromX} ${fromY + deltaY} ${toX} ${toY - deltaY} ${toX} ${toY}`;
}

function buildRetryLoopPath(node) {
  const startX = node.x + NODE_WIDTH - 12;
  const startY = node.y + 14;
  const rightX = node.x + NODE_WIDTH + 32;
  const bottomY = node.y + NODE_HEIGHT - 12;
  const endX = node.x + NODE_WIDTH - 18;

  return `M ${startX} ${startY} C ${rightX} ${startY} ${rightX} ${bottomY} ${endX} ${bottomY}`;
}

function resolveAgentMeta(agentName) {
  const normalized = String(agentName || "").toLowerCase();
  if (!normalized) {
    return null;
  }

  if (normalized.includes("research")) {
    return AGENT_META.research;
  }
  if (normalized.includes("code")) {
    return AGENT_META.code;
  }
  if (normalized.includes("analysis")) {
    return AGENT_META.analysis;
  }
  if (normalized.includes("writing")) {
    return AGENT_META.writing;
  }
  return null;
}

function normalizeModelLabel(model) {
  const normalized = String(model || "").trim().toLowerCase();
  if (!normalized) {
    return "";
  }

  if (normalized.includes("gpt-4o-mini")) {
    return "GPT-4o-mini";
  }
  if (normalized.includes("gpt-4o")) {
    return "GPT-4o";
  }
  if (normalized.includes("claude")) {
    return "Claude";
  }
  return String(model || "").trim();
}

function normalizeReflectionLabel(reflectionEvents) {
  const completed = [...reflectionEvents]
    .reverse()
    .find((event) => String(event?.event_type || "") === "reflection_completed");

  const action = String(completed?.details?.action || completed?.data?.action || "").trim();
  if (!action) {
    return "Branch";
  }
  return action.replace(/_/g, " ");
}

function formatDuration(durationMs) {
  const safeDuration = Math.max(0, toNumber(durationMs));
  if (!safeDuration) {
    return "0.0s";
  }

  return `${(safeDuration / 1000).toFixed(1)}s`;
}

function toNumber(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
}

function toTimestamp(value) {
  if (!value) {
    return 0;
  }
  const ms = Date.parse(String(value));
  return Number.isFinite(ms) ? ms : 0;
}
