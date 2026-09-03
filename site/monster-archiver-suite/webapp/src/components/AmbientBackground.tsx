import React, { useEffect, useRef } from "react";
import { motion, useReducedMotion } from "motion/react";

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  radius: number;
  baseAlpha: number;
}

export default function AmbientBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const reduceMotion = useReducedMotion();

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let animationFrameId: number;
    let mouseX = -1000;
    let mouseY = -1000;
    let particles: Particle[] = [];
    // Viewport size cached on resize — previously window.innerWidth/Height
    // was read ~6 times per particle per frame (hundreds of layout
    // property reads per frame for no reason).
    let viewW = window.innerWidth;
    let viewH = window.innerHeight;
    // The glow color only changes when the theme flips; getComputedStyle
    // every single frame (the old code) forces a style recalculation on
    // the whole document ~60x a second.
    let glowRgb = "255, 255, 255";
    const readGlow = () => {
      glowRgb = getComputedStyle(document.documentElement).getPropertyValue("--glow-rgb").trim() || "255, 255, 255";
    };
    readGlow();
    const themeObserver = new MutationObserver(readGlow);
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

    const mouseConnectionRadius = 180;
    const particleConnectionRadius = 120;

    const initParticles = () => {
      particles = [];
      // Calculate particle count based on screen area to maintain density
      const numParticles = Math.floor((viewW * viewH) / 12000);
      for (let i = 0; i < numParticles; i++) {
        particles.push({
          x: Math.random() * viewW,
          y: Math.random() * viewH,
          vx: (Math.random() - 0.5) * 0.6, // Drift speed X
          vy: (Math.random() - 0.5) * 0.6, // Drift speed Y
          radius: Math.random() * 1.5 + 0.5, // Size between 0.5 and 2
          baseAlpha: Math.random() * 0.5 + 0.2 // Opacity
        });
      }
    };

    const resize = () => {
      viewW = window.innerWidth;
      viewH = window.innerHeight;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = viewW * dpr;
      canvas.height = viewH * dpr;
      ctx.scale(dpr, dpr);
      canvas.style.width = `${viewW}px`;
      canvas.style.height = `${viewH}px`;
      initParticles();
    };

    window.addEventListener("resize", resize);
    resize();

    const handleMouseMove = (e: MouseEvent) => {
      mouseX = e.clientX;
      mouseY = e.clientY;
    };
    window.addEventListener("mousemove", handleMouseMove);

    const handleMouseLeave = () => {
      mouseX = -1000;
      mouseY = -1000;
    };
    window.addEventListener("mouseleave", handleMouseLeave);

    const drawFrame = () => {
      ctx.clearRect(0, 0, viewW, viewH);

      // Move and draw particles
      particles.forEach(p => {
        p.x += p.vx;
        p.y += p.vy;

        // Wrap around edges for infinite seamless space
        if (p.x < 0) p.x = viewW;
        if (p.x > viewW) p.x = 0;
        if (p.y < 0) p.y = viewH;
        if (p.y > viewH) p.y = 0;

        // Interaction: Gently nudge particles away from mouse to make it feel tangible
        if (mouseX !== -1000) {
          const dx = mouseX - p.x;
          const dy = mouseY - p.y;
          const distSq = dx * dx + dy * dy; // squared compare — sqrt only mattered for a <100 test
          if (distSq < 10000) {
            p.x -= dx * 0.01;
            p.y -= dy * 0.01;
          }
        }

        // Draw the dot
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${glowRgb}, ${p.baseAlpha})`;
        ctx.fill();
      });

      // Draw the "web" lines. The old code ran a full O(n^2) pair scan with
      // two sqrt calls per pair every frame; a uniform grid with cell size =
      // the connection radius means each particle only checks its own 3x3
      // neighborhood, and squared-distance rejection skips sqrt entirely
      // until a pair actually qualifies. Visual output is unchanged.
      const cell = particleConnectionRadius;
      const cols = Math.max(1, Math.ceil(viewW / cell));
      const rows = Math.max(1, Math.ceil(viewH / cell));
      const grid: number[][] = new Array(cols * rows);
      for (let i = 0; i < particles.length; i++) {
        const p = particles[i];
        const cx = Math.min(cols - 1, Math.max(0, Math.floor(p.x / cell)));
        const cy = Math.min(rows - 1, Math.max(0, Math.floor(p.y / cell)));
        const k = cy * cols + cx;
        (grid[k] || (grid[k] = [])).push(i);
      }

      ctx.lineWidth = 0.6;
      const maxDistSq = cell * cell;
      for (let i = 0; i < particles.length; i++) {
        const p1 = particles[i];

        // 1. Connect particle to Mouse (The interactive web)
        if (mouseX !== -1000) {
          const dxM = mouseX - p1.x;
          const dyM = mouseY - p1.y;
          const distMSq = dxM * dxM + dyM * dyM;
          if (distMSq < mouseConnectionRadius * mouseConnectionRadius) {
            const distM = Math.sqrt(distMSq);
            ctx.beginPath();
            ctx.moveTo(p1.x, p1.y);
            ctx.lineTo(mouseX, mouseY);
            ctx.strokeStyle = `rgba(${glowRgb}, ${(1 - distM / mouseConnectionRadius) * 0.3})`;
            ctx.stroke();
          }
        }

        // 2. Connect particle to nearby particles (grid neighborhood)
        const cx = Math.min(cols - 1, Math.max(0, Math.floor(p1.x / cell)));
        const cy = Math.min(rows - 1, Math.max(0, Math.floor(p1.y / cell)));
        for (let oy = 0; oy <= 1; oy++) {
          for (let ox = -1; ox <= 1; ox++) {
            // Forward half of the 3x3 neighborhood: the left cell of the
            // same row is covered from that neighbor's own (1,0) scan, so
            // skipping only (oy=0, ox=-1) visits every pair exactly once —
            // same-cell pairs stay deduped by the j <= i filter below.
            if (oy === 0 && ox === -1) continue;
            const nx = cx + ox, ny = cy + oy;
            if (nx < 0 || ny < 0 || nx >= cols || ny >= rows) continue;
            const bucket = grid[ny * cols + nx];
            if (!bucket) continue;
            for (const j of bucket) {
              if (j <= i) continue;
              const p2 = particles[j];
              const dx = p1.x - p2.x;
              const dy = p1.y - p2.y;
              const distSq = dx * dx + dy * dy;
              if (distSq < maxDistSq) {
                const dist = Math.sqrt(distSq);
                ctx.beginPath();
                ctx.moveTo(p1.x, p1.y);
                ctx.lineTo(p2.x, p2.y);
                ctx.strokeStyle = `rgba(${glowRgb}, ${(1 - dist / particleConnectionRadius) * 0.15})`;
                ctx.stroke();
              }
            }
          }
        }
      }
    };

    const loop = () => {
      drawFrame();
      animationFrameId = requestAnimationFrame(loop);
    };

    // Particles drift by 0.6 px per frame at 60 fps, so rendering faster
    // than ~30 fps is invisible; halving the frame rate of a purely ambient
    // layer halves its GPU/CPU cost on every laptop that has this open.
    let lastRender = 0;
    const throttledLoop = (t: number) => {
      animationFrameId = requestAnimationFrame(throttledLoop);
      if (t - lastRender < 33) return;
      lastRender = t;
      drawFrame();
    };

    // Never keep animating a decorative layer in a hidden tab.
    const visibility = () => {
      if (document.hidden) {
        cancelAnimationFrame(animationFrameId);
      } else {
        lastRender = 0;
        animationFrameId = requestAnimationFrame(throttledLoop);
      }
    };
    document.addEventListener("visibilitychange", visibility);

    if (reduceMotion) {
      // Reduced motion: paint the web once, statically, and stop.
      drawFrame();
    } else {
      animationFrameId = requestAnimationFrame(throttledLoop);
    }

    return () => {
      window.removeEventListener("resize", resize);
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseleave", handleMouseLeave);
      document.removeEventListener("visibilitychange", visibility);
      themeObserver.disconnect();
      cancelAnimationFrame(animationFrameId);
    };
  }, [reduceMotion]);

  return (
    <div className="fixed inset-0 overflow-hidden pointer-events-none z-[-5]">
      {/* Interactive Free-Floating Particle Web */}
      <canvas 
        ref={canvasRef} 
        className="absolute inset-0" 
      />
      
      {/* Slow-moving, deeply blurred Aurora/Nebula orbs behind the web */}
      <motion.div
        animate={reduceMotion ? undefined : {
          scale: [1, 1.25, 1],
          x: [0, 80, 0],
          y: [0, -60, 0],
        }}
        transition={{ duration: 18, repeat: Infinity, ease: "easeInOut" }}
        className="absolute -top-[10%] -left-[10%] w-[50vw] h-[50vw] rounded-full bg-deezer-600/10 blur-[120px] mix-blend-screen"
      />
      
      <motion.div
        animate={reduceMotion ? undefined : {
          scale: [1, 1.3, 1],
          x: [0, -100, 0],
          y: [0, 80, 0],
        }}
        transition={{ duration: 24, repeat: Infinity, ease: "easeInOut", delay: 2 }}
        className="absolute top-[15%] -right-[10%] w-[45vw] h-[45vw] rounded-full bg-flow-500/10 blur-[140px] mix-blend-screen"
      />
    </div>
  );
}
