import { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import Mascot from "./Mascot";

interface BootSequenceProps {
  /** Called once the sequence's internal timeline is done, or the user skips it. */
  onFinished: () => void;
}

const BOOT_LINES = [
  "mounting spectral integrity engine ......... OK",
  "linking metadata providers (itunes/mb/deezer) OK",
  "waking local LLM bridge (ollama) ............ OK",
  "calibrating BPM & key detector .............. OK",
  "indexing lyric transcription core ........... OK",
];

const STATUS_MESSAGES = [
  "Warming up the archive daemon",
  "Parsing ID3 & Vorbis frames",
  "Tuning spectral thresholds",
  "Booting Monster Archiver Suite",
];

const LINE_STEP_MS = 190;
const LINE_START_MS = 160;
const MARK_HOLD_MS = 700;
const PROGRESS_DURATION_MS = 1250;

export default function BootSequence({ onFinished }: BootSequenceProps) {
  const prefersReduced =
    typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const [phase, setPhase] = useState<"lines" | "mark" | "progress">("lines");
  const [visibleLines, setVisibleLines] = useState(0);
  const [progress, setProgress] = useState(0);
  const [statusIdx, setStatusIdx] = useState(0);
  const [showSkipHint, setShowSkipHint] = useState(false);
  const finishedRef = useRef(false);

  const finish = () => {
    if (finishedRef.current) return;
    finishedRef.current = true;
    onFinished();
  };

  // main timeline
  useEffect(() => {
    if (prefersReduced) {
      const t = setTimeout(finish, 260);
      return () => clearTimeout(t);
    }

    const timers: ReturnType<typeof setTimeout>[] = [];
    BOOT_LINES.forEach((_, i) => {
      const at = LINE_START_MS + i * LINE_STEP_MS;
      timers.push(setTimeout(() => setVisibleLines(i + 1), at));
    });

    const linesDoneAt = LINE_START_MS + BOOT_LINES.length * LINE_STEP_MS;
    timers.push(setTimeout(() => setPhase("mark"), linesDoneAt + 200));
    timers.push(setTimeout(() => setPhase("progress"), linesDoneAt + 200 + MARK_HOLD_MS));
    timers.push(setTimeout(() => setShowSkipHint(true), 900));

    return () => {
      timers.forEach(clearTimeout);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // progress bar fill + status cycling
  useEffect(() => {
    if (phase !== "progress") return;
    const start = performance.now();
    let raf = 0;

    const step = (now: number) => {
      const t = Math.min(1, (now - start) / PROGRESS_DURATION_MS);
      setProgress(Math.floor(t * 100));
      setStatusIdx(Math.min(STATUS_MESSAGES.length - 1, Math.floor(t * STATUS_MESSAGES.length)));
      if (t < 1) {
        raf = requestAnimationFrame(step);
      } else {
        setTimeout(finish, 240);
      }
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase]);

  // let the impatient skip with a click or keypress
  useEffect(() => {
    const skip = () => finish();
    window.addEventListener("keydown", skip);
    window.addEventListener("click", skip);
    return () => {
      window.removeEventListener("keydown", skip);
      window.removeEventListener("click", skip);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <motion.div
      className="fixed inset-0 z-[100] bg-void-950 flex flex-col items-center justify-center overflow-hidden cursor-pointer"
      initial={{ opacity: 1 }}
      exit={{ clipPath: "circle(0% at 50% 50%)", transition: { duration: prefersReduced ? 0.15 : 0.65, ease: [0.76, 0, 0.24, 1] } }}
      style={{ clipPath: "circle(150% at 50% 50%)" }}
    >
      {/* faint ambient purple glow behind everything */}
      <div className="absolute w-[520px] h-[520px] bg-deezer-600/25 rounded-full blur-[130px]" />
      <div className="absolute w-[420px] h-[420px] bg-flow-500/10 rounded-full blur-[120px] translate-x-40 translate-y-24" />

      {/* CRT-style scanline sweep for texture */}
      {!prefersReduced && (
        <div className="absolute inset-0 pointer-events-none opacity-[0.06] overflow-hidden">
          <div className="absolute inset-x-0 h-24 bg-gradient-to-b from-transparent via-deezer-200 to-transparent animate-scanline" />
        </div>
      )}

      <div className="relative flex flex-col items-center px-6 w-full max-w-sm">
        {/* terminal boot lines */}
        {phase === "lines" && (
          <div className="font-mono text-[11px] sm:text-xs text-deezer-300/80 space-y-1.5 w-full mb-2 min-h-[130px]">
            {BOOT_LINES.slice(0, visibleLines).map((line, i) => (
              <motion.div
                key={i}
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.25 }}
                className="whitespace-pre flex items-center gap-2"
              >
                <span className="text-flow-400">&gt;</span>
                <span className="truncate">{line}</span>
              </motion.div>
            ))}
            <span className="inline-block w-2 h-3.5 bg-deezer-300 caret-blink align-middle" />
          </div>
        )}

        {/* logo assembly + wordmark */}
        {(phase === "mark" || phase === "progress") && (
          <div className="flex flex-col items-center">
            <motion.div
              initial={{ scale: 0.4, opacity: 0, rotate: -25 }}
              animate={{ scale: 1, opacity: 1, rotate: 0 }}
              transition={{ type: "spring", stiffness: 200, damping: 16 }}
              className="relative mb-5"
            >
              <div className="absolute inset-0 -m-6 bg-deezer-500/35 rounded-full blur-2xl animate-breathe" />
              <div className="relative drop-shadow-[0_0_30px_rgba(var(--glow-rgb),0.55)]">
                <Mascot layoutId="mascot" size={104} blink />
              </div>
            </motion.div>

            <motion.h1
              initial={{ opacity: 0, letterSpacing: "0.4em" }}
              animate={{ opacity: 1, letterSpacing: "0.08em" }}
              transition={{ duration: 0.6, delay: 0.1 }}
              className="font-display font-bold text-2xl sm:text-3xl text-gradient-flow uppercase text-center"
            >
              Monster Archiver
            </motion.h1>
            <motion.p
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.35 }}
              className="text-[10px] font-mono uppercase tracking-[0.3em] text-deezer-400/70 mt-2"
            >
              Nexus Web Suite v18.4
            </motion.p>
          </div>
        )}

        {/* progress bar */}
        {phase === "progress" && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            className="w-full mt-8"
          >
            <div className="h-1.5 w-full bg-void-700 rounded-full overflow-hidden border border-deezer-800/60">
              <div
                className="h-full bg-gradient-deezer rounded-full transition-[width] duration-100 ease-linear"
                style={{ width: `${progress}%` }}
              />
            </div>
            <div className="flex items-center justify-between mt-2.5 font-mono text-[10px] text-slate-500 uppercase tracking-wider">
              <span>{STATUS_MESSAGES[statusIdx]}…</span>
              <span className="text-deezer-300">{progress}%</span>
            </div>
          </motion.div>
        )}
      </div>

      {showSkipHint && (
        <motion.p
          initial={{ opacity: 0 }}
          animate={{ opacity: 0.5 }}
          className="absolute bottom-8 text-[10px] font-mono uppercase tracking-widest text-slate-500"
        >
          click or press any key to skip
        </motion.p>
      )}
    </motion.div>
  );
}
