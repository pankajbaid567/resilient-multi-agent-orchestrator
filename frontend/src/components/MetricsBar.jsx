import { useEffect, useMemo, useRef, useState } from "react";

export default function MetricsBar({ metrics = {} }) {
  const totalTokens = Number(metrics.totalTokens || 0);
  const estimatedCost = Number(metrics.estimatedCost || 0);
  const totalTimeValue = normalizeTime(metrics.totalTime);
  const retryCount = Number(metrics.retryCount || 0);
  const fallbackCount = Number(metrics.fallbackCount || 0);
  const reflectionCount = Number(metrics.reflectionCount || 0);
  const confidence = String(metrics.confidence || "Unknown");

  const animatedTokens = useCountUp(totalTokens, { duration: 700, decimals: 0 });
  const animatedCost = useCountUp(estimatedCost, { duration: 650, decimals: 4 });
  const animatedTime = useCountUp(totalTimeValue, { duration: 700, decimals: 0 });
  const animatedRetries = useCountUp(retryCount, { duration: 650, decimals: 0 });
  const animatedFallbacks = useCountUp(fallbackCount, { duration: 650, decimals: 0 });
  const animatedReflections = useCountUp(reflectionCount, { duration: 650, decimals: 0 });

  const confidenceMeta = useMemo(() => {
    const normalized = confidence.toLowerCase();
    if (normalized === "high") {
      return { label: "High", classes: "border-emerald-400/40 bg-emerald-500/20 text-emerald-100" };
    }
    if (normalized === "medium") {
      return { label: "Medium", classes: "border-amber-400/40 bg-amber-500/20 text-amber-100" };
    }
    if (normalized === "low") {
      return { label: "Low", classes: "border-red-400/40 bg-red-500/20 text-red-100" };
    }
    return { label: confidence, classes: "border-slate-400/40 bg-slate-500/20 text-slate-200" };
  }, [confidence]);

  const cards = [
    { label: "🔢 Tokens", value: Number(animatedTokens).toLocaleString() },
    { label: "💰 Cost", value: `$${animatedCost.toFixed(4)}` },
    { label: "⏱ Time", value: `${Math.max(0, Math.round(animatedTime))}s` },
    { label: "🔁 Retries", value: Math.max(0, Math.round(animatedRetries)).toString() },
    { label: "🔄 Fallbacks", value: Math.max(0, Math.round(animatedFallbacks)).toString() },
    { label: "🤔 Reflections", value: Math.max(0, Math.round(animatedReflections)).toString() },
  ];

  return (
    <section className="glass rounded-2xl border border-white/10 p-3 sm:p-4">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-7">
        {cards.map((card) => (
          <div key={card.label} className="rounded-xl border border-white/10 bg-white/5 px-3 py-2">
            <p className="text-[11px] uppercase tracking-wide text-[var(--text-secondary)]">{card.label}</p>
            <p className="mt-1 text-sm font-semibold text-[var(--text-primary)]">{card.value}</p>
          </div>
        ))}

        <div className="rounded-xl border border-white/10 bg-white/5 px-3 py-2">
          <p className="text-[11px] uppercase tracking-wide text-[var(--text-secondary)]">✅ Confidence</p>
          <span className={`mt-1 inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold ${confidenceMeta.classes}`}>
            {confidenceMeta.label}
          </span>
        </div>
      </div>
    </section>
  );
}

function useCountUp(targetValue, { duration = 600, decimals = 0 } = {}) {
  const [displayValue, setDisplayValue] = useState(Number(targetValue) || 0);
  const previousValueRef = useRef(Number(targetValue) || 0);
  const frameRef = useRef(null);

  useEffect(() => {
    const from = Number(previousValueRef.current) || 0;
    const to = Number(targetValue) || 0;

    if (from === to) {
      setDisplayValue(to);
      return () => undefined;
    }

    const startedAt = performance.now();

    const tick = (now) => {
      const progress = Math.min((now - startedAt) / duration, 1);
      const eased = 1 - (1 - progress) ** 3;
      const next = from + (to - from) * eased;
      setDisplayValue(Number(next.toFixed(decimals)));

      if (progress < 1) {
        frameRef.current = requestAnimationFrame(tick);
      } else {
        previousValueRef.current = to;
      }
    };

    frameRef.current = requestAnimationFrame(tick);

    return () => {
      if (frameRef.current) {
        cancelAnimationFrame(frameRef.current);
      }
    };
  }, [decimals, duration, targetValue]);

  return displayValue;
}

function normalizeTime(value) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const parsed = Number.parseFloat(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return 0;
}
