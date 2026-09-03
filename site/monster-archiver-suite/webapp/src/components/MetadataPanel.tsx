import React, { useState, useEffect } from "react";
import { Search, Music, Disc, RefreshCw, XCircle, Tag } from "lucide-react";
import { TrackMetadata, SearchPreset } from "../types";
import { toast } from "../Toasts";
import { motion, AnimatePresence } from "motion/react";

interface MetadataPanelProps {
  metadata: TrackMetadata;
  onChange: (metadata: TrackMetadata) => void;
}

const MZSTATIC_SIZE_RE = /\d+x\d+bb\.(jpg|jpeg|png|webp)/i;
// Apple's mzstatic CDN serves every artwork at a fixed set of square sizes;
// when one 404s (some releases only carry the smaller renditions), walking
// the ladder down is what turns "Cover failed to load" into a picture.
const COVER_FALLBACK_SIZES = ["3000x3000bb", "1000x1000bb", "600x600bb", "400x400bb"];

interface InputFieldProps {
  label: string;
  field: string;
  value: any;
  icon?: React.ReactNode;
  type?: string;
  placeholder?: string;
  span?: number;
  onFieldChange: (field: keyof TrackMetadata, value: any) => void;
}

// Module-level on purpose: an inline `const InputField = () => ...` inside
// MetadataPanel gets a fresh function identity on every parent render, so
// React unmounts + remounts the subtree and the <input> loses focus after
// each keystroke (every handleFieldChange re-renders this panel).
function InputField({ label, field, value, icon, type = "text", placeholder = "", span = 1, onFieldChange }: InputFieldProps) {
  return (
    <motion.div
      className={`${span === 2 ? "col-span-2" : "col-span-1"} group relative`}
      whileHover={{ scale: 1.01 }}
      whileTap={{ scale: 0.99 }}
    >
      <label className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5 block group-focus-within:text-deezer-400 transition-colors">
        {label}
      </label>
      <div className="relative">
        {icon && (
          <div className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 group-focus-within:text-deezer-400 transition-colors">
            {icon}
          </div>
        )}
        <input
          type={type}
          value={value || ""}
          placeholder={placeholder}
          onChange={(e) => onFieldChange(field as keyof TrackMetadata, type === "number" ? (parseInt(e.target.value) || 0) : e.target.value)}
          className={`w-full bg-void-950/80 backdrop-blur-sm border border-void-700/60 rounded-xl py-2.5 ${icon ? "pl-10" : "px-4"} pr-4 text-white text-sm focus:outline-none focus:border-deezer-500/80 focus:ring-1 focus:ring-deezer-500/50 transition-all hover:bg-void-900 shadow-inner hover:shadow-deezer-500/10`}
        />
      </div>
    </motion.div>
  );
}

