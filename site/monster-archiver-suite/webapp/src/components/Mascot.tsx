import { useEffect, useRef } from "react";
import { motion, useMotionValue, useSpring, useReducedMotion } from "motion/react";

interface MascotProps {
  size: number;
  /** Rotating dashed scan-ring — same slot the old LogoMark used for the
   * AudioPlayer's isPlaying indicator. */
  spin?: boolean;
  /** Periodic eyelid blink. */
  blink?: boolean;
  /** Cursor-follow pupils + subtle head parallax. On by default; auto-disabled
   * under prefers-reduced-motion regardless of this prop. */
  reactive?: boolean;
  /** Pass the same id from two mount points (e.g. BootSequence's splash and
   * the header badge) to morph the mascot between them, same convention the
   * old LogoMark used. */
  layoutId?: string;
  className?: string;
}

// Expects the art at /public/mascot.png (served at "/mascot.png"), exported
// from art/make_mascot.py's *padded* variant — viewBox "-20 -20 440 500",
// i.e. the source SVG's 400x460 character plus 20px of breathing room on
// every side. That padding is why ART_W/ART_H are 440/500, not 400/460.
const MASCOT_SRC = "/mascot.png";
const ART_W = 440;
const ART_H = 500;

// Percentage-space geometry for the two eyes + chest core, hand-measured
// against that same padded viewBox (see art/make_mascot.py for the source
// coordinates this was derived from). The PNG bakes those three spots as
// plain pale "unlit" shapes on purpose — everything colored on top of them
// here is live DOM/CSS, so it repaints instantly on theme change with no
// per-instance theme lookup, the same trick index.css's palette vars use
// everywhere else in the app.
type Spot = { xPct: number; yPct: number; wPct: number; hPct: number };
const EYE_L: Spot = { xPct: 39.09, yPct: 40.4, wPct: 13.18, hPct: 14 };
const EYE_R: Spot = { xPct: 60.91, yPct: 40.4, wPct: 13.18, hPct: 14 };
const CORE: Spot = { xPct: 50, yPct: 64.6, wPct: 13.64, hPct: 12 };
const EYES = [EYE_L, EYE_R];
const LIGHTS = [EYE_L, EYE_R, CORE];

// left/top that center a wPct x hPct box on (xPct, yPct), all in the same
// percentage space — calc() keeps this exact regardless of `size`, and
// keeps the centering math separate from the animated x/y drift applied
// via motion values below (so the two never fight over one transform).
const centerStyle = (s: Spot): React.CSSProperties => ({
  position: "absolute",
  left: `calc(${s.xPct}% - ${s.wPct / 2}%)`,
  top: `calc(${s.yPct}% - ${s.hPct / 2}%)`,
  width: `${s.wPct}%`,
  height: `${s.hPct}%`,
});

