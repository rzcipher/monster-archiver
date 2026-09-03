
import React, { useEffect, useRef, useState } from "react";
import { Play, Pause, Volume2, VolumeX } from "lucide-react";
import Mascot from "./Mascot";
import WaveSurfer from 'wavesurfer.js';

interface AudioPlayerProps {
  lyricsText?: string;
  src: string;
  title: string;
  artist?: string;
  coverUrl?: string;
  sourceMode: "original" | "finalized";
  hasFinalized: boolean;
  onSourceModeChange: (mode: "original" | "finalized") => void;
}

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

export default function AudioPlayer({
  src,
  title,
  artist,
  coverUrl,
  sourceMode,
  hasFinalized,
  onSourceModeChange,
  lyricsText
}: AudioPlayerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const wavesurfer = useRef<WaveSurfer | null>(null);

  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [volume, setVolume] = useState(0.85);
  const [muted, setMuted] = useState(false);
  const [coverFailed, setCoverFailed] = useState(false);

  const [lrcLines, setLrcLines] = useState<{time: number, text: string}[]>([]);
  const [showLyrics, setShowLyrics] = useState(false);

  useEffect(() => {
    if (!lyricsText) {
      setLrcLines([]);
      return;
    }
    const lines = lyricsText.split('\n');
    const parsed = [];
    for (const line of lines) {
      const match = line.match(/^\[(\d+):(\d+\.\d+)\](.*)/);
      if (match) {
        const min = parseInt(match[1]);
        const sec = parseFloat(match[2]);
        const text = match[3].trim();
        if (text) parsed.push({ time: min * 60 + sec, text });
      }
    }
    setLrcLines(parsed);
  }, [lyricsText]);

  const activeLyric = lrcLines.reduce((acc, curr) => {
    if (currentTime >= curr.time) return curr;
    return acc;
  }, { time: 0, text: "" });

  const nextLyric = lrcLines.find(l => l.time > currentTime);

  // Latest volume/mute without forcing the wavesurfer create-effect to rerun
  // (a fresh ws must inherit the user's level the moment it exists, or
  // switching Original/Finalized sources silently resets volume to 1.0).
  const volumeRef = useRef({ volume, muted });
  volumeRef.current = { volume, muted };

  // Resolve the theme's accent pair from the live CSS custom properties so
  // the waveform recolors with ThemePicker instead of being frozen to the
  // default nebula purple.
  const accentColors = () => {
    const css = getComputedStyle(document.documentElement);
    const get = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
    return {
      progressColor: get('--color-deezer-500', '#8b2ce8'),
      cursorColor: get('--color-deezer-400', '#a155f0'),
    };
  };

  useEffect(() => {
    if (!containerRef.current) return;
    
    // Destroy previous instance
    if (wavesurfer.current) {
      wavesurfer.current.destroy();
    }

    const ws = WaveSurfer.create({
      container: containerRef.current,
      waveColor: 'rgba(112, 115, 132, 0.4)', // slate-500 equivalent low opacity
      ...accentColors(),
      barWidth: 2,
      barGap: 2,
      barRadius: 2,
      height: 44,
      url: src,
      normalize: true,
    });

    wavesurfer.current = ws;
    ws.setVolume(volumeRef.current.muted ? 0 : volumeRef.current.volume);

    // Recolor live when the theme flips (data-theme changes on <html>), no
    // instance rebuild — setOptions repaints the bars directly.
    const themeObserver = new MutationObserver(() => {
      try { ws.setOptions(accentColors()); } catch { /* destroyed mid-flip */ }
    });
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

    ws.on('play', () => setIsPlaying(true));
    ws.on('pause', () => setIsPlaying(false));
    ws.on('timeupdate', (time) => setCurrentTime(time));
    ws.on('ready', () => setDuration(ws.getDuration()));
    ws.on('finish', () => {
      setIsPlaying(false);
      setCurrentTime(0);
    });

    setCurrentTime(0);
    setDuration(0);
    setIsPlaying(false);
    setCoverFailed(false);

    return () => {
      themeObserver.disconnect();
      ws.destroy();
    };
  }, [src]);

  useEffect(() => {
    if (wavesurfer.current) {
      wavesurfer.current.setVolume(muted ? 0 : volume);
    }
  }, [volume, muted]);

  const togglePlay = () => {
    wavesurfer.current?.playPause();
  };

  const handleVolumeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = Number(e.target.value);
    setVolume(value);
    if (value > 0 && muted) setMuted(false);
  };

  const effectiveVolume = muted ? 0 : volume;

  return (
    <div className="fixed bottom-0 left-0 right-0 z-50 bg-[#0B0A10]/95 backdrop-blur-xl border-t border-void-800/50">
      
      {/* Synced Lyrics Overlay */}
      {showLyrics && lrcLines.length > 0 && (
        <div className="absolute bottom-full left-0 right-0 p-4 pointer-events-none overflow-hidden flex flex-col items-center justify-end h-64 bg-gradient-to-t from-[#0B0A10]/95 via-[#0B0A10]/50 to-transparent">
          <div className="max-w-3xl w-full text-center flex flex-col items-center justify-end pb-4 transition-all duration-500">
             {activeLyric.text ? (
               <div className="text-2xl md:text-3xl font-black text-white text-shadow-xl mb-3 drop-shadow-[0_0_15px_rgba(var(--glow-rgb),0.6)] animate-in fade-in slide-in-from-bottom-2 duration-300">
                 {activeLyric.text}
               </div>
             ) : (
               <div className="text-xl md:text-2xl font-bold text-slate-500/50 mb-3 italic">
                 ...
               </div>
             )}
             {nextLyric && nextLyric.text && (
               <div className="text-sm md:text-base font-medium text-slate-400/60 truncate max-w-xl transition-all duration-300">
                 {nextLyric.text}
               </div>
             )}
          </div>
        </div>
      )}

      <div className="w-full px-6 py-3 flex items-center justify-between gap-6">
        {/* Cover + track info */}
        <div className="flex items-center gap-4 min-w-0 w-64 shrink-0">
          <div className="w-12 h-12 rounded-md overflow-hidden bg-void-900 border border-void-800 flex items-center justify-center shrink-0">
            {coverUrl && !coverFailed ? (
              <img
                src={coverUrl}
                alt=""
                referrerPolicy="no-referrer"
                className="w-full h-full object-cover"
                onError={() => setCoverFailed(true)}
              />
            ) : (
              <Mascot size={30} spin={isPlaying} blink={false} reactive={false} />
            )}
          </div>
          <div className="min-w-0 flex flex-col justify-center">
            <p className="text-white text-[13px] font-bold truncate tracking-wide">{title || "Untitled track"}</p>
            <p className="text-slate-400 text-[11px] truncate mt-0.5">{artist || "Unknown artist"}</p>
          </div>
        </div>

        {/* Transport + Waveform */}
        <div className="flex-1 max-w-4xl flex items-center gap-5">
          <button
            onClick={togglePlay}
            className="w-10 h-10 shrink-0 rounded-full bg-deezer-500 hover:bg-deezer-400 text-white flex items-center justify-center transition-transform hover:scale-105 active:scale-95 cursor-pointer border-0 shadow-lg shadow-deezer-500/20"
            aria-label={isPlaying ? "Pause" : "Play"}
          >
            {isPlaying ? <Pause className="w-5 h-5 fill-current" /> : <Play className="w-5 h-5 ml-0.5 fill-current" />}
          </button>
          
          <div className="flex-1 flex items-center gap-3">
            <span className="text-[11px] text-slate-500 font-mono w-9 text-right shrink-0">
              {formatTime(currentTime)}
            </span>
            
            {/* WAVESURFER CONTAINER */}
            <div ref={containerRef} className="flex-1 h-[44px] cursor-pointer" />
            
            <span className="text-[11px] text-slate-500 font-mono w-9 shrink-0">
              {formatTime(duration)}
            </span>
          </div>
        </div>

        {/* Source toggle + volume */}
        <div className="flex items-center gap-4 shrink-0 w-64 justify-end">
          
          {lrcLines.length > 0 && (
            <button
              onClick={() => setShowLyrics(!showLyrics)}
              className={`px-3 py-1.5 rounded-lg text-[10px] font-bold uppercase tracking-wider transition-colors cursor-pointer border shrink-0 ${showLyrics ? 'bg-deezer-500/20 text-deezer-400 border-deezer-500/50' : 'bg-void-900 text-slate-400 border-void-800 hover:text-white'}`}
            >
              Lyrics
            </button>
          )}

          {hasFinalized && (
            <div className="hidden lg:flex items-center bg-void-900 rounded-full p-0.5 border border-void-800">
              {(["original", "finalized"] as const).map((mode) => (
                <button
                  key={mode}
                  onClick={() => onSourceModeChange(mode)}
                  className={`px-3 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider transition-colors cursor-pointer border-0 ${
                    sourceMode === mode
                      ? "bg-deezer-500 text-white"
                      : "text-slate-500 hover:text-slate-300 bg-transparent"
                  }`}
                >
                  {mode === "original" ? "Original" : "Finalized"}
                </button>
              ))}
            </div>
          )}
          
          <div className="flex items-center gap-2">
            <button
              onClick={() => setMuted((m) => !m)}
              className="text-slate-400 hover:text-white transition-colors cursor-pointer bg-transparent border-0 shrink-0"
              aria-label={muted ? "Unmute" : "Mute"}
            >
              {effectiveVolume === 0 ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
            </button>
            <input
              type="range"
              className="player-range w-24 h-1.5 hidden sm:block"
              min={0}
              max={1}
              step={0.01}
              value={effectiveVolume}
              onChange={handleVolumeChange}
              style={{
                background: `linear-gradient(to right, var(--color-deezer-500) ${effectiveVolume * 100}%, var(--color-void-800) ${effectiveVolume * 100}%)`
              }}
              aria-label="Volume"
            />
          </div>
        </div>
      </div>
    </div>
  );
}
