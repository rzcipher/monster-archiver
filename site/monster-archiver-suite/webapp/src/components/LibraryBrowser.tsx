import React, { useEffect, useState } from "react";
import { FolderOpen, Music, PlayCircle, Loader2 } from "lucide-react";

export default function LibraryBrowser({ onLoadTrack }: { onLoadTrack: (file: any) => void }) {
  const [tracks, setTracks] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchTracks = async () => {
      try {
        setLoading(true);
        const res = await fetch("/api/library/tracks");
        if (!res.ok) {
          throw new Error("Failed to load library tracks");
        }
        const data = await res.json();
        setTracks(data);
      } catch (e: any) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    };
    fetchTracks();
  }, []);

  if (loading) {
    return (
      <div className="w-full bg-void-900 rounded-xl border border-void-700 p-6 mt-4 flex items-center justify-center text-slate-400">
        <Loader2 className="w-5 h-5 animate-spin mr-2" /> Loading library...
      </div>
    );
  }

  if (error) {
    return (
      <div className="w-full bg-void-900 rounded-xl border border-void-700 p-6 mt-4 text-center text-rose-400">
        {error}
      </div>
    );
  }

  if (tracks.length === 0) {
    return null;
  }

  return (
    <div className="w-full bg-void-900/60 backdrop-blur-md rounded-xl border border-void-700 p-6 mt-4 shadow-xl">
      <h3 className="text-white font-medium text-lg flex items-center gap-2 mb-4">
        <FolderOpen className="w-5 h-5 text-deezer-400" />
        Library Tracks
      </h3>
      <div className="overflow-y-auto max-h-64 custom-scrollbar pr-2 space-y-2">
        {tracks.map((track, i) => (
          <div
            key={i}
            onClick={() => onLoadTrack({ path: track.path, originalName: track.originalName, size: track.size })}
            className="flex items-center justify-between p-3 bg-void-950/50 hover:bg-void-800 rounded-lg border border-void-800 hover:border-deezer-500/50 cursor-pointer transition-all group"
          >
            <div className="flex items-center gap-3 overflow-hidden">
              <div className="w-8 h-8 rounded bg-void-900 flex items-center justify-center shrink-0 group-hover:bg-deezer-500/20 text-slate-500 group-hover:text-deezer-400 transition-colors">
                <Music className="w-4 h-4" />
              </div>
              <div className="truncate">
                <p className="text-sm text-slate-200 font-medium truncate">{track.originalName}</p>
                <p className="text-[10px] text-slate-500 font-mono mt-0.5">{(track.size / (1024 * 1024)).toFixed(1)} MB</p>
              </div>
            </div>
            <PlayCircle className="w-5 h-5 text-slate-600 group-hover:text-deezer-400 opacity-0 group-hover:opacity-100 transition-all shrink-0" />
          </div>
        ))}
      </div>
    </div>
  );
}