export default function Mascot({
  size,
  spin = false,
  blink = false,
  reactive = true,
  layoutId,
  className,
}: MascotProps) {
  const prefersReducedMotion = useReducedMotion();
  const active = reactive && !prefersReducedMotion;
  const containerRef = useRef<HTMLDivElement | null>(null);

  // Pupil drift (percent of eye box) and whole-head tilt (degrees), each
  // eased through a spring so they glide rather than snap to the cursor.
  const pupilX = useMotionValue(0);
  const pupilY = useMotionValue(0);
  const springPupilX = useSpring(pupilX, { stiffness: 120, damping: 14 });
  const springPupilY = useSpring(pupilY, { stiffness: 120, damping: 14 });

  const tiltX = useMotionValue(0);
  const tiltY = useMotionValue(0);
  const springTiltX = useSpring(tiltX, { stiffness: 90, damping: 16 });
  const springTiltY = useSpring(tiltY, { stiffness: 90, damping: 16 });

  useEffect(() => {
    if (!active) return;
    const el = containerRef.current;
    if (!el) return;

    const PUPIL_RANGE = 3.4; // max pupil drift, in % of eye box
    const TILT_RANGE = 5; // max head tilt, degrees
    // Generous radius so the effect stays a subtle "aware of the cursor"
    // read rather than snapping hard when the pointer is right on top of it.
    const RADIUS = 480;

    // rAF-coalesced with a cached rect (see MonsterCostume for the rationale —
    // one forced layout per raw mousemove event is what made the mascot
    // tracking feel expensive on low-end machines).
    let cachedRect: DOMRect | null = null;
    let pendingEvent: MouseEvent | null = null;
    let rafId = 0;
    const invalidate = () => { cachedRect = null; };

    const applyMove = () => {
      rafId = 0;
      const e = pendingEvent;
      if (!e) return;
      if (!cachedRect) cachedRect = el.getBoundingClientRect();
      const cx = cachedRect.left + cachedRect.width / 2;
      const cy = cachedRect.top + cachedRect.height / 2;
      const nx = Math.max(-1, Math.min(1, (e.clientX - cx) / RADIUS));
      const ny = Math.max(-1, Math.min(1, (e.clientY - cy) / RADIUS));

      pupilX.set(nx * PUPIL_RANGE);
      pupilY.set(ny * PUPIL_RANGE);
      tiltY.set(nx * TILT_RANGE);
      tiltX.set(-ny * TILT_RANGE);
    };

    const handleMove = (e: MouseEvent) => {
      pendingEvent = e;
      if (!rafId) rafId = requestAnimationFrame(applyMove);
    };

    const handleLeave = () => {
      pendingEvent = null;
      pupilX.set(0);
      pupilY.set(0);
      tiltX.set(0);
      tiltY.set(0);
    };

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
  }, [active, pupilX, pupilY, tiltX, tiltY]);

  return (
    <motion.div
      layoutId={layoutId}
      ref={containerRef}
      style={{ width: size, aspectRatio: `${ART_W} / ${ART_H}` }}
      className={`relative shrink-0 ${className ?? ""}`}
    >
      {/* Ambient backdrop halo — breathes behind the whole silhouette and
          recolors with the theme via --glow-rgb, same variable AmbientBackground
          and BootSequence's drop-shadow already key off. */}
      {!prefersReducedMotion && (
        <motion.div
          aria-hidden
          className="absolute inset-0 -z-10 rounded-full"
          style={{
            background: "radial-gradient(circle, rgba(var(--glow-rgb), 0.4), transparent 68%)",
            filter: "blur(6px)",
          }}
          animate={{ scale: [0.88, 1.06, 0.88], opacity: [0.5, 0.95, 0.5] }}
          transition={{ duration: 4.4, repeat: Infinity, ease: "easeInOut" }}
        />
      )}

      {/* Scan ring — kept from the old spin state (AudioPlayer's now-playing cue). */}
      {spin && !prefersReducedMotion && (
        <motion.span
          aria-hidden
          className="absolute rounded-full border-2 border-dashed"
          style={{ inset: "-10%", borderColor: "rgba(var(--glow-rgb), 0.55)" }}
          animate={{ rotate: 360 }}
          transition={{ duration: 3.2, repeat: Infinity, ease: "linear" }}
        />
      )}

      {/* Breathing + parallax tilt live on one wrapper so the head reads as a
          single rigid body; pupils get their own independent drift below so
          they dart slightly ahead of/against the head's own motion. */}
      <motion.div
        className="relative w-full h-full"
        style={{
          rotateX: active ? springTiltX : 0,
          rotateY: active ? springTiltY : 0,
          transformPerspective: 400,
        }}
        animate={prefersReducedMotion ? undefined : { scale: [1, 1.045, 1] }}
        transition={
          prefersReducedMotion ? undefined : { duration: 5.5, repeat: Infinity, ease: "easeInOut" }
        }
      >
        <img
          src={MASCOT_SRC}
          alt=""
          draggable={false}
          className="absolute inset-0 w-full h-full object-contain pointer-events-none select-none"
        />

        {/* Lights: a soft colored ring around each unlit eye/core spot, fully
            transparent at its own center so the baked art shows through
            untouched — plain alpha compositing, so it looks right over both
            the pale eyes and the dark body with no blend-mode edge cases. */}
        {LIGHTS.map((spot, i) => (
          <motion.div
            key={`light-${i}`}
            aria-hidden
            style={{
              ...centerStyle(spot),
              transform: "scale(2.1)",
              borderRadius: "9999px",
              background:
                "radial-gradient(circle, transparent 0%, transparent 34%, rgba(var(--glow-rgb), 0.55) 55%, transparent 76%)",
              pointerEvents: "none",
            }}
            animate={prefersReducedMotion ? undefined : { opacity: [0.3, 0.85, 0.3] }}
            transition={
              prefersReducedMotion
                ? undefined
                : { duration: 2.6 + i * 0.35, repeat: Infinity, ease: "easeInOut", delay: i * 0.3 }
            }
          />
        ))}

        {/* Pupils — centered at rest, drift toward the cursor on top of the
            shared head tilt above. Solid fill, so no blend-mode dependence. */}
        {EYES.map((eye, i) => (
          <motion.div
            key={`pupil-${i}`}
            aria-hidden
            style={{
              ...centerStyle({ ...eye, wPct: eye.wPct * 0.34, hPct: eye.hPct * 0.34 }),
              borderRadius: "9999px",
              background: "var(--color-deezer-600)",
              boxShadow: "0 0 6px rgba(var(--glow-rgb), 0.85)",
              x: active ? springPupilX : 0,
              y: active ? springPupilY : 0,
              pointerEvents: "none",
            }}
          />
        ))}

        {/* Blink — a body-toned lid grows down over each eye, then lifts. */}
        {blink && !prefersReducedMotion && (
          <>
            {EYES.map((eye, i) => (
              <motion.div
                key={`lid-${i}`}
                aria-hidden
                className="bg-void-900"
                style={{
                  ...centerStyle(eye),
                  borderRadius: "9999px",
                  transformOrigin: "50% 0%",
                  pointerEvents: "none",
                }}
                animate={{ scaleY: [0, 0, 1, 0, 0] }}
                transition={{ duration: 4.6, times: [0, 0.92, 0.96, 0.99, 1], repeat: Infinity }}
              />
            ))}
          </>
        )}
      </motion.div>
    </motion.div>
  );
}
