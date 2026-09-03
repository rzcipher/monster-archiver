import React, { useEffect, useState } from "react";
import { AlertCircle, CheckCircle, Info, X } from "lucide-react";

/**
 * Tiny app-wide toast channel.
 *
 * Replaces the browser-native alert() popups the archiver used to throw at
 * upload/compile/transcription failures: a blocking, theme-less OS dialog
 * mid-workflow is jarring and (in Chrome at least) mutes keyboard input app
 * wide until dismissed. These stack bottom-right, use the same design tokens
 * as the rest of the UI, and auto-dismiss — while still being announced to
 * screen readers via the aria-live region.
 *
 * Module-level store on purpose: toast() is called from plain async helper
 * functions deep inside App/LyricsEditor where no hook could reach a
 * provider's context, and it keeps the diff (and runtime cost) at zero for
 * every component that never shows one.
 */

export type ToastKind = "error" | "ok" | "info";

export interface ToastItem {
  id: number;
  kind: ToastKind;
  message: string;
}

let nextId = 1;
let items: ToastItem[] = [];
const listeners = new Set<(items: ToastItem[]) => void>();

function emit() {
  const snapshot = [...items];
  listeners.forEach((l) => l(snapshot));
}

/** Show a toast. ttl<=0 keeps it until dismissed manually. */
export function toast(message: string, kind: ToastKind = "error", ttl = kind === "ok" ? 3500 : 6500) {
  const item: ToastItem = { id: nextId++, kind, message };
  // Cap the stack so a retry loop firing on every keystroke can't pile up.
  items = [...items, item].slice(-4);
  emit();
  if (ttl > 0) {
    setTimeout(() => {
      if (items.some((t) => t.id === item.id)) {
        items = items.filter((t) => t.id !== item.id);
        emit();
      }
    }, ttl);
  }
  return item.id;
}

export function dismissToast(id: number) {
  items = items.filter((t) => t.id !== id);
  emit();
}

const KIND_STYLES: Record<ToastKind, { icon: typeof AlertCircle; accent: string }> = {
  error: { icon: AlertCircle, accent: "border-red-500/40 text-red-200" },
  ok: { icon: CheckCircle, accent: "border-emerald-500/40 text-emerald-200" },
  info: { icon: Info, accent: "border-deezer-500/40 text-deezer-200" },
};

export default function ToastHost() {
  const [list, setList] = useState<ToastItem[]>([]);

  useEffect(() => {
    listeners.add(setList);
    setList([...items]);
    return () => {
      listeners.delete(setList);
    };
  }, []);

  if (list.length === 0) return null;

  return (
    <div
      aria-live="polite"
      aria-label="Notifications"
      className="fixed bottom-20 right-3 sm:right-4 z-[95] flex flex-col gap-2 max-w-[min(92vw,24rem)]"
    >
      {list.map((t) => {
        const style = KIND_STYLES[t.kind];
        const Icon = style.icon;
        return (
          <div
            key={t.id}
            role={t.kind === "error" ? "alert" : "status"}
            className={`pointer-events-auto flex items-start gap-2.5 rounded-lg border bg-void-900/95 backdrop-blur px-3 py-2.5 shadow-xl shadow-black/40 ${style.accent}`}
            style={{ animation: "toast-in 220ms ease-out" }}
          >
            <Icon className="w-4 h-4 mt-0.5 shrink-0" />
            <p className="text-xs sm:text-[13px] leading-relaxed text-slate-200 whitespace-pre-wrap break-words flex-1">
              {t.message}
            </p>
            <button
              onClick={() => dismissToast(t.id)}
              aria-label="Dismiss notification"
              className="shrink-0 text-slate-500 hover:text-white transition-colors"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