export default function MetadataPanel({ metadata, onChange }: MetadataPanelProps) {
  const [searchTitle, setSearchTitle] = useState("");
  const [searchArtist, setSearchArtist] = useState("");
  const [searchResults, setSearchResults] = useState<SearchPreset[]>([]);
  const [searching, setSearching] = useState(false);
  const [coverFailed, setCoverFailed] = useState(false);

  useEffect(() => {
    if (!searchTitle && !searchArtist) {
      if (metadata.title) setSearchTitle(metadata.title);
      if (metadata.artist) setSearchArtist(metadata.artist);
    }
  }, [metadata.title, metadata.artist]);

  useEffect(() => {
    setCoverFailed(false);
  }, [metadata.cover]);

  const handleFieldChange = (field: keyof TrackMetadata, value: any) => {
    onChange({ ...metadata, [field]: value });
  };

  const handleSearch = async () => {
    setSearching(true);
    setSearchResults([]);
    try {
      const q = `title=${encodeURIComponent(searchTitle)}&artist=${encodeURIComponent(searchArtist)}`;
      const fallbackRes = await fetch(`/api/search-metadata?${q}`);
      const fallbackData = await fallbackRes.json();
      const found = Array.isArray(fallbackData) ? fallbackData : (fallbackData.results || []);
      setSearchResults(found);
      if (!found.length) toast("No metadata found for that title/artist.", "info");
    } catch (e: any) {
      console.error("API search failed", e);
      toast(`Metadata search failed: ${e.message || e}`);
    } finally {
      setSearching(false);
    }
  };

  const handleApplyPreset = (preset: SearchPreset) => {
    onChange({
      ...metadata,
      ...preset,
      title: preset.title || metadata.title,
      artist: preset.artist || metadata.artist,
    });
  };

  const handleCoverError = (e: React.SyntheticEvent<HTMLImageElement, Event>) => {
    const img = e.currentTarget;
    const cover = metadata.cover;
    if (cover && MZSTATIC_SIZE_RE.test(cover)) {
      const step = parseInt(img.dataset.step || "0", 10);
      for (let i = step; i < COVER_FALLBACK_SIZES.length; i++) {
        const next = cover.replace(MZSTATIC_SIZE_RE, `${COVER_FALLBACK_SIZES[i]}.$1`);
        if (next !== img.src) {
          img.dataset.step = String(i + 1);
          img.src = next;
          return;
        }
      }
    }
    setCoverFailed(true);
  };

  return (
    <div className="grid grid-cols-3 gap-6 h-[calc(100vh-140px)]">
      {/* 1. MASTER METADATA INPUTS */}
      <div className="col-span-2 bg-void-900/60 backdrop-blur-md rounded-2xl border border-void-700/50 p-6 overflow-y-auto custom-scrollbar shadow-2xl relative">
        <div className="absolute inset-0 bg-gradient-to-br from-deezer-500/5 to-flow-500/5 rounded-2xl pointer-events-none" />
        
        <h4 className="text-white font-semibold text-lg flex items-center gap-2 mb-6 relative">
          <Tag className="w-5 h-5 text-deezer-400" />
          Track Metadata Tags
        </h4>

        <div className="grid grid-cols-2 gap-x-5 gap-y-6 relative">
          <InputField onFieldChange={handleFieldChange} label="Title" field="title" value={metadata.title} icon={<Music className="w-4 h-4" />} />
          <InputField onFieldChange={handleFieldChange} label="Artist" field="artist" value={metadata.artist} icon={<Disc className="w-4 h-4" />} />
          
          <InputField onFieldChange={handleFieldChange} label="Album Artist" field="album_artist" value={metadata.album_artist} icon={<Disc className="w-4 h-4" />} />
          <InputField onFieldChange={handleFieldChange} label="Album" field="album" value={metadata.album} icon={<Disc className="w-4 h-4" />} />
          
          <InputField onFieldChange={handleFieldChange} label="Year" field="year" value={metadata.year} />
          
          <div className="grid grid-cols-2 gap-4">
            <InputField onFieldChange={handleFieldChange} label="Track Number" field="track" value={metadata.track} type="number" />
            <InputField onFieldChange={handleFieldChange} label="Disc Number" field="disc" value={metadata.disc} type="number" />
          </div>

          <InputField onFieldChange={handleFieldChange} label="Genre" field="genre" value={metadata.genre} />
          <InputField onFieldChange={handleFieldChange} label="Composer" field="composer" value={metadata.composer} />

          <InputField onFieldChange={handleFieldChange} label="ISRC" field="isrc" value={metadata.isrc} placeholder="e.g. USRC17607839" />
          
          <div className="grid grid-cols-2 gap-4">
            <InputField onFieldChange={handleFieldChange} label="BPM" field="bpm" value={metadata.bpm} type="number" />
            <InputField onFieldChange={handleFieldChange} label="Key" field="key" value={metadata.key} />
          </div>
        </div>

        
        {/* Content Advisory */}
        <div className="mt-8 relative group">
          <label className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-2 block group-focus-within:text-deezer-400 transition-colors">
            Content Advisory
          </label>
          <div className="flex p-1 bg-void-950/80 backdrop-blur-sm border border-void-700/60 rounded-xl max-w-sm">
            <button
              onClick={() => handleFieldChange("explicit", true)}
              className={`flex-1 py-1.5 text-xs font-semibold rounded-lg transition-colors ${metadata.explicit === true ? 'bg-void-800 text-white shadow-sm' : 'text-slate-400 hover:text-slate-300 hover:bg-void-900'}`}
            >
              Explicit
            </button>
            <button
              onClick={() => handleFieldChange("explicit", false)}
              className={`flex-1 py-1.5 text-xs font-semibold rounded-lg transition-colors ${metadata.explicit === false ? 'bg-void-800 text-white shadow-sm' : 'text-slate-400 hover:text-slate-300 hover:bg-void-900'}`}
            >
              Clean
            </button>
            <button
              onClick={() => handleFieldChange("explicit", null)}
              className={`flex-1 py-1.5 text-xs font-semibold rounded-lg transition-colors ${metadata.explicit === null || metadata.explicit === undefined ? 'bg-void-800 text-white shadow-sm border border-void-600/50' : 'text-slate-400 hover:text-slate-300 hover:bg-void-900'}`}
            >
              None
            </button>
          </div>
        </div>
<div className="mt-8 relative group">
          <label className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5 block group-focus-within:text-deezer-400 transition-colors">
            Album Artwork URL
          </label>
          <div className="flex gap-2">
            <input
              type="text"
              value={metadata.cover || ""}
              onChange={(e) => handleFieldChange("cover", e.target.value)}
              placeholder="Embed high-quality online jpg or png artwork URL"
              className="flex-1 bg-void-950/80 backdrop-blur-sm border border-void-700/60 rounded-xl py-2.5 px-4 text-white text-sm focus:outline-none focus:border-deezer-500/80 focus:ring-1 focus:ring-deezer-500/50 transition-all font-mono hover:bg-void-900 shadow-inner"
            />
            <AnimatePresence>
              {metadata.cover && (
                <motion.button
                  initial={{ opacity: 0, scale: 0.9, width: 0 }}
                  animate={{ opacity: 1, scale: 1, width: "auto" }}
                  exit={{ opacity: 0, scale: 0.9, width: 0 }}
                  type="button"
                  onClick={() => handleFieldChange("cover", "")}
                  title="Remove cover art entirely"
                  className="px-4 bg-void-950/80 backdrop-blur-sm border border-void-700/60 hover:border-rose-500/50 text-slate-400 hover:text-rose-400 hover:bg-rose-500/10 rounded-xl transition-all cursor-pointer shrink-0 flex items-center gap-1.5 text-xs font-medium overflow-hidden"
                >
                  <XCircle className="w-4 h-4 shrink-0" />
                  <span className="whitespace-nowrap">Remove</span>
                </motion.button>
              )}
            </AnimatePresence>
          </div>
        </div>
      </div>

      {/* 2. ARTWORK PREVIEW & ONLINE SEARCH SIDEBAR */}
      <div className="bg-void-900/60 backdrop-blur-md rounded-2xl border border-void-700/50 p-6 flex flex-col h-full justify-between shadow-2xl relative">
        <div className="absolute inset-0 bg-gradient-to-bl from-flow-500/5 to-transparent rounded-2xl pointer-events-none" />

        {/* Cover Art Display */}
        <div className="mb-6 relative">
          <h4 className="text-white font-semibold text-sm uppercase tracking-wider mb-4 block">Artwork Preview</h4>
          <motion.div 
            whileHover={{ scale: 1.02 }}
            className={`w-full aspect-square bg-void-950/80 rounded-2xl overflow-hidden border ${metadata.cover && !coverFailed ? 'border-deezer-500/30 shadow-[0_0_30px_rgba(var(--glow-rgb),0.1)]' : 'border-void-700/60'} relative group flex items-center justify-center transition-all duration-500`}
          >
            <AnimatePresence mode="wait">
              {metadata.cover && !coverFailed ? (
                <motion.img
                  key={metadata.cover}
                  initial={{ opacity: 0, scale: 0.9 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 1.1 }}
                  transition={{ duration: 0.4 }}
                  src={metadata.cover}
                  alt="Album Cover"
                  referrerPolicy="no-referrer"
                  onError={handleCoverError}
                  className="w-full h-full object-cover transition-transform group-hover:scale-105 duration-700"
                />
              ) : (
                <motion.div 
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  className="flex flex-col items-center justify-center text-slate-600"
                >
                  <Disc className="w-12 h-12 mb-3 text-void-700 animate-[spin_10s_linear_infinite]" />
                  <p className="text-[10px] font-bold uppercase tracking-widest text-slate-500">
                    {coverFailed ? "Cover failed to load" : "No Cover"}
                  </p>
                </motion.div>
              )}
            </AnimatePresence>
          </motion.div>
          {metadata.cover && (
            <div className="mt-3 flex items-center justify-between px-1">
              <span className="text-[10px] text-emerald-400 font-bold tracking-wider">RESOLVING FALLBACKS</span>
              <button
                onClick={() => handleFieldChange("cover", metadata.cover.replace(MZSTATIC_SIZE_RE, "3000x3000bb.$1"))}
                className="text-[10px] text-deezer-400 hover:text-deezer-300 transition-colors font-mono uppercase font-bold bg-transparent border-0 cursor-pointer"
              >
                Force Max-Res
              </button>
            </div>
          )}
        </div>

        {/* Dynamic Global Metadata Lookup Widget */}
        <div className="border-t border-void-700/60 pt-5 flex-1 flex flex-col relative">
          <h4 className="text-white font-semibold text-sm uppercase tracking-wider mb-4 flex items-center gap-2">
            <Search className="w-4 h-4 text-flow-400" />
            Global API Finder
          </h4>
          <div className="flex gap-2 mb-4">
            <input 
              type="text" 
              placeholder="Song"
              value={searchTitle}
              onChange={(e) => setSearchTitle(e.target.value)}
              className="w-1/2 bg-void-950/80 backdrop-blur-sm border border-void-700/60 rounded-xl py-2 px-3 text-white text-xs focus:outline-none focus:border-flow-500/80 transition-all hover:bg-void-900"
            />
            <input 
              type="text" 
              placeholder="Artist"
              value={searchArtist}
              onChange={(e) => setSearchArtist(e.target.value)}
              className="w-1/2 bg-void-950/80 backdrop-blur-sm border border-void-700/60 rounded-xl py-2 px-3 text-white text-xs focus:outline-none focus:border-flow-500/80 transition-all hover:bg-void-900"
            />
            <motion.button 
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
              onClick={handleSearch}
              disabled={searching}
              className="w-10 h-10 bg-gradient-to-r from-flow-600 to-deezer-500 hover:from-flow-500 hover:to-deezer-400 text-white rounded-xl transition-all shadow-lg flex items-center justify-center shrink-0 cursor-pointer disabled:opacity-50"
            >
              {searching ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
            </motion.button>
          </div>
          
          <div className="overflow-y-auto max-h-[160px] space-y-2 pr-1 custom-scrollbar flex-1 relative rounded-xl">
            <AnimatePresence>
              {searchResults.map((r, idx) => (
                <motion.div 
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, scale: 0.95 }}
                  transition={{ delay: idx * 0.05 }}
                  key={idx}
                  role="button"
                  tabIndex={0}
                  aria-label={`Apply ${r.source} preset: ${r.title} — ${r.artist}`}
                  className="bg-void-950/60 backdrop-blur-md border border-void-700/50 hover:border-flow-500/50 hover:bg-void-900 rounded-xl p-2.5 transition-all text-left flex gap-3 items-center cursor-pointer group focus:outline-none focus:ring-1 focus:ring-flow-500/60"
                  onClick={() => handleApplyPreset(r)}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handleApplyPreset(r); } }}
                >
                  {r.cover ? (
                    <img
                      src={r.cover}
                      alt=""
                      referrerPolicy="no-referrer"
                      loading="lazy"
                      decoding="async"
                      onError={(e) => { e.currentTarget.style.display = "none"; }}
                      className="w-10 h-10 rounded-lg object-cover border border-void-700/50 shadow-md group-hover:scale-105 transition-transform"
                    />
                  ) : (
                    <div className="w-10 h-10 rounded-lg bg-void-950 border border-void-700/50 flex items-center justify-center text-slate-500 group-hover:scale-105 transition-transform">
                      <Music className="w-4 h-4" />
                    </div>
                  )}
                  <div className="min-w-0 flex-1">
                    <h5 className="text-white text-xs font-semibold truncate leading-tight group-hover:text-flow-400 transition-colors">{r.title}{r.explicit === true && <span className="ml-1.5 px-1 py-0.5 rounded text-[8px] font-black bg-rose-500/20 text-rose-500 border border-rose-500/30">E</span>}</h5>
                    <p className="text-slate-400 text-[10px] truncate mt-0.5">{r.artist} • <span className="text-flow-400 font-medium font-mono bg-flow-500/10 px-1 py-0.5 rounded">{r.source}</span></p>
                  </div>
                </motion.div>
              ))}
            </AnimatePresence>
            
            {searchResults.length === 0 && !searching && (
              <motion.div 
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="absolute inset-0 flex items-center justify-center text-center p-4 text-slate-600 text-xs font-medium"
              >
                Enter parameters and hit search to find official release tags.
              </motion.div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
