import React, { useState, useEffect } from "react";
import { X, RotateCcw, RefreshCw, AlertTriangle, CheckCircle2, History } from "lucide-react";
import { motion, AnimatePresence } from "motion/react";

interface ActivityEntry {
  id: number;
  timestamp: string;
  action: string;
  file_path: string | null;
  prior_path: string | null;
  details: string | null;
  reverted: number;
}

interface ActivityLogProps {
  open: boolean;
  onClose: () => void;
}

export default function ActivityLog({ open, onClose }: ActivityLogProps) {
  const [entries, setEntries] = useState<ActivityEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revertingId, setRevertingId] = useState<number | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/activity?limit=20");
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
      setEntries(data.entries || []);
    } catch (e: any) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) load();
  }, [open]);

  const handleRevert = async (id: number) => {
    setRevertingId(id);
    setError(null);
    try {
      const res = await fetch(`/api/activity/${id}/revert`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
      await load();
    } catch (e: any) {
      setError(e.message || String(e));
    } finally {
      setRevertingId(null);
    }
  };

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            className="fixed inset-0 bg-black/60 z-40"
          />
          <motion.div
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "tween", duration: 0.25 }}
            className="fixed top-0 right-0 h-full w-full max-w-md bg-[#0f1117] border-l border-slate-800 shadow-2xl z-50 flex flex-col"
          >
            <div className="flex items-center justify-between px-5 py-4 border-b border-slate-800/80">
              <h3 className="text-white font-semibold text-base flex items-center gap-2">
                <History className="w-4.5 h-4.5 text-indigo-400" />
                Recent Activity
              </h3>
              <button
                onClick={onClose}
                className="p-1.5 text-slate-500 hover:text-slate-300 bg-transparent border-0 cursor-pointer rounded-md hover:bg-slate-800/60"
              >
                <X className="w-4.5 h-4.5" />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto custom-scrollbar p-4 space-y-3">
              {loading && (
                <div className="flex justify-center py-10">
                  <RefreshCw className="w-6 h-6 text-indigo-400 animate-spin" />
                </div>
              )}

              {!loading && error && (
                <p className="text-rose-400 text-xs flex items-center gap-1.5">
                  <AlertTriangle className="w-3.5 h-3.5" /> {error}
                </p>
              )}

              {!loading && !error && entries.length === 0 && (
                <p className="text-slate-600 text-xs text-center py-10">
                  No archive or merge operations recorded yet.
                </p>
              )}

              {!loading &&
                entries.map((entry) => (
                  <div
                    key={entry.id}
                    className="bg-[#0b0e14]/60 rounded-lg p-3.5 border border-slate-800/60"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold">
                          {entry.action} • {entry.timestamp}
                        </p>
                        <p className="text-slate-300 text-xs mt-1 break-words">
                          {entry.details || entry.file_path}
                        </p>
                      </div>
                      {entry.reverted ? (
                        <span className="shrink-0 text-[10px] text-emerald-400 font-semibold flex items-center gap-1 px-2 py-1 bg-emerald-950/30 border border-emerald-800/30 rounded-md">
                          <CheckCircle2 className="w-3 h-3" /> Reverted
                        </span>
                      ) : (
                        <button
                          onClick={() => handleRevert(entry.id)}
                          disabled={revertingId === entry.id}
                          className="shrink-0 text-[10px] font-semibold flex items-center gap-1 px-2 py-1 bg-amber-950/30 hover:bg-amber-900/40 border border-amber-800/30 text-amber-400 rounded-md transition-colors cursor-pointer disabled:opacity-50"
                        >
                          {revertingId === entry.id ? (
                            <RefreshCw className="w-3 h-3 animate-spin" />
                          ) : (
                            <RotateCcw className="w-3 h-3" />
                          )}
                          Revert
                        </button>
                      )}
                    </div>
                  </div>
                ))}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
