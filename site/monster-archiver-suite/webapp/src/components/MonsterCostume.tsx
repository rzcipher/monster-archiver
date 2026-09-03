import { useEffect, useRef } from "react";
import { motion, useMotionValue, useSpring, useTransform, useReducedMotion } from "motion/react";

/**
 * Second ambient layer, stacked above AmbientBackground's canvas: pairs of
 * glowing eyes peeking from the dark edges of the screen, plus a couple of
 * loosely stitched seam threads. Both key off --glow-rgb directly via style,
 * so they recolor the instant ThemePicker flips data-theme — no MutationObserver
 * needed here the way AmbientBackground's canvas requires one.
 */

interface EyeSpot {
  id: string;
  xPct: number;
  yPct: number;
  gapPx: number;
  eyePx: number;
  breatheDuration: number;
  breatheDelay: number;
  blinkDuration: number;
  blinkDelay: number;
  hideOnMobile?: boolean;
}

const EYE_SPOTS: EyeSpot[] = [
  { id: "tl", xPct: 5, yPct: 16, gapPx: 34, eyePx: 15, breatheDuration: 6.5, breatheDelay: 0, blinkDuration: 5.2, blinkDelay: 2.1 },
  { id: "tr", xPct: 95, yPct: 11, gapPx: 30, eyePx: 13, breatheDuration: 7.4, breatheDelay: 1.6, blinkDuration: 6.6, blinkDelay: 4.8, hideOnMobile: true },
  { id: "lm", xPct: 3.5, yPct: 58, gapPx: 36, eyePx: 16, breatheDuration: 8.1, breatheDelay: 2.8, blinkDuration: 5.8, blinkDelay: 0.6 },
  { id: "rm", xPct: 96.5, yPct: 47, gapPx: 32, eyePx: 14, breatheDuration: 6.9, breatheDelay: 0.9, blinkDuration: 6.1, blinkDelay: 3.4, hideOnMobile: true },
  { id: "bl", xPct: 8, yPct: 91, gapPx: 38, eyePx: 17, breatheDuration: 7.7, breatheDelay: 3.5, blinkDuration: 5.4, blinkDelay: 1.4 },
  { id: "br", xPct: 92, yPct: 85, gapPx: 33, eyePx: 15, breatheDuration: 6.3, breatheDelay: 2.1, blinkDuration: 6.9, blinkDelay: 5.5, hideOnMobile: true },
];

// Cursor distance (px) inside which a pair widens/dilates, as if it noticed you.
const ALERT_RADIUS = 280;
const PUPIL_RANGE = 2.6;

