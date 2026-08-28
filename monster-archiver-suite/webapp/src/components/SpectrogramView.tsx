import React from "react";
import { Activity, ShieldCheck, ShieldAlert, Sparkles } from "lucide-react";
import { SpectrogramPoint } from "../types";
import { motion } from "motion/react";

interface SpectrogramViewProps {
  spectrogram: SpectrogramPoint[];
  suspect: boolean;
  isLossless: boolean;
  maxActiveFreq: number;
  energyBelow16k: number;
}

export default function SpectrogramView({
  spectrogram,
  suspect,
  isLossless,
  maxActiveFreq,
  energyBelow16k
}: SpectrogramViewProps) {
  if (!spectrogram || spectrogram.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-64 bg-slate-900/40 rounded-2xl border border-slate-800">
        <Activity className="w-8 h-8 text-slate-500 animate-pulse mb-2" />
        <p className="text-slate-400 text-sm">No spectral data available. Upload an audio file to scan.</p>
      </div>
    );
  }

  // Draw SVG spectrogram
  const width = 600;
  const height = 180;
  const padding = 30;

  const minDb = -80;
  const maxDb = 0;

  // Scale calculations
  const xScale = (idx: number) => padding + (idx / (spectrogram.length - 1)) * (width - 2 * padding);
  const yScale = (db: number) => {
    const clamped = Math.max(minDb, Math.min(maxDb, db));
    return padding + ((clamped - maxDb) / (minDb - maxDb)) * (height - 2 * padding);
  };

  // Build line path
  const pathData = spectrogram
    .map((p, idx) => `${idx === 0 ? "M" : "L"} ${xScale(idx)} ${yScale(p.db)}`)
    .join(" ");

  // Build area path under the curve
  const areaData = `${pathData} L ${xScale(spectrogram.length - 1)} ${height - padding} L ${xScale(0)} ${height - padding} Z`;

  // Cutoff position index (approximate for drawing vertical cutoff indicator line)
  const cutoffIdx = spectrogram.findIndex((p) => p.freq >= maxActiveFreq);
  const cutoffX = cutoffIdx !== -1 ? xScale(cutoffIdx) : padding;

  // 16kHz position index (for reference)
  const ref16kIdx = spectrogram.findIndex((p) => p.freq >= 16000);
  const ref16kX = ref16kIdx !== -1 ? xScale(ref16kIdx) : padding;

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
            <p className="text-slate-500 text-xs">High-speed frequency distribution and cutoff threshold check</p>
          </div>
        </div>

        {/* Verdict Badge */}
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

      {/* SVG Interactive Chart */}
      <div className="w-full overflow-x-auto select-none mb-6">
        <svg 
          viewBox={`0 0 ${width} ${height}`} 
          className="w-full min-w-[500px] h-auto overflow-visible"
        >
          {/* Grid lines */}
          <line x1={padding} y1={yScale(-20)} x2={width - padding} y2={yScale(-20)} stroke="#1e293b" strokeDasharray="3,3" />
          <line x1={padding} y1={yScale(-40)} x2={width - padding} y2={yScale(-40)} stroke="#1e293b" strokeDasharray="3,3" />
          <line x1={padding} y1={yScale(-60)} x2={width - padding} y2={yScale(-60)} stroke="#1e293b" strokeDasharray="3,3" />
          
          <text x={padding - 5} y={yScale(0) + 4} textAnchor="end" className="fill-slate-500 text-[10px] font-mono">0dB</text>
          <text x={padding - 5} y={yScale(-30) + 4} textAnchor="end" className="fill-slate-500 text-[10px] font-mono">-30dB</text>
          <text x={padding - 5} y={yScale(-60) + 4} textAnchor="end" className="fill-slate-500 text-[10px] font-mono">-60dB</text>

          {/* X axis labels */}
          <text x={padding} y={height - padding + 15} textAnchor="middle" className="fill-slate-500 text-[10px] font-mono">0Hz</text>
          <text x={xScale(Math.floor(spectrogram.length * 0.25))} y={height - padding + 15} textAnchor="middle" className="fill-slate-500 text-[10px] font-mono">5.5kHz</text>
          <text x={xScale(Math.floor(spectrogram.length * 0.5))} y={height - padding + 15} textAnchor="middle" className="fill-slate-500 text-[10px] font-mono">11kHz</text>
          <text x={xScale(Math.floor(spectrogram.length * 0.75))} y={height - padding + 15} textAnchor="middle" className="fill-slate-500 text-[10px] font-mono">16.5kHz</text>
          <text x={width - padding} y={height - padding + 15} textAnchor="middle" className="fill-slate-500 text-[10px] font-mono">22kHz</text>

          {/* Area under curve with gradient */}
          <defs>
            <linearGradient id="chartGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={suspect ? "#f43f5e" : "#6366f1"} stopOpacity="0.25" />
              <stop offset="100%" stopColor={suspect ? "#f43f5e" : "#6366f1"} stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={areaData} fill="url(#chartGradient)" />

          {/* Spectrum curve */}
          <path 
            d={pathData} 
            fill="none" 
            stroke={suspect ? "#f43f5e" : "#6366f1"} 
            strokeWidth="2" 
            strokeLinecap="round" 
            strokeLinejoin="round" 
          />

          {/* Cutoff Limit Vertical Indicator Line */}
          {cutoffX > padding && (
            <g>
              <line 
                x1={cutoffX} 
                y1={padding} 
                x2={cutoffX} 
                y2={height - padding} 
                stroke={suspect ? "#f43f5e" : "#10b981"} 
                strokeWidth="1.5" 
                strokeDasharray="4,2" 
              />
              <circle cx={cutoffX} cy={yScale(spectrogram[cutoffIdx]?.db || -80)} r="4" fill={suspect ? "#f43f5e" : "#10b981"} />
              <text 
                x={cutoffX} 
                y={padding - 5} 
                textAnchor="middle" 
                className={`text-[9px] font-mono font-bold ${suspect ? "fill-rose-400" : "fill-emerald-400"}`}
              >
                Cutoff: {(maxActiveFreq / 1000).toFixed(1)}kHz
              </text>
            </g>
          )}

          {/* 16kHz Compression Line (Reference) */}
          <line 
            x1={ref16kX} 
            y1={padding} 
            x2={ref16kX} 
            y2={height - padding} 
            stroke="#f43f5e" 
            strokeWidth="1" 
            strokeDasharray="2,4" 
            opacity="0.4"
          />
          <text 
            x={ref16kX} 
            y={height - padding - 5} 
            textAnchor="end" 
            dx="-4"
            className="fill-rose-500/60 text-[8px] font-mono"
          >
            16kHz Limit
          </text>
        </svg>
      </div>

      {/* Narrative Explanation */}
      <div className="bg-slate-900/50 rounded-xl border border-slate-800 p-4">
        <div className="flex gap-2.5 items-start">
          <Sparkles className="w-5 h-5 text-indigo-400 shrink-0 mt-0.5" />
          <div className="text-sm">
            <span className="text-white font-medium">Spectral Diagnosis: </span>
            <span className="text-slate-300">
              {!isLossless ? (
                "This is a compressed lossy format. Cutoffs at 16kHz–20kHz are fully expected and normal for MP3/M4A compression formats."
              ) : suspect ? (
                `⚠️ Flagged upconvert. Although the container is FLAC, the audio signal experiences a sudden drop and experiences an absolute cutoff at ${(maxActiveFreq / 1000).toFixed(1)} kHz. Real lossless files carry active signals, tape hiss, or noise-shaping dither above 18.5 kHz.`
              ) : (
                `✨ Pristine file. Frequency distribution stretches smoothly and dynamically up to ${(maxActiveFreq / 1000).toFixed(1)} kHz with no lowpass compression cutoff, representing real CD-quality or high-resolution lossless audio.`
              )}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
