import { useEffect, useRef, useState } from "react";
import { Palette, Check } from "lucide-react";
import { motion, AnimatePresence } from "motion/react";
import { THEMES, ThemeId, getStoredTheme, setTheme } from "../theme";

/**
 * Header control for switching the app's color theme. A single icon button
 * opens a small popover of swatches (each theme's deezer-500/flow-500 pair);
 * clicking one calls setTheme(), which flips the data-theme attribute on the
 * html element immediately — every Tailwind color utility in the app
 * repaints on its own since they all resolve to the same CSS custom
 * properties theme.ts is toggling, no prop-drilling required.
 */
export default function ThemePicker() {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState<ThemeId>("nebula");
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setActive(getStoredTheme());
  }, []);

  // Close on outside click / Escape — standard popover hygiene.
  useEffect(() => {
    if (!open) return;
    const handlePointer = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", handlePointer);
    window.addEventListener("keydown", handleKey);
    return () => {
      window.removeEventListener("mousedown", handlePointer);
      window.removeEventListener("keydown", handleKey);
    };
  }, [open]);

  const choose = (id: ThemeId) => {
    setTheme(id);
    setActive(id);
    setOpen(false);
  };

  return (
    <div ref={containerRef} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-label="Choose color theme"
        aria-expanded={open}
        className={`w-9 h-9 flex items-center justify-center rounded-lg border transition-colors cursor-pointer ${
          open
            ? "border-deezer-500/60 text-deezer-300 bg-deezer-950/30"
            : "border-void-700 text-slate-400 hover:text-deezer-300 hover:border-deezer-500/40"
        }`}
      >
        <Palette className="w-4 h-4" />
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -6, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.97 }}
            transition={{ duration: 0.15 }}
            className="absolute right-0 top-11 z-50 w-52 bg-void-900 border border-void-700 rounded-xl shadow-2xl ring-1 ring-white/5 p-2"
          >
            <p className="px-2 pt-1.5 pb-2 text-[10px] font-mono uppercase tracking-widest text-slate-500">
              Color theme
            </p>
            <div className="space-y-0.5">
              {THEMES.map((t) => {
                const isActive = active === t.id;
                return (
                  <button
                    key={t.id}
                    onClick={() => choose(t.id)}
                    className={`w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-sm transition-colors cursor-pointer ${
                      isActive
                        ? "bg-deezer-950/40 text-white"
                        : "text-slate-400 hover:bg-void-800 hover:text-slate-200"
                    }`}
                  >
                    <span
                      className="w-4 h-4 rounded-full shrink-0 ring-1 ring-white/10"
                      style={{
                        backgroundImage: `linear-gradient(135deg, ${t.swatch}, ${t.swatchAlt})`,
                      }}
                    />
                    <span className="flex-1 text-left font-medium">{t.label}</span>
                    {isActive && <Check className="w-3.5 h-3.5 text-deezer-400" />}
                  </button>
                );
              })}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