function EyePair({ spot, reduced }: { spot: EyeSpot; reduced: boolean }) {
  const pairRef = useRef<HTMLDivElement | null>(null);

  const pupilX = useMotionValue(0);
  const pupilY = useMotionValue(0);
  const springPupilX = useSpring(pupilX, { stiffness: 110, damping: 14 });
  const springPupilY = useSpring(pupilY, { stiffness: 110, damping: 14 });

  const alert = useMotionValue(0);
  const springAlert = useSpring(alert, { stiffness: 80, damping: 20 });
  const irisScale = useTransform(springAlert, [0, 1], [1, 1.35]);
  const pupilScale = useTransform(springAlert, [0, 1], [1, 1.5]);

  useEffect(() => {
    if (reduced) return;
    const el = pairRef.current;
    if (!el) return;

    // Coalesce moves to one rAF and cache the rect (invalidated on scroll/
    // resize): the old handler ran a synchronous getBoundingClientRect per
    // mousemove event, forcing a layout recalculation up to ~100x/second on
    // high-poll-rate mice — pure jank for a decorative eye-track.
    let cachedRect: DOMRect | null = null;
    let pendingEvent: MouseEvent | null = null;
    let rafId = 0;
    const invalidate = () => { cachedRect = null; };

    const applyMove = () => {
      rafId = 0;
      const e = pendingEvent;
      if (!e) return;
      if (!cachedRect) cachedRect = el.getBoundingClientRect();
      const dx = e.clientX - cachedRect.left;
      const dy = e.clientY - cachedRect.top;
      const dist = Math.sqrt(dx * dx + dy * dy);
      pupilX.set(Math.max(-1, Math.min(1, dx / 360)) * PUPIL_RANGE);
      pupilY.set(Math.max(-1, Math.min(1, dy / 360)) * PUPIL_RANGE);
      alert.set(dist < ALERT_RADIUS ? 1 - dist / ALERT_RADIUS : 0);
    };
    const handleMove = (e: MouseEvent) => {
      pendingEvent = e;
      if (!rafId) rafId = requestAnimationFrame(applyMove);
    };
    const handleLeave = () => { pendingEvent = null; alert.set(0); };

    window.addEventListener("mousemove", handleMove, { passive: true });
    window.addEventListener("mouseleave", handleLeave);
    window.addEventListener("scroll", invalidate, { passive: true, capture: true });
    window.addEventListener("resize", invalidate);
    return () => {
      window.removeEventListener("mousemove", handleMove);
      window.removeEventListener("mouseleave", handleLeave);
      window.removeEventListener("scroll", invalidate, { capture: true });
      window.removeEventListener("resize", invalidate);
      if (rafId) cancelAnimationFrame(rafId);
    };
  }, [reduced, pupilX, pupilY, alert]);

  const offsets = [-spot.gapPx / 2, spot.gapPx / 2];

  return (
    <div
      ref={pairRef}
      className={`absolute ${spot.hideOnMobile ? "hidden sm:block" : ""}`}
      style={{ left: `${spot.xPct}%`, top: `${spot.yPct}%` }}
    >
      {/* the pair fades in/out of visibility together, as if surfacing from
          and receding back into the dark, rather than sitting at a flat glow */}
      <motion.div
        animate={reduced ? undefined : { opacity: [0.25, 0.8, 0.25] }}
        transition={
          reduced
            ? undefined
            : { duration: spot.breatheDuration, delay: spot.breatheDelay, repeat: Infinity, ease: "easeInOut" }
        }
        style={reduced ? { opacity: 0.4 } : undefined}
      >
        {offsets.map((offset, i) => (
          <div
            key={i}
            className="absolute"
            style={{ left: offset - spot.eyePx / 2, top: -spot.eyePx / 2, width: spot.eyePx, height: spot.eyePx }}
          >
            <motion.div
              className="absolute inset-0 rounded-full"
              style={{
                background:
                  "radial-gradient(circle, transparent 0%, transparent 28%, rgba(var(--glow-rgb), 0.75) 55%, transparent 78%)",
                scale: reduced ? 1 : irisScale,
              }}
            />
            <motion.div
              className="absolute rounded-full"
              style={{
                left: "50%",
                top: "50%",
                width: spot.eyePx * 0.32,
                height: spot.eyePx * 0.32,
                marginLeft: -(spot.eyePx * 0.16),
                marginTop: -(spot.eyePx * 0.16),
                background: "var(--color-deezer-600)",
                boxShadow: "0 0 5px rgba(var(--glow-rgb), 0.9)",
                x: reduced ? 0 : springPupilX,
                y: reduced ? 0 : springPupilY,
                scale: reduced ? 1 : pupilScale,
              }}
            />
            {!reduced && (
              <motion.div
                className="absolute inset-0 rounded-full bg-void-950"
                style={{ transformOrigin: "50% 0%" }}
                animate={{ scaleY: [0, 0, 1, 0, 0] }}
                transition={{
                  duration: spot.blinkDuration,
                  delay: spot.blinkDelay,
                  repeat: Infinity,
                  times: [0, 0.94, 0.97, 0.99, 1],
                }}
              />
            )}
          </div>
        ))}
      </motion.div>
    </div>
  );
}

// Three loosely wandering "seam" threads, stretched corner to corner. Fixed
// 1440x900 viewBox with preserveAspectRatio="none" so it always fills the
// viewport — the curves stretch a bit on very wide/narrow windows, which
// reads as fine for a soft background texture, not a precise graphic.
function CostumeSeams({ reduced }: { reduced: boolean }) {
  const paths = [
    { d: "M -40,140 C 220,50 380,230 640,150 S 1180,70 1480,190", duration: 9, delay: 0 },
    { d: "M -40,760 C 260,860 460,680 760,780 S 1220,840 1480,740", duration: 10.5, delay: 1.2 },
    { d: "M 1480,420 C 1220,340 1040,500 760,410 S 260,320 -40,440", duration: 8.4, delay: 2.4 },
  ];

  return (
    <svg
      className="absolute inset-0 w-full h-full"
      viewBox="0 0 1440 900"
      preserveAspectRatio="none"
      aria-hidden
    >
      {paths.map((p, i) => (
        <motion.path
          key={i}
          d={p.d}
          fill="none"
          strokeWidth={1.5}
          strokeDasharray="7 9"
          style={{ stroke: "rgba(var(--glow-rgb), 0.22)" }}
          animate={
            reduced
              ? undefined
              : { opacity: [0.12, 0.34, 0.12], strokeDashoffset: [0, -400] }
          }
          initial={reduced ? { opacity: 0.2 } : undefined}
          transition={
            reduced
              ? undefined
              : {
                  opacity: { duration: p.duration, delay: p.delay, repeat: Infinity, ease: "easeInOut" },
                  strokeDashoffset: { duration: 70, repeat: Infinity, ease: "linear" },
                }
          }
        />
      ))}
    </svg>
  );
}

export default function MonsterCostume() {
  const reduced = !!useReducedMotion();

  return (
    <div className="fixed inset-0 -z-[9] overflow-hidden pointer-events-none" aria-hidden>
      <CostumeSeams reduced={reduced} />
      {EYE_SPOTS.map((spot) => (
        <EyePair key={spot.id} spot={spot} reduced={reduced} />
      ))}
    </div>
  );
}
