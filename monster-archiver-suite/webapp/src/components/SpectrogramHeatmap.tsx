import React, { useEffect, useMemo, useRef } from "react";
import { Activity, ShieldCheck, ShieldAlert, Sparkles, Info } from "lucide-react";
import { motion } from "motion/react";
import { AudioFileInfo, SpectrogramImage } from "../types";

interface SpectrogramHeatmapProps {
  spectrogramFull?: { image: SpectrogramImage; fileInfo: AudioFileInfo } | null;
  suspect: boolean;
  isLossless: boolean;
  maxActiveFreq: number;
  trackTitle?: string;
}

// ---- Colormap (inferno-style: near-black -> purple -> red -> orange -> pale yellow) ----
const COLOR_STOPS: [number, [number, number, number]][] = [
  [0.0, [0, 0, 4]],
  [0.13, [27, 12, 65]],
  [0.25, [74, 12, 107]],
  [0.38, [120, 28, 109]],
  [0.5, [165, 44, 96]],
  [0.63, [207, 68, 70]],
  [0.75, [237, 105, 37]],
  [0.88, [251, 155, 6]],
  [1.0, [252, 255, 164]]
];

function buildColormapLUT(): Uint8Array {
  const lut = new Uint8Array(256 * 3);
  for (let i = 0; i < 256; i++) {
    const t = i / 255;
    let lo = COLOR_STOPS[0];
    let hi = COLOR_STOPS[COLOR_STOPS.length - 1];
    for (let s = 0; s < COLOR_STOPS.length - 1; s++) {
      if (t >= COLOR_STOPS[s][0] && t <= COLOR_STOPS[s + 1][0]) {
        lo = COLOR_STOPS[s];
        hi = COLOR_STOPS[s + 1];
        break;
      }
    }
    const span = hi[0] - lo[0] || 1;
    const localT = (t - lo[0]) / span;
    lut[i * 3] = Math.round(lo[1][0] + (hi[1][0] - lo[1][0]) * localT);
    lut[i * 3 + 1] = Math.round(lo[1][1] + (hi[1][1] - lo[1][1]) * localT);
    lut[i * 3 + 2] = Math.round(lo[1][2] + (hi[1][2] - lo[1][2]) * localT);
  }
  return lut;
}

const COLORMAP_LUT = buildColormapLUT();

