import { AnimatePresence, motion } from "framer-motion";

const VARIANT_STYLES = {
  success: "border-emerald-400/45 bg-emerald-500/20 text-emerald-100",
  info: "border-sky-400/45 bg-sky-500/20 text-sky-100",
  warning: "border-amber-400/45 bg-amber-500/20 text-amber-100",
  error: "border-red-400/45 bg-red-500/20 text-red-100",
};

export default function ToastStack({ toasts = [], onDismiss }) {
  const safeToasts = Array.isArray(toasts) ? toasts : [];

  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-[70] flex w-[min(92vw,420px)] flex-col gap-2">
      <AnimatePresence initial={false}>
        {safeToasts.map((toast) => {
          const style = VARIANT_STYLES[toast.variant] || VARIANT_STYLES.info;
          return (
            <motion.article
              key={toast.id}
              initial={{ opacity: 0, x: 32, y: 10 }}
              animate={{ opacity: 1, x: 0, y: 0 }}
              exit={{ opacity: 0, x: 30, y: 8 }}
              transition={{ duration: 0.2 }}
              className={`pointer-events-auto rounded-xl border px-3 py-2 shadow-lg shadow-black/35 backdrop-blur ${style}`}
            >
              <div className="flex items-start justify-between gap-3">
                <p className="text-sm font-medium leading-snug">{toast.message}</p>
                <button
                  type="button"
                  onClick={() => onDismiss?.(toast.id)}
                  className="mt-0.5 shrink-0 rounded-md border border-white/20 bg-black/20 px-1.5 py-0.5 text-xs text-white/90 transition hover:bg-black/35"
                  aria-label="Dismiss toast"
                >
                  x
                </button>
              </div>
            </motion.article>
          );
        })}
      </AnimatePresence>
    </div>
  );
}