import { useEffect, useRef } from "react";
import {
  motion,
  useMotionValue,
  useSpring,
  useTransform,
  useReducedMotion,
  type MotionValue,
} from "motion/react";

/**
 * First ambient layer — the deepest thing in the stack. MonsterCostume's
 * eyes/seams render above this, the grain-overlay texture above that again,
 * then the real UI. Three parts, all `fixed inset-0` so they cover the
 * viewport regardless of scroll position (see index.css's html/body note):
 *
 *  1. A few large, softly blurred corner blobs that breathe (scale + opacity
 *     loop) on their own and drift slightly toward the cursor for a loose
 *     parallax read. Plain DOM elements styled with rgba(var(--glow-rgb),…),
 *     so they recolor the instant ThemePicker flips data-theme — same trick
 *     as everywhere else in the app, no theme-awareness needed here.
 *  2. A canvas of small drifting embers/motes — cheaper to animate in bulk
 *     on canvas than as this many individual DOM nodes. Canvas can't resolve
 *     CSS custom properties on its own, so a MutationObserver on
 *     <html data-theme> re-reads --glow-rgb / --color-flow-400 whenever the
 *     theme changes; the draw loop just picks up the new colors next frame.
 *     Motes within a small radius of the cursor scatter and settle back.
 *  3. A soft spotlight that eases toward the cursor and fades in while it's
 *     moving, out again once it's idle — the most literal "reacts to your
 *     mouse" cue in this layer.
 *
 * All three are frozen or skipped under prefers-reduced-motion, same
 * convention as Mascot/MonsterCostume.
 */

const BLOBS = [
  { xPct: 12, yPct: 18, size: 46, duration: 7.5, delay: 0 },
  { xPct: 88, yPct: 24, size: 40, duration: 8.6, delay: 1.4 },
  { xPct: 24, yPct: 86, size: 44, duration: 9.2, delay: 2.6 },
  { xPct: 80, yPct: 82, size: 38, duration: 7.9, delay: 0.8 },
];

const PARTICLE_RADIUS = 130; // px around the cursor motes scatter away from
const SPOTLIGHT_SIZE = 420; // px, diameter of the cursor-follow glow

interface Particle {
  x: number; // home position, in canvas px (before drift/repel)
  y: number;
  size: number;
  driftPhaseX: number;
  driftPhaseY: number;
  driftSpeedX: number;
  driftSpeedY: number;
  driftRangeX: number;
  driftRangeY: number;
  flickerPhase: number;
  flickerSpeed: number;
  maxAlpha: number;
  useSecondary: boolean; // tint from --color-flow-400 instead of --glow-rgb
  offsetX: number; // current mouse-repel offset, decays toward 0 each frame
  offsetY: number;
}

// Canvas fillStyle accepts both "rgb(r, g, b)" and plain hex directly, so
// no manual hex parsing is needed for either color.
function readThemeColors() {
  const style = getComputedStyle(document.documentElement);
  const glowRgb = style.getPropertyValue("--glow-rgb").trim();
  const flow400 = style.getPropertyValue("--color-flow-400").trim();
  return {
    primary: glowRgb ? `rgb(${glowRgb})` : "rgb(139, 44, 232)",
    secondary: flow400 || "rgb(139, 44, 232)",
  };
}

function makeParticles(width: number, height: number, count: number): Particle[] {
  const particles: Particle[] = [];
  for (let i = 0; i < count; i++) {
    particles.push({
      x: Math.random() * width,
      y: Math.random() * height,
      size: 1 + Math.random() * 2.2,
      driftPhaseX: Math.random() * Math.PI * 2,
      driftPhaseY: Math.random() * Math.PI * 2,
      driftSpeedX: 0.05 + Math.random() * 0.08,
      driftSpeedY: 0.04 + Math.random() * 0.07,
      driftRangeX: 18 + Math.random() * 30,
      driftRangeY: 24 + Math.random() * 36,
      flickerPhase: Math.random() * Math.PI * 2,
      flickerSpeed: 0.4 + Math.random() * 0.5,
      maxAlpha: 0.25 + Math.random() * 0.4,
      useSecondary: Math.random() < 0.35,
      offsetX: 0,
      offsetY: 0,
    });
  }
  return particles;
}

function CanvasMotes({ reduced }: { reduced: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    let width = 0;
    let height = 0;
    let particles: Particle[] = [];
    let colors = readThemeColors();
    let mouseX = -9999;
    let mouseY = -9999;
    let rafId = 0;
    let visible = !document.hidden;
    let resizeTimer: number | undefined;

    const particleCount = () => (window.innerWidth < 640 ? 24 : 52);

    const resize = () => {
      width = window.innerWidth;
      height = window.innerHeight;
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      particles = makeParticles(width, height, particleCount());
    };
    resize();

    // Reduced motion: paint the field once at half brightness and stop —
    // still reads as "there," with nothing actually moving.
    if (reduced) {
      const drawStatic = () => {
        ctx.clearRect(0, 0, width, height);
        for (const p of particles) {
          ctx.globalAlpha = p.maxAlpha * 0.6;
          ctx.fillStyle = p.useSecondary ? colors.secondary : colors.primary;
          ctx.beginPath();
          ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
          ctx.fill();
        }
        ctx.globalAlpha = 1;
      };
      drawStatic();
      const handleResize = () => {
        window.clearTimeout(resizeTimer);
        resizeTimer = window.setTimeout(() => {
          resize();
          drawStatic();
        }, 150);
      };
      window.addEventListener("resize", handleResize);
      return () => {
        window.removeEventListener("resize", handleResize);
        window.clearTimeout(resizeTimer);
      };
    }

    const handleMove = (e: MouseEvent) => {
      mouseX = e.clientX;
      mouseY = e.clientY;
    };
    const handleLeave = () => {
      mouseX = -9999;
      mouseY = -9999;
    };
    const handleVisibility = () => {
      visible = !document.hidden;
    };
    const handleResize = () => {
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(resize, 150);
    };

    // Canvas can't see CSS custom properties change on its own — this is
    // the one place in the ambient layer that needs to watch data-theme
    // directly rather than resolving var(--glow-rgb) for free.
    const observer = new MutationObserver(() => {
      colors = readThemeColors();
    });
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });

    window.addEventListener("mousemove", handleMove, { passive: true });
    window.addEventListener("mouseleave", handleLeave);
    window.addEventListener("resize", handleResize);
    document.addEventListener("visibilitychange", handleVisibility);

    let last = performance.now();

    const tick = (now: number) => {
      rafId = requestAnimationFrame(tick);
      if (!visible) {
        last = now;
        return;
      }
      const dt = Math.min((now - last) / 1000, 0.05);
      last = now;

      ctx.clearRect(0, 0, width, height);

      for (const p of particles) {
        p.driftPhaseX += p.driftSpeedX * dt;
        p.driftPhaseY += p.driftSpeedY * dt;
        p.flickerPhase += p.flickerSpeed * dt;

        const homeX = p.x + Math.sin(p.driftPhaseX) * p.driftRangeX;
        const homeY = p.y + Math.cos(p.driftPhaseY) * p.driftRangeY;

        // Scatter away from the cursor within PARTICLE_RADIUS, then settle
        // back to the drift path once it moves off (simple exponential
        // decay rather than a full spring — cheap at this particle count).
        const dx = homeX - mouseX;
        const dy = homeY - mouseY;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < PARTICLE_RADIUS) {
          const push = (1 - dist / PARTICLE_RADIUS) * 26;
          const angle = Math.atan2(dy, dx);
          p.offsetX += (Math.cos(angle) * push - p.offsetX) * 0.18;
          p.offsetY += (Math.sin(angle) * push - p.offsetY) * 0.18;
        } else {
          p.offsetX *= 0.92;
          p.offsetY *= 0.92;
        }

        const drawX = homeX + p.offsetX;
        const drawY = homeY + p.offsetY;
        // Breathing flicker: eases between 40% and 100% of the particle's
        // own max alpha rather than snapping fully off, so the field never
        // looks like it's blinking out.
        const alpha = p.maxAlpha * (0.4 + 0.6 * ((Math.sin(p.flickerPhase) + 1) / 2));

        ctx.globalAlpha = alpha;
        ctx.fillStyle = p.useSecondary ? colors.secondary : colors.primary;
        ctx.shadowColor = ctx.fillStyle;
        ctx.shadowBlur = p.size * 3.2;
        ctx.beginPath();
        ctx.arc(drawX, drawY, p.size, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.shadowBlur = 0;
      ctx.globalAlpha = 1;
    };

    rafId = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(rafId);
      observer.disconnect();
      window.removeEventListener("mousemove", handleMove);
      window.removeEventListener("mouseleave", handleLeave);
      window.removeEventListener("resize", handleResize);
      document.removeEventListener("visibilitychange", handleVisibility);
      window.clearTimeout(resizeTimer);
    };
  }, [reduced]);

  return <canvas ref={canvasRef} className="absolute inset-0" aria-hidden />;
}

function Blob({
  b,
  depth,
  springX,
  springY,
  reduced,
}: {
  b: (typeof BLOBS)[number];
  depth: number;
  springX: MotionValue<number>;
  springY: MotionValue<number>;
  reduced: boolean;
}) {
  // Farther-index blobs drift a touch more than near ones, for a loose
  // parallax-depth read rather than every blob moving in lockstep.
  const x = useTransform(springX, (v) => v * depth);
  const y = useTransform(springY, (v) => v * depth);

  return (
    <motion.div
      aria-hidden
      className="absolute rounded-full"
      style={{
        left: `${b.xPct}%`,
        top: `${b.yPct}%`,
        width: `${b.size}vmax`,
        height: `${b.size}vmax`,
        marginLeft: `-${b.size / 2}vmax`,
        marginTop: `-${b.size / 2}vmax`,
        background: "radial-gradient(circle, rgba(var(--glow-rgb), 0.16), transparent 70%)",
        filter: "blur(40px)",
        x: reduced ? 0 : x,
        y: reduced ? 0 : y,
      }}
      animate={reduced ? undefined : { scale: [1, 1.1, 1], opacity: [0.5, 1, 0.5] }}
      initial={reduced ? { opacity: 0.65 } : undefined}
      transition={
        reduced
          ? undefined
          : { duration: b.duration, delay: b.delay, repeat: Infinity, ease: "easeInOut" }
      }
    />
  );
}

function CornerBlobs({ reduced }: { reduced: boolean }) {
  const mouseX = useMotionValue(0);
  const mouseY = useMotionValue(0);
  const springX = useSpring(mouseX, { stiffness: 22, damping: 14, mass: 0.9 });
  const springY = useSpring(mouseY, { stiffness: 22, damping: 14, mass: 0.9 });

  useEffect(() => {
    if (reduced) return;
    const handleMove = (e: MouseEvent) => {
      mouseX.set(e.clientX / window.innerWidth - 0.5);
      mouseY.set(e.clientY / window.innerHeight - 0.5);
    };
    window.addEventListener("mousemove", handleMove, { passive: true });
    return () => window.removeEventListener("mousemove", handleMove);
  }, [reduced, mouseX, mouseY]);

  return (
    <>
      {BLOBS.map((b, i) => (
        <Blob key={i} b={b} depth={10 + i * 4} springX={springX} springY={springY} reduced={reduced} />
      ))}
    </>
  );
}

function CursorSpotlight({ reduced }: { reduced: boolean }) {
  const x = useMotionValue(-9999);
  const y = useMotionValue(-9999);
  const opacity = useMotionValue(0);
  const springX = useSpring(x, { stiffness: 55, damping: 20, mass: 0.5 });
  const springY = useSpring(y, { stiffness: 55, damping: 20, mass: 0.5 });
  const springOpacity = useSpring(opacity, { stiffness: 60, damping: 22 });

  useEffect(() => {
    if (reduced) return;
    let idleTimer: number | undefined;
    const handleMove = (e: MouseEvent) => {
      x.set(e.clientX);
      y.set(e.clientY);
      opacity.set(1);
      window.clearTimeout(idleTimer);
      // Fades back out ~1.5s after the cursor stops, rather than staying
      // pinned to the last position indefinitely.
      idleTimer = window.setTimeout(() => opacity.set(0), 1500);
    };
    const handleLeave = () => opacity.set(0);

    window.addEventListener("mousemove", handleMove, { passive: true });
    window.addEventListener("mouseleave", handleLeave);
    return () => {
      window.removeEventListener("mousemove", handleMove);
      window.removeEventListener("mouseleave", handleLeave);
      window.clearTimeout(idleTimer);
    };
  }, [reduced, x, y, opacity]);

  if (reduced) return null;

  return (
    <motion.div
      aria-hidden
      className="absolute rounded-full"
      style={{
        left: 0,
        top: 0,
        width: SPOTLIGHT_SIZE,
        height: SPOTLIGHT_SIZE,
        marginLeft: -SPOTLIGHT_SIZE / 2,
        marginTop: -SPOTLIGHT_SIZE / 2,
        background: "radial-gradient(circle, rgba(var(--glow-rgb), 0.14), transparent 72%)",
        x: springX,
        y: springY,
        opacity: springOpacity,
      }}
    />
  );
}

export default function AmbientBackground() {
  const reduced = !!useReducedMotion();

  return (
    <div className="fixed inset-0 -z-20 overflow-hidden pointer-events-none" aria-hidden>
      <CornerBlobs reduced={reduced} />
      <CanvasMotes reduced={reduced} />
      <CursorSpotlight reduced={reduced} />
    </div>
  );
}