function rgbToHex(r: number, g: number, b: number): string {
  return `#${[r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

const LEGEND_STOPS = [0, 0.25, 0.5, 0.75, 1].map((t) => {
  const idx = Math.round(t * 255);
  return rgbToHex(COLORMAP_LUT[idx * 3], COLORMAP_LUT[idx * 3 + 1], COLORMAP_LUT[idx * 3 + 2]);
});

// ---- Axis helpers ----
function niceStep(roughStep: number): number {
  if (roughStep <= 0) return 1;
  const magnitude = Math.pow(10, Math.floor(Math.log10(roughStep)));
  const candidates = [1, 2, 2.5, 5, 10];
  for (const c of candidates) {
    if (roughStep <= c * magnitude) return c * magnitude;
  }
  return 10 * magnitude;
}

function freqTicks(maxFreqHz: number, targetCount = 12): number[] {
  if (!maxFreqHz || maxFreqHz <= 0) return [0];
  const step = niceStep(maxFreqHz / targetCount);
  const ticks: number[] = [];
  for (let f = 0; f <= maxFreqHz + 1e-6; f += step) ticks.push(Math.round(f));
  return ticks;
}

function timeTicks(duration: number, targetCount = 8): number[] {
  if (!duration || duration <= 0) return [0];
  const step = niceStep(duration / targetCount);
  const ticks: number[] = [];
  for (let t = 0; t <= duration + 1e-6; t += step) ticks.push(Math.round(t));
  return ticks;
}

function formatHzLabel(hz: number): string {
  if (hz === 0) return "0Hz";
  if (hz % 1000 === 0) return `${hz / 1000}kHz`;
  return `${(hz / 1000).toFixed(1)}kHz`;
}

function formatTimeLabel(sec: number, totalDuration: number): string {
  if (totalDuration >= 600) {
    const m = Math.floor(sec / 60);
    const s = Math.round(sec % 60).toString().padStart(2, "0");
    return `${m}:${s}`;
  }
  return `${Math.round(sec)}s`;
}

function formatBytes(bytes: number): string {
  const mb = bytes / (1024 * 1024);
  if (mb >= 1) return `${mb.toFixed(2)} MB`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}

function formatChannels(n: number): string {
  if (n === 1) return "1 (Mono)";
  if (n === 2) return "2 (Stereo)";
  return `${n} ch`;
}

// Logical coordinate space the canvas raster and SVG overlay both share —
// the SVG scales freely via viewBox, and the canvas is rasterized at a
// devicePixelRatio-aware multiple of this same space (see draw effect below)
// so the heatmap stays crisp instead of blurring when stretched to fill wide containers.
const W = 900;
const H = 380;
const MARGIN = { top: 40, right: 60, bottom: 32, left: 56 };
const PLOT_W = W - MARGIN.left - MARGIN.right;
const PLOT_H = H - MARGIN.top - MARGIN.bottom;

export default function SpectrogramHeatmap({
  spectrogramFull,
  suspect,
  isLossless,
  maxActiveFreq,
  trackTitle
}: SpectrogramHeatmapProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const image = spectrogramFull?.image;
  const fileInfo = spectrogramFull?.fileInfo;

  const bytes = useMemo(() => {
    if (!image?.data) return null;
    try {
      const binary = atob(image.data);
      const arr = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) arr[i] = binary.charCodeAt(i);
      return arr;
    } catch {
      return null;
    }
  }, [image?.data]);

  useEffect(() => {
    if (!bytes || !image || !canvasRef.current || image.rows <= 0 || image.cols <= 0) return;
    const { rows, cols } = image;

    // Paint the raw grid onto a tiny offscreen canvas the same size as the data...
    const off = document.createElement("canvas");
    off.width = cols;
    off.height = rows;
    const offCtx = off.getContext("2d");
    if (!offCtx) return;
    const imgData = offCtx.createImageData(cols, rows);
    for (let i = 0; i < bytes.length; i++) {
      const v = bytes[i];
      const li = v * 3;
      imgData.data[i * 4] = COLORMAP_LUT[li];
      imgData.data[i * 4 + 1] = COLORMAP_LUT[li + 1];
      imgData.data[i * 4 + 2] = COLORMAP_LUT[li + 2];
      imgData.data[i * 4 + 3] = 255;
    }
    offCtx.putImageData(imgData, 0, 0);

    // ...then upscale it with smoothing onto the visible canvas, rendered at a
    // higher raster resolution than the logical W/H so it stays sharp on retina/4K.
    const SCALE = Math.min(2, window.devicePixelRatio || 1);
    const canvas = canvasRef.current;
    canvas.width = W * SCALE;
    canvas.height = H * SCALE;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(SCALE, 0, 0, SCALE, 0, 0);
    ctx.clearRect(0, 0, W, H);
    ctx.imageSmoothingEnabled = true;
    // @ts-ignore - not in all lib.dom versions but widely supported
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(off, 0, 0, cols, rows, MARGIN.left, MARGIN.top, PLOT_W, PLOT_H);
  }, [bytes, image]);

  if (!image || !fileInfo || !bytes) {
    return (
      <div className="flex flex-col items-center justify-center h-64 bg-slate-900/40 rounded-2xl border border-slate-800">
        <Activity className="w-8 h-8 text-slate-500 animate-pulse mb-2" />
        <p className="text-slate-400 text-sm">No spectral data available. Upload an audio file to scan.</p>
      </div>
    );
  }

  const fTicks = freqTicks(image.maxFreqHz);
  const tTicks = timeTicks(fileInfo.duration);
  const freqToY = (f: number) => MARGIN.top + (1 - f / image.maxFreqHz) * PLOT_H;
  const timeToX = (t: number) => MARGIN.left + (t / (fileInfo.duration || 1)) * PLOT_W;

  const cutoffColor = suspect ? "#f43f5e" : "#10b981";
  const showCutoff = maxActiveFreq > 0 && maxActiveFreq <= image.maxFreqHz;

  return (
    <div className="bg-[#0f1117] rounded-xl border border-slate-800 p-6 shadow-2xl relative overflow-hidden ring-1 ring-white/5">
      <div className="absolute top-0 right-0 w-32 h-32 bg-indigo-500/5 rounded-full blur-2xl pointer-events-none" />

      <div className="flex flex-wrap items-center justify-between gap-4 mb-5 border-b border-slate-800/60 pb-4">
        <div className="flex items-center gap-3">
          <div className="p-2.5 bg-indigo-950/40 rounded-xl border border-indigo-800/20 text-indigo-400">
            <Activity className="w-5 h-5" />
          </div>
          <div>
            <h3 className="text-white font-medium text-lg leading-tight">Spectral Integrity Scanner</h3>
            <p className="text-slate-500 text-xs">Full-resolution spectrogram and cutoff threshold check</p>
          </div>
        </div>

        {!isLossless ? (
          <div className="flex items-center gap-1.5 px-3.5 py-1.5 bg-slate-800/40 border border-slate-700/30 text-slate-300 rounded-full text-xs font-semibold uppercase tracking-wider">
            Lossy Container
          </div>
        ) : suspect ? (
          <motion.div
            initial={{ scale: 0.95, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            className="flex items-center gap-1.5 px-3.5 py-1.5 bg-rose-950/30 border border-rose-500/30 text-rose-400 rounded-full text-xs font-semibold uppercase tracking-wider"
          >
            <ShieldAlert className="w-4 h-4 text-rose-400" />
            Suspect Upconvert
          </motion.div>
        ) : (
          <motion.div
            initial={{ scale: 0.95, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            className="flex items-center gap-1.5 px-3.5 py-1.5 bg-emerald-950/30 border border-emerald-500/30 text-emerald-400 rounded-full text-xs font-semibold uppercase tracking-wider"
          >
            <ShieldCheck className="w-4 h-4 text-emerald-400" />
            Pristine Lossless
          </motion.div>
        )}
      </div>

      {/* Heatmap: canvas paints the pixel data, SVG overlay draws axes/labels/cutoff on top */}
      <div className="relative w-full mb-6 rounded-lg overflow-hidden bg-black/40 border border-slate-800/60" style={{ aspectRatio: `${W} / ${H}` }}>
        <canvas ref={canvasRef} className="absolute inset-0 w-full h-full" />
        <svg viewBox={`0 0 ${W} ${H}`} className="absolute inset-0 w-full h-full select-none" style={{ pointerEvents: "none" }}>
          {/* Frequency gridlines + labels */}
          {fTicks.map((f) => (
            <g key={`f-${f}`}>
              <line
                x1={MARGIN.left}
                y1={freqToY(f)}
                x2={W - MARGIN.right}
                y2={freqToY(f)}
                stroke="#ffffff"
                strokeOpacity={f === 0 ? 0.15 : 0.06}
              />
              <text x={MARGIN.left - 8} y={freqToY(f) + 3} textAnchor="end" className="fill-slate-400 text-[9px] font-mono">
                {formatHzLabel(f)}
              </text>
            </g>
          ))}

          {/* Time ticks + labels */}
          {tTicks.map((t) => (
            <g key={`t-${t}`}>
              <line x1={timeToX(t)} y1={H - MARGIN.bottom} x2={timeToX(t)} y2={H - MARGIN.bottom + 4} stroke="#475569" />
              <text x={timeToX(t)} y={H - MARGIN.bottom + 16} textAnchor="middle" className="fill-slate-400 text-[9px] font-mono">
                {formatTimeLabel(t, fileInfo.duration)}
              </text>
            </g>
          ))}

          {/* Plot border */}
          <rect x={MARGIN.left} y={MARGIN.top} width={PLOT_W} height={PLOT_H} fill="none" stroke="#1e293b" />

          {/* Cutoff indicator */}
          {showCutoff && (
            <g>
              <line
                x1={MARGIN.left}
                y1={freqToY(maxActiveFreq)}
                x2={W - MARGIN.right}
                y2={freqToY(maxActiveFreq)}
                stroke={cutoffColor}
                strokeWidth={1.5}
                strokeDasharray="5,3"
              />
              <text
                x={W - MARGIN.right - 4}
                y={freqToY(maxActiveFreq) - 6}
                textAnchor="end"
                className="text-[10px] font-mono font-bold"
                fill={cutoffColor}
              >
                Cutoff: {(maxActiveFreq / 1000).toFixed(1)}kHz
              </text>
            </g>
          )}

          {/* Title + sample rate readout */}
          <text x={MARGIN.left} y={20} className="fill-slate-200 text-[11px] font-medium">
            {trackTitle || "Untitled Track"}
          </text>
          <text x={W - MARGIN.right} y={20} textAnchor="end" className="fill-slate-500 text-[10px] font-mono">
            Sample Rate: {fileInfo.sampleRate.toLocaleString()} Hz
          </text>

          {/* Color legend */}
          <defs>
            <linearGradient id="heatLegend" x1="0" y1="1" x2="0" y2="0">
              {LEGEND_STOPS.map((c, i) => (
                <stop key={i} offset={`${(i / (LEGEND_STOPS.length - 1)) * 100}%`} stopColor={c} />
              ))}
            </linearGradient>
          </defs>
          <text x={W - MARGIN.right + 24} y={MARGIN.top - 6} textAnchor="middle" className="fill-slate-500 text-[8px] font-mono">
            High
          </text>
          <rect x={W - MARGIN.right + 19} y={MARGIN.top} width={10} height={70} rx={2} fill="url(#heatLegend)" stroke="#334155" strokeWidth={0.5} />
          <text x={W - MARGIN.right + 24} y={MARGIN.top + 82} textAnchor="middle" className="fill-slate-500 text-[8px] font-mono">
            Low
          </text>
        </svg>
      </div>

      {/* Narrative Explanation */}
      <div className="bg-slate-900/50 rounded-xl border border-slate-800 p-4 mb-6">
        <div className="flex gap-2.5 items-start">
          <Sparkles className="w-5 h-5 text-indigo-400 shrink-0 mt-0.5" />
          <div className="text-sm">
            <span className="text-white font-medium">Spectral Diagnosis: </span>
            <span className="text-slate-300">
              {!isLossless
                ? "This is a compressed lossy format. Cutoffs at 16kHz\u201320kHz are fully expected and normal for MP3/M4A compression formats."
                : suspect
                  ? `\u26A0\uFE0F Flagged upconvert. Although the container is FLAC, the audio signal experiences a sudden drop and an absolute cutoff at ${(maxActiveFreq / 1000).toFixed(1)} kHz. Real lossless files carry active signal, tape hiss, or noise-shaping dither above 18.5 kHz.`
                  : `\u2728 Pristine file. Frequency distribution stretches smoothly and dynamically up to ${(maxActiveFreq / 1000).toFixed(1)} kHz with no lowpass compression cutoff, representing real CD-quality or high-resolution lossless audio.`}
            </span>
          </div>
        </div>
      </div>

      {/* Audio File Information */}
      <div className="bg-[#0b0e14]/60 rounded-xl border border-slate-800 p-5">
        <h4 className="text-white font-medium text-sm mb-3.5 flex items-center gap-1.5 uppercase tracking-wider text-slate-400">
          <Info className="w-4 h-4 text-indigo-400" />
          Audio File Information
        </h4>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          {[
            ["Type", fileInfo.type],
            ["Sample Rate", `${fileInfo.sampleRate.toLocaleString()} Hz`],
            ["Bit Depth", fileInfo.bitDepth ? `${fileInfo.bitDepth} bit` : "N/A (lossy)"],
            ["Channels", formatChannels(fileInfo.channels)],
            ["Duration", `${fileInfo.duration.toFixed(2)}s`],
            ["Nyquist", `${fileInfo.nyquist.toFixed(1)} kHz`],
            ["Size", formatBytes(fileInfo.sizeBytes)],
            ["Samples", fileInfo.samples.toLocaleString()],
            ["Analysis Frames", fileInfo.analysisFrames.toLocaleString()],
            ["FFT Size", fileInfo.fftSize.toLocaleString()],
            ["Freq Resolution", `${fileInfo.freqResolution.toFixed(2)} Hz/bin`]
          ].map(([label, value]) => (
            <div key={label} className="bg-[#0f1117] rounded-lg p-3 border border-slate-800/60">
              <span className="text-[10px] text-slate-500 font-semibold block uppercase tracking-wider">{label}</span>
              <span className="text-white font-bold text-sm font-mono">{value}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
