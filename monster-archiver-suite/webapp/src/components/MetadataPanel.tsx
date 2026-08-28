import React, { useState, useEffect } from "react";
import { Music, Disc, Calendar, Hash, Tag, User, Users, Barcode, Search, RefreshCw, Layers, Check, XCircle } from "lucide-react";
import { TrackMetadata, LyricsOption } from "../types";
import { motion, AnimatePresence } from "motion/react";

interface MetadataPanelProps {
  metadata: TrackMetadata;
  onChange: (updated: TrackMetadata) => void;
  onApplyPreset: (meta: any) => void;
}

// Apple's mzstatic CDN doesn't reliably serve every track's artwork at every
// requested size — cascade down through progressively smaller, more reliable
// sizes on load failure instead of leaving a permanently broken image.
// 3000x3000 leads since that's Apple's own documented ceiling for artwork
// masters; mirrors the fallback order routes/tags.ts uses for the actual
// embed, so the preview shown here matches what gets embedded.
const ARTWORK_FALLBACK_SIZES = ["3000x3000", "2000x2000", "1200x1200", "600x600", "300x300", "100x100"];

export default function MetadataPanel({
  metadata,
  onChange,
  onApplyPreset
}: MetadataPanelProps) {
  const [searchTitle, setSearchTitle] = useState(metadata.title || "");
  const [searchArtist, setSearchArtist] = useState(metadata.artist || "");
  const [searchResults, setSearchResults] = useState<any[]>([]);
  const [searching, setSearching] = useState(false);
  const [customCoverUrl, setCustomCoverUrl] = useState(metadata.cover || "");
  const [coverFailed, setCoverFailed] = useState(false);

  // Reset the "gave up" state whenever a new cover URL comes in (new search
  // result applied, or the user edits the Artwork URL field directly).
  useEffect(() => setCoverFailed(false), [metadata.cover]);

  const handleCoverError = (e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    const attempt = Number(img.dataset.fallbackAttempt || "0");
    if (attempt >= ARTWORK_FALLBACK_SIZES.length) {
      setCoverFailed(true);
      return;
    }
    img.dataset.fallbackAttempt = String(attempt + 1);
    img.src = (metadata.cover || "").replace(/\d+x\d+bb\.(jpg|png|webp)/i, `${ARTWORK_FALLBACK_SIZES[attempt]}bb.$1`);
  };

  const handleFieldChange = (key: keyof TrackMetadata, val: any) => {
    onChange({
      ...metadata,
      [key]: val
    });
  };

  const handleSearch = async () => {
    if (!searchTitle.trim()) return;
    setSearching(true);
    try {
      const q = `title=${encodeURIComponent(searchTitle)}&artist=${encodeURIComponent(searchArtist)}`;
      const res = await fetch(`/api/search-metadata?${q}`);
      const data = await res.json();
      // A 4xx/5xx from the API returns an { error } object, not an array —
      // feeding that straight into searchResults.map() would crash the panel,
      // so guard the shape here and just show "no results" instead.
      setSearchResults(Array.isArray(data) ? data : []);
    } catch (e) {
      console.error(e);
      setSearchResults([]);
    } finally {
      setSearching(false);
    }
  };

  const handleApplyPreset = (preset: any) => {
    onApplyPreset(preset);
    if (preset.cover) {
      setCustomCoverUrl(preset.cover);
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      
      {/* 1. EDITABLE TAGS FORM */}
      <div className="lg:col-span-2 bg-[#0f1117] rounded-xl border border-slate-800 p-6 shadow-2xl ring-1 ring-white/5">
        <h3 className="text-white font-medium text-lg mb-5 flex items-center gap-2 border-b border-slate-800/60 pb-3">
          <Tag className="w-5 h-5 text-indigo-400" />
          Track Metadata Tags
        </h3>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          
          {/* Title */}
          <div>
            <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5 block">Title</label>
            <div className="relative">
              <span className="absolute inset-y-0 left-0 flex items-center pl-3 text-slate-500">
                <Music className="w-4 h-4" />
              </span>
              <input 
                type="text" 
                value={metadata.title} 
                onChange={(e) => handleFieldChange("title", e.target.value)}
                className="w-full bg-[#0b0e14]/60 border border-slate-800 rounded-lg py-2.5 pl-10 pr-4 text-white text-sm focus:outline-none focus:border-indigo-500 transition-colors"
              />
            </div>
          </div>

          {/* Artist */}
          <div>
            <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5 block">Artist</label>
            <div className="relative">
              <span className="absolute inset-y-0 left-0 flex items-center pl-3 text-slate-500">
                <User className="w-4 h-4" />
              </span>
              <input 
                type="text" 
                value={metadata.artist} 
                onChange={(e) => handleFieldChange("artist", e.target.value)}
                className="w-full bg-[#0b0e14]/60 border border-slate-800 rounded-lg py-2.5 pl-10 pr-4 text-white text-sm focus:outline-none focus:border-indigo-500 transition-colors"
              />
            </div>
          </div>

          {/* Album Artist */}
          <div>
            <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5 block">Album Artist</label>
            <div className="relative">
              <span className="absolute inset-y-0 left-0 flex items-center pl-3 text-slate-500">
                <Users className="w-4 h-4" />
              </span>
              <input 
                type="text" 
                value={metadata.album_artist} 
                onChange={(e) => handleFieldChange("album_artist", e.target.value)}
                placeholder="Defaults to Artist if left blank"
                className="w-full bg-[#0b0e14]/60 border border-slate-800 rounded-lg py-2.5 pl-10 pr-4 text-white text-sm focus:outline-none focus:border-indigo-500 transition-colors"
              />
            </div>
          </div>

          {/* Album */}
          <div>
            <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5 block">Album</label>
            <div className="relative">
              <span className="absolute inset-y-0 left-0 flex items-center pl-3 text-slate-500">
                <Disc className="w-4 h-4" />
              </span>
              <input 
                type="text" 
                value={metadata.album} 
                onChange={(e) => handleFieldChange("album", e.target.value)}
                className="w-full bg-[#0b0e14]/60 border border-slate-800 rounded-lg py-2.5 pl-10 pr-4 text-white text-sm focus:outline-none focus:border-indigo-500 transition-colors"
              />
            </div>
          </div>

          {/* Year */}
          <div>
            <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5 block">Year</label>
            <div className="relative">
              <span className="absolute inset-y-0 left-0 flex items-center pl-3 text-slate-500">
                <Calendar className="w-4 h-4" />
              </span>
              <input 
                type="text" 
                value={metadata.year} 
                onChange={(e) => handleFieldChange("year", e.target.value)}
                className="w-full bg-[#0b0e14]/60 border border-slate-800 rounded-lg py-2.5 pl-10 pr-4 text-white text-sm focus:outline-none focus:border-indigo-500 transition-colors"
              />
            </div>
          </div>

          {/* Track Number & Disc Number */}
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5 block">Track Number</label>
              <div className="relative">
                <span className="absolute inset-y-0 left-0 flex items-center pl-3 text-slate-500">
                  <Hash className="w-4 h-4" />
                </span>
                <input 
                  type="text" 
                  value={metadata.track} 
                  onChange={(e) => handleFieldChange("track", e.target.value)}
                  className="w-full bg-[#0b0e14]/60 border border-slate-800 rounded-lg py-2.5 pl-10 pr-4 text-white text-sm focus:outline-none focus:border-indigo-500 transition-colors"
                />
              </div>
            </div>
            <div>
              <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5 block">Disc Number</label>
              <div className="relative">
                <span className="absolute inset-y-0 left-0 flex items-center pl-3 text-slate-500">
                  <Disc className="w-4 h-4" />
                </span>
                <input 
                  type="text" 
                  value={metadata.disc}
                  placeholder="1"
                  onChange={(e) => handleFieldChange("disc", e.target.value)}
                  className="w-full bg-[#0b0e14]/60 border border-slate-800 rounded-lg py-2.5 pl-10 pr-4 text-white text-sm focus:outline-none focus:border-indigo-500 transition-colors"
                />
              </div>
            </div>
          </div>

          {/* Genre */}
          <div>
            <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5 block">Genre</label>
            <div className="relative">
              <span className="absolute inset-y-0 left-0 flex items-center pl-3 text-slate-500">
                <Layers className="w-4 h-4" />
              </span>
              <input 
                type="text" 
                value={metadata.genre} 
                onChange={(e) => handleFieldChange("genre", e.target.value)}
                className="w-full bg-[#0b0e14]/60 border border-slate-800 rounded-lg py-2.5 pl-10 pr-4 text-white text-sm focus:outline-none focus:border-indigo-500 transition-colors"
              />
            </div>
          </div>

          {/* Composer */}
          <div>
            <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5 block">Composer</label>
            <div className="relative">
              <span className="absolute inset-y-0 left-0 flex items-center pl-3 text-slate-500">
                <User className="w-4 h-4" />
              </span>
              <input 
                type="text" 
                value={metadata.composer} 
                onChange={(e) => handleFieldChange("composer", e.target.value)}
                className="w-full bg-[#0b0e14]/60 border border-slate-800 rounded-lg py-2.5 pl-10 pr-4 text-white text-sm focus:outline-none focus:border-indigo-500 transition-colors"
              />
            </div>
          </div>

          {/* ISRC */}
          <div>
            <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5 block">ISRC</label>
            <div className="relative">
              <span className="absolute inset-y-0 left-0 flex items-center pl-3 text-slate-500">
                <Barcode className="w-4 h-4" />
              </span>
              <input 
                type="text" 
                value={metadata.isrc}
                placeholder="e.g. USRC17607839"
                onChange={(e) => handleFieldChange("isrc", e.target.value.toUpperCase())}
                className="w-full bg-[#0b0e14]/60 border border-slate-800 rounded-lg py-2.5 pl-10 pr-4 text-white text-sm font-mono focus:outline-none focus:border-indigo-500 transition-colors"
              />
            </div>
          </div>

          {/* Explicit Flag */}
          <div>
            <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5 block">Content Advisory</label>
            <div className="flex bg-[#0b0e14]/60 border border-slate-800 rounded-lg p-1">
              <button
                type="button"
                onClick={() => handleFieldChange("explicit", true)}
                className={`flex-1 py-1.5 text-xs font-medium rounded-md transition-colors ${metadata.explicit === true ? 'bg-red-500/20 text-red-400 border border-red-500/50' : 'text-slate-500 hover:text-slate-300'}`}
              >
                Explicit
              </button>
              <button
                type="button"
                onClick={() => handleFieldChange("explicit", false)}
                className={`flex-1 py-1.5 text-xs font-medium rounded-md transition-colors ${metadata.explicit === false ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/50' : 'text-slate-500 hover:text-slate-300'}`}
              >
                Clean
              </button>
              <button
                type="button"
                onClick={() => handleFieldChange("explicit", null)}
                className={`flex-1 py-1.5 text-xs font-medium rounded-md transition-colors ${metadata.explicit == null ? 'bg-slate-700 text-white border border-slate-600' : 'text-slate-500 hover:text-slate-300'}`}
              >
                None
              </button>
            </div>
          </div>

          {/* BPM & Key */}
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5 block">BPM</label>
              <input 
                type="number" 
                value={metadata.bpm || ""} 
                onChange={(e) => handleFieldChange("bpm", parseInt(e.target.value) || 0)}
                className="w-full bg-[#0b0e14]/60 border border-slate-800 rounded-lg py-2.5 px-4 text-white text-sm focus:outline-none focus:border-indigo-500 transition-colors"
              />
            </div>
            <div>
              <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5 block">Key</label>
              <input 
                type="text" 
                value={metadata.key || ""} 
                onChange={(e) => handleFieldChange("key", e.target.value)}
                className="w-full bg-[#0b0e14]/60 border border-slate-800 rounded-lg py-2.5 px-4 text-white text-sm focus:outline-none focus:border-indigo-500 transition-colors"
              />
            </div>
          </div>

        </div>

        {/* Cover Art URL Input */}
        <div className="mt-5">
          <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5 block">Album Artwork URL</label>
          <div className="flex gap-2">
            <input
              type="text"
              value={metadata.cover || ""}
              onChange={(e) => handleFieldChange("cover", e.target.value)}
              placeholder="Embed high-quality online jpg or png artwork URL"
              className="flex-1 bg-[#0b0e14]/60 border border-slate-800 rounded-lg py-2.5 px-4 text-white text-sm focus:outline-none focus:border-indigo-500 transition-colors font-mono text-xs"
            />
            {metadata.cover && (
              <button
                type="button"
                onClick={() => handleFieldChange("cover", "")}
                title="Remove cover art entirely — the compiled file will have no embedded artwork"
                className="px-3 bg-[#0b0e14]/60 border border-slate-800 hover:border-rose-500/50 text-slate-400 hover:text-rose-400 rounded-lg transition-colors cursor-pointer shrink-0 flex items-center gap-1.5 text-xs font-medium"
              >
                <XCircle className="w-3.5 h-3.5" />
                Remove
              </button>
            )}
          </div>
        </div>
      </div>

      {/* 2. ARTWORK PREVIEW & ONLINE SEARCH SIDEBAR */}
      <div className="bg-[#0f1117] rounded-xl border border-slate-800 p-6 shadow-2xl flex flex-col h-full justify-between ring-1 ring-white/5">
        
        {/* Cover Art Display with Stuck Resolution Safeguards */}
        <div className="mb-6">
          <h4 className="text-white font-medium text-sm uppercase tracking-wider mb-3 block">Artwork Preview</h4>
          <div className="w-full aspect-square bg-[#0b0e14]/80 rounded-lg overflow-hidden border border-slate-800 relative group flex items-center justify-center">
            {metadata.cover && !coverFailed ? (
              <img
                key={metadata.cover}
                src={metadata.cover}
                alt="Album Cover"
                referrerPolicy="no-referrer"
                onError={handleCoverError}
                className="w-full h-full object-cover transition-transform group-hover:scale-105 duration-300"
              />
            ) : (
              <div className="flex flex-col items-center justify-center text-slate-600">
                <Disc className="w-16 h-16 animate-spin-slow mb-2 text-indigo-500/40" />
                <p className="text-xs font-medium uppercase tracking-wider text-slate-500">
                  {coverFailed ? "Cover failed to load" : "No Cover Embedded"}
                </p>
              </div>
            )}
          </div>
          {metadata.cover && (
            <div className="mt-2.5 flex items-center justify-between">
              <span className="text-[10px] text-emerald-400 font-medium">Resolving Fallbacks Active</span>
              <button
                onClick={() => handleFieldChange("cover", metadata.cover.replace(/\d+x\d+bb\.(jpg|png)/i, "3000x3000bb.$1"))}
                className="text-[10px] text-indigo-400 hover:underline font-mono bg-transparent border-0 cursor-pointer"
              >
                Forces Max-Res Master
              </button>
            </div>
          )}
        </div>

        {/* Dynamic Global Metadata Lookup Widget */}
        <div className="border-t border-slate-800/60 pt-4 flex-1 flex flex-col">
          <h4 className="text-white font-medium text-sm uppercase tracking-wider mb-3 block flex items-center gap-1.5">
            <Search className="w-4 h-4 text-indigo-400" />
            Global API Finder
          </h4>

          <div className="flex gap-2 mb-3">
            <input 
              type="text" 
              placeholder="Song..."
              value={searchTitle}
              onChange={(e) => setSearchTitle(e.target.value)}
              className="w-1/2 bg-[#0b0e14] border border-slate-800 rounded-lg py-1.5 px-3 text-white text-xs focus:outline-none focus:border-indigo-500"
            />
            <input 
              type="text" 
              placeholder="Artist..."
              value={searchArtist}
              onChange={(e) => setSearchArtist(e.target.value)}
              className="w-1/2 bg-[#0b0e14] border border-slate-800 rounded-lg py-1.5 px-3 text-white text-xs focus:outline-none focus:border-indigo-500"
            />
            <button 
              onClick={handleSearch}
              disabled={searching}
              className="p-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg transition-colors flex items-center justify-center shrink-0 cursor-pointer"
            >
              {searching ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
            </button>
          </div>

          <div className="overflow-y-auto max-h-[160px] space-y-2 pr-1 custom-scrollbar flex-1">
            {searchResults.map((r, idx) => (
              <div 
                key={idx}
                className="bg-[#0b0e14]/60 border border-slate-800 hover:border-indigo-500/50 rounded-lg p-2.5 transition-all text-left flex gap-2.5 items-center group cursor-pointer"
                onClick={() => handleApplyPreset(r)}
              >
                {r.cover ? (
                  <img
                    src={r.cover}
                    alt=""
                    referrerPolicy="no-referrer"
                    onError={(e) => {
                      const img = e.currentTarget;
                      if (!img.dataset.retried) {
                        img.dataset.retried = "1";
                        img.src = r.cover.replace(/\d+x\d+bb\.(jpg|png|webp)/i, "300x300bb.$1");
                      } else {
                        img.style.display = "none";
                      }
                    }}
                    className="w-9 h-9 rounded object-cover border border-slate-800/50"
                  />
                ) : (
                  <div className="w-9 h-9 rounded bg-[#0b0e14] flex items-center justify-center text-slate-500"><Music className="w-4 h-4" /></div>
                )}
                <div className="min-w-0 flex-1">
                  <h5 className="text-white text-xs font-semibold truncate leading-tight">
                    {r.title}
                    {r.explicit === true && <span className="ml-1.5 inline-flex items-center px-1 py-0.5 rounded text-[8px] font-bold bg-red-500/20 text-red-400 border border-red-500/30 uppercase tracking-widest align-middle">E</span>}
                    {r.explicit === false && <span className="ml-1.5 inline-flex items-center px-1 py-0.5 rounded text-[8px] font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 uppercase tracking-widest align-middle">C</span>}
                  </h5>
                  <p className="text-slate-400 text-[10px] truncate">{r.artist} • <span className="text-indigo-400 font-medium font-mono">{r.source}</span></p>
                </div>
              </div>
            ))}
            {searchResults.length === 0 && !searching && (
              <div className="text-center py-6 text-slate-600 text-xs">
                Enter parameters and hit search to find official release tags.
              </div>
            )}
          </div>
        </div>

      </div>
    </div>
  );
}
