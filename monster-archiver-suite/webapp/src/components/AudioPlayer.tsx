import React, { useEffect, useRef, useState } from "react";
import { Play, Pause, Volume2, VolumeX } from "lucide-react";
import Mascot from "./Mascot";

interface AudioPlayerProps {
  /** Streamable URL for the currently selected source (original or finalized). */
  src: string;
  title: string;
  artist?: string;
  coverUrl?: string;
  sourceMode: "original" | "finalized";
  /** Whether a compiled/tagged copy exists yet — hides the toggle until it does. */
  hasFinalized: boolean;
  onSourceModeChange: (mode: "original" | "finalized") => void;
}

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

/**
 * Sticky "now playing" bar for previewing whichever track is currently in
 * the workspace — the freshly uploaded original, or (once compiled) the
 * finalized tagged copy — without leaving the tagging/lyrics workflow.
 * Streams from /api/stream (Range-enabled) rather than /api/download, which
 * forces a browser Save-As instead of inline playback.
 */
export default function AudioPlayer({
  src,
  title,
  artist,
  coverUrl,
  sourceMode,
  hasFinalized,
  onSourceModeChange
}: AudioPlayerProps) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [volume, setVolume] = useState(0.85);
  const [muted, setMuted] = useState(false);
  const [coverFailed, setCoverFailed] = useState(false);

  // New track, or toggled between original/finalized — the <audio> element
  // will pick up the new src and reload on its own; reset the transport
  // readout here so it doesn't keep showing the previous file's progress.
  useEffect(() => {
    setCurrentTime(0);
    setDuration(0);
    setIsPlaying(false);
    setCoverFailed(false);
  }, [src]);

  useEffect(() => {
    if (audioRef.current) {
      audioRef.current.volume = muted ? 0 : volume;
    }
  }, [volume, muted]);

  const togglePlay = () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) {
      // play() returns a rejectable promise (decode error, etc.) — swallow
      // it rather than letting an unhandled rejection surface, and make
      // sure state doesn't claim playback started when it didn't.
      audio.play().catch(() => setIsPlaying(false));
    } else {
      audio.pause();
    }
  };

  const handleSeek = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = Number(e.target.value);
    setCurrentTime(value);
    if (audioRef.current) audioRef.current.currentTime = value;
  };

  const handleVolumeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = Number(e.target.value);
    setVolume(value);
    if (value > 0 && muted) setMuted(false);
  };

  const progressPct = duration > 0 ? (Math.min(currentTime, duration) / duration) * 100 : 0;
  const effectiveVolume = muted ? 0 : volume;

  return (
    <div className="fixed bottom-0 left-0 right-0 z-50 border-t border-void-700/80 bg-void-900/90 backdrop-blur-md">
      <audio
        ref={audioRef}
        src={src}
        preload="metadata"
        onPlay={() => setIsPlaying(true)}
        onPause={() => setIsPlaying(false)}
        onEnded={() => {
          setIsPlaying(false);
          setCurrentTime(0);
        }}
        onLoadedMetadata={(e) => setDuration(e.currentTarget.duration || 0)}
        onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
      />

      <div className="max-w-7xl mx-auto px-6 py-3 flex items-center gap-4">
        {/* Cover + track info */}
        <div className="flex items-center gap-3 min-w-0 w-48 sm:w-56 shrink-0">
          <div className="w-11 h-11 rounded-lg overflow-hidden bg-void-950 border border-void-700 flex items-center justify-center shrink-0">
            {coverUrl && !coverFailed ? (
              <img
                src={coverUrl}
                alt=""
                className="w-full h-full object-cover"
                onError={() => setCoverFailed(true)}
              />
            ) : (
              <Mascot size={30} spin={isPlaying} blink={false} reactive={false} />
            )}
          </div>
          <div className="min-w-0">
            <p className="text-white text-sm font-medium truncate">{title || "Untitled track"}</p>
            <p className="text-slate-500 text-xs truncate">{artist || "Unknown artist"}</p>
          </div>
        </div>

        {/* Transport + seek */}
        <div className="flex-1 flex items-center gap-3 min-w-0">
          <button
            onClick={togglePlay}
            className="w-9 h-9 shrink-0 rounded-full bg-gradient-to-r from-deezer-600 to-flow-500 hover:from-deezer-500 hover:to-flow-400 text-white flex items-center justify-center shadow-lg shadow-deezer-500/20 transition-all cursor-pointer border-0"
            aria-label={isPlaying ? "Pause" : "Play"}
          >
            {isPlaying ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4 ml-0.5" />}
          </button>

          <span className="text-[11px] text-slate-500 font-mono w-9 text-right shrink-0 hidden sm:inline">
            {formatTime(currentTime)}
          </span>

          <input
            type="range"
            className="player-range flex-1"
            min={0}
            max={duration || 0}
            step={0.1}
            value={Math.min(currentTime, duration || 0)}
            onChange={handleSeek}
            style={{
              background: `linear-gradient(to right, var(--color-deezer-500) ${progressPct}%, var(--color-void-700) ${progressPct}%)`
            }}
            aria-label="Seek"
          />

          <span className="text-[11px] text-slate-500 font-mono w-9 shrink-0 hidden sm:inline">
            {formatTime(duration)}
          </span>
        </div>

        {/* Source toggle + volume */}
        <div className="flex items-center gap-3 shrink-0">
          {hasFinalized && (
            <div className="hidden md:flex items-center bg-void-800 rounded-full p-0.5 border border-void-700">
              {(["original", "finalized"] as const).map((mode) => (
                <button
                  key={mode}
                  onClick={() => onSourceModeChange(mode)}
                  className={`px-2.5 py-1 rounded-full text-[10px] font-semibold uppercase tracking-wider transition-colors cursor-pointer border-0 ${
                    sourceMode === mode
                      ? "bg-gradient-deezer text-white"
                      : "text-slate-500 hover:text-slate-300 bg-transparent"
                  }`}
                >
                  {mode === "original" ? "Original" : "Finalized"}
                </button>
              ))}
            </div>
          )}

          <button
            onClick={() => setMuted((m) => !m)}
            className="text-slate-400 hover:text-white transition-colors cursor-pointer bg-transparent border-0 shrink-0"
            aria-label={muted ? "Unmute" : "Mute"}
          >
            {effectiveVolume === 0 ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
          </button>

          <input
            type="range"
            className="player-range w-20 hidden sm:block"
            min={0}
            max={1}
            step={0.01}
            value={effectiveVolume}
            onChange={handleVolumeChange}
            style={{
              background: `linear-gradient(to right, var(--color-deezer-500) ${effectiveVolume * 100}%, var(--color-void-700) ${effectiveVolume * 100}%)`
            }}
            aria-label="Volume"
          />
        </div>
      </div>
    </div>
  );
}
