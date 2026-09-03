import React, { useState, useRef, useEffect } from "react";
import {
  AlignLeft, Sparkles, Database, Trash2, Check, RefreshCw, Languages,
  HelpCircle, Mic2, Target, AlertTriangle, Wand2, Clock
} from "lucide-react";
import { LyricsOption, TrackMetadata, FlaggedLyricLine } from "../types";
import { motion, AnimatePresence } from "motion/react";
import { toast } from "../Toasts";

interface LyricsEditorProps {
  lyricsText: string;
  metadata: TrackMetadata;
  filePath?: string;
  onChange: (text: string) => void;
}

function stripLinePreview(line: string): string {
  return line.replace(/^(?:\[\d+:\d+(?:\.\d+)?\])+/, "").replace(/<\d+:\d+(?:\.\d+)?>/g, "").trim();
}

interface CursorInfo {
  line: number;
  count: number;
  preview: string;
  hasContent: boolean;
}

const EMPTY_CURSOR_INFO: CursorInfo = { line: 0, count: 1, preview: "", hasContent: false };

function locateFlaggedLine(lines: string[], snapshot: FlaggedLyricLine): number {
  if (lines[snapshot.index] === snapshot.rawLine) return snapshot.index;
  let best = -1;
  let bestDist = Infinity;
  lines.forEach((l, i) => {
    if (l === snapshot.rawLine) {
      const dist = Math.abs(i - snapshot.index);
      if (dist < bestDist) {
        bestDist = dist;
        best = i;
      }
    }
  });
  return best;
}

function recomputeFlaggedLines(newText: string, prevFlags: FlaggedLyricLine[]): FlaggedLyricLine[] {
  if (!prevFlags.length) return prevFlags;
  const lines = newText.split(/\r\n|\r|\n/);
  const next: FlaggedLyricLine[] = [];
  for (const f of prevFlags) {
    const idx = locateFlaggedLine(lines, f);
    if (idx !== -1) next.push({ ...f, index: idx });
  }
  return next;
}

export default function LyricsEditor({ lyricsText, metadata, filePath, onChange }: LyricsEditorProps) {
  const [lrclibResults, setLrclibResults] = useState<LyricsOption[]>([]);
  const [searching, setSearching] = useState(false);
  const [translating, setTranslating] = useState(false);
  const [translateProgress, setTranslateProgress] = useState(0);
  const [translationMode, setTranslationMode] = useState<"translate_and_merge" | "translate_only">("translate_and_merge");
  const [transcribing, setTranscribing] = useState(false);
  const [transcribeProgress, setTranscribeProgress] = useState(0);
  const [translatingLine, setTranslatingLine] = useState(false);
  const [flaggedLines, setFlaggedLines] = useState<FlaggedLyricLine[]>([]);
  const [cursorInfo, setCursorInfo] = useState<CursorInfo>(EMPTY_CURSOR_INFO);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    setFlaggedLines([]);
    setCursorInfo(EMPTY_CURSOR_INFO);
  }, [filePath]);

  // Simulate progress
  useEffect(() => {
    let interval: any;
    if (translating) {
      setTranslateProgress(0);
      interval = setInterval(() => {
        setTranslateProgress(p => p < 90 ? p + (90 / (15 * 10)) : p); // ~15 seconds to reach 90%
      }, 100);
    } else {
      setTranslateProgress(100);
    }
    return () => clearInterval(interval);
  }, [translating]);

  useEffect(() => {
    let interval: any;
    if (transcribing) {
      setTranscribeProgress(0);
      interval = setInterval(() => {
        setTranscribeProgress(p => p < 95 ? p + (95 / (90 * 10)) : p); // ~90 seconds to reach 95%
      }, 100);
    } else {
      setTranscribeProgress(100);
    }
    return () => clearInterval(interval);
  }, [transcribing]);

  const updateCursorInfo = () => {
    const el = textareaRef.current;
    if (!el) return;
    const value = el.value;
    const start = el.selectionStart ?? 0;
    const end = el.selectionEnd ?? start;
    const startLine = value.slice(0, start).split("\n").length - 1;
    const endLine = value.slice(0, end).split("\n").length - 1;
    const lo = Math.min(startLine, endLine);
    const hi = Math.max(startLine, endLine);
    const allLines = value.split("\n");
    const preview = stripLinePreview(allLines[lo] || "");
    const hasContent = allLines.slice(lo, hi + 1).some((l) => stripLinePreview(l).length > 0);
    setCursorInfo({ line: lo, count: hi - lo + 1, preview, hasContent });
  };

  const getSelectedLineIndices = (): number[] => {
    const el = textareaRef.current;
    if (!el) return [];
    const value = el.value;
    const start = el.selectionStart ?? 0;
    const end = el.selectionEnd ?? start;
    const startLine = value.slice(0, start).split("\n").length - 1;
    const endLine = value.slice(0, end).split("\n").length - 1;
    const lo = Math.min(startLine, endLine);
    const hi = Math.max(startLine, endLine);
    const indices: number[] = [];
    for (let i = lo; i <= hi; i++) indices.push(i);
    return indices;
  };

  const applyLineTranslationResult = (newLyrics: string) => {
    onChange(newLyrics);
    setFlaggedLines((prev) => recomputeFlaggedLines(newLyrics, prev));
  };

  const handleFetchDatabase = async () => {
    setSearching(true);
    try {
      const q = `title=${encodeURIComponent(metadata.title)}&artist=${encodeURIComponent(metadata.artist)}`;
      const res = await fetch(`/api/fetch-lyrics?${q}`);
      const data = await res.json();
      // The endpoint answers with [] on upstream trouble and { error } on a
      // missing title — anything but a real array must not reach the list.
      if (!Array.isArray(data)) {
        setLrclibResults([]);
        toast(`LRCLIB search failed: ${data?.error || `HTTP ${res.status}`}`);
      } else {
        setLrclibResults(data);
        if (data.length === 0) toast("LRCLIB found no synced or plain lyrics for this title/artist.", "info");
      }
    } catch (e: any) {
      console.error(e);
      toast(`LRCLIB search failed: ${e.message || e}`);
    } finally {
      setSearching(false);
    }
  };

  const handleApplyLyrics = (lyric: LyricsOption) => {
    const text = lyric.synced || lyric.plain;
    onChange(text);
    setFlaggedLines([]);
  };

  const handleTranscribe = async () => {
    if (!filePath) return;
    setTranscribing(true);
    try {
      const res = await fetch("/api/transcribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filePath, title: metadata.title, artist: metadata.artist, genre: metadata.genre })
      });
      const data = await res.json();
      if (res.ok && data.lrc) {
        onChange(data.lrc);
        setFlaggedLines([]);
      } else {
        toast(`AI Transcription Error: ${data?.error || `HTTP ${res.status}`}`);
      }
    } catch (e: any) {
      console.error(e);
      toast(`AI Transcription Error: ${e.message || e}`);
    } finally {
      setTranscribing(false);
    }
  };

  const handleLocalTranslate = async () => {
    if (!lyricsText.trim()) return;
    setTranslating(true);
    try {
      const res = await fetch("/api/generate-lyrics", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lyricsText, title: metadata.title, artist: metadata.artist, mode: translationMode })
      });
      const data = await res.json();
      if (data.lyrics) {
        onChange(data.lyrics);
        setFlaggedLines(Array.isArray(data.flaggedLines) ? data.flaggedLines : []);
      } else if (data.error) {
        toast(`AI Translation Error: ${data.error}`);
      } else if (!res.ok) {
        toast(`AI Translation Error: HTTP ${res.status}`);
      }
    } catch (e: any) {
      console.error(e);
      toast(`AI Translation Error: ${e.message || e}`);
    } finally {
      setTranslating(false);
    }
  };

  const handleTranslateSelection = async () => {
    if (translating || translatingLine) return;
    const indices = getSelectedLineIndices();
    if (!indices.length) return;
    setTranslatingLine(true);
    try {
      const res = await fetch("/api/translate-line", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lyricsText, lineIndices: indices, title: metadata.title, artist: metadata.artist, mode: translationMode })
      });
      const data = await res.json();
      if (res.ok && data.lyrics) {
        applyLineTranslationResult(data.lyrics);
      } else {
        toast(`AI Line Translation Error: ${data?.error || `HTTP ${res.status}`}`);
      }
    } catch (e: any) {
      toast(`AI Line Translation Error: ${e.message || e}`);
    } finally {
      setTranslatingLine(false);
    }
  };

  const handleFixFlaggedLine = async (entry: FlaggedLyricLine) => {
    if (translating || translatingLine) return;
    const idx = locateFlaggedLine(lyricsText.split(/\r\n|\r|\n/), entry);
    if (idx === -1) {
      setFlaggedLines((prev) => prev.filter((f) => f !== entry));
      return;
    }
    setTranslatingLine(true);
    try {
      const res = await fetch("/api/translate-line", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lyricsText, lineIndices: [idx], title: metadata.title, artist: metadata.artist, mode: translationMode })
      });
      const data = await res.json();
      if (res.ok && data.lyrics) {
        applyLineTranslationResult(data.lyrics);
      } else {
        toast(`AI Line Translation Error: ${data?.error || `HTTP ${res.status}`}`);
      }
    } catch (e: any) {
      toast(`AI Line Translation Error: ${e.message || e}`);
    } finally {
      setTranslatingLine(false);
    }
  };

  const handleFixAllFlagged = async () => {
    if (translating || translatingLine || !flaggedLines.length) return;
    setTranslatingLine(true);
    try {
      let currentText = lyricsText;
      const queue = flaggedLines;
      for (const entry of queue) {
        const idx = locateFlaggedLine(currentText.split(/\r\n|\r|\n/), entry);
        if (idx === -1) continue;
        try {
          const res = await fetch("/api/translate-line", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lyricsText: currentText, lineIndices: [idx], title: metadata.title, artist: metadata.artist, mode: translationMode })
          });
          const data = await res.json();
          if (res.ok && data.lyrics) {
            currentText = data.lyrics;
            onChange(currentText);
          }
        } catch (e) {
          console.error(e);
        }
      }
      setFlaggedLines((prev) => recomputeFlaggedLines(currentText, prev));
    } finally {
      setTranslatingLine(false);
    }
  };

  const renderProgressBar = (progress: number, colorClass: string = "bg-deezer-500", text: string = "Processing") => (
    <div className="mt-3 relative">
      <div className="h-1.5 w-full bg-void-950/50 rounded-full overflow-hidden mb-1.5 border border-void-700/30">
        <motion.div 
          initial={{ width: 0 }}
          animate={{ width: `${progress}%` }}
          transition={{ ease: "linear", duration: 0.1 }}
          className={`h-full ${colorClass} rounded-full`}
        />
      </div>
      <div className="flex justify-between items-center text-[10px] uppercase font-bold tracking-widest text-slate-400">
        <span className="flex items-center gap-1">
          <Clock className="w-3 h-3" /> {text}
        </span>
        <span>{Math.round(progress)}%</span>
      </div>
    </div>
  );

  return (
    <div className="grid grid-cols-3 gap-6 h-[calc(100vh-140px)]">
      {/* TEXT EDITOR AREA */}
      <div className="col-span-2 bg-void-900/60 backdrop-blur-md rounded-2xl border border-void-700/50 p-6 flex flex-col h-full shadow-2xl relative">
        <div className="absolute inset-0 bg-gradient-to-br from-flow-500/5 to-transparent rounded-2xl pointer-events-none" />
        
        <div className="flex items-center justify-between mb-4 relative z-10">
          <h4 className="text-white font-semibold text-lg flex items-center gap-2">
            <AlignLeft className="w-5 h-5 text-flow-400" />
            LRC Synced / Plain Lyrics
          </h4>
          <motion.button
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            onClick={() => {
              if (confirm("Are you sure you want to clear all lyrics?")) {
                onChange("");
                setFlaggedLines([]);
              }
            }}
            disabled={!lyricsText.trim()}
            className="px-3 py-1.5 bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/20 rounded-lg text-xs font-semibold uppercase tracking-wider transition-all disabled:opacity-50 cursor-pointer flex items-center gap-1.5"
          >
            <Trash2 className="w-3.5 h-3.5" /> Wipe
          </motion.button>
        </div>

        <div className="flex-1 relative mb-4">
          <textarea
            ref={textareaRef}
            value={lyricsText}
            onChange={(e) => onChange(e.target.value)}
            onKeyUp={updateCursorInfo}
            onMouseUp={updateCursorInfo}
            placeholder="[00:10.00]Line 1&#10;[00:15.50]Line 2..."
            // Lock the editor while a transcription/translation round-trip is
            // in flight: those flows replace the whole text on completion, so
            // anything typed mid-flight used to vanish with no feedback.
            readOnly={transcribing || translating || translatingLine}
            aria-busy={transcribing || translating || translatingLine}
            title={transcribing || translating || translatingLine ? "Waiting on AI result — editing resumes automatically" : undefined}
            className={`w-full h-full bg-void-950/80 backdrop-blur-sm border border-void-700/60 rounded-xl p-4 text-white font-mono text-sm leading-relaxed focus:outline-none focus:border-flow-500/80 focus:ring-1 focus:ring-flow-500/50 resize-none custom-scrollbar transition-all shadow-inner ${transcribing || translating || translatingLine ? "opacity-60 cursor-progress" : "hover:bg-void-900"}`}
            spellCheck={false}
          />
        </div>

        <div className="flex items-center justify-between bg-void-950/80 border border-void-700/60 rounded-xl px-4 py-2.5 relative z-10">
          <div className="text-[11px] font-mono text-slate-400 font-medium">
            Line <span className="text-white">{cursorInfo.line + 1}</span> 
            {cursorInfo.count > 1 ? ` - ${cursorInfo.line + cursorInfo.count}` : ""}
            <span className="ml-4 text-slate-500 hidden md:inline">
              {cursorInfo.preview ? `"${cursorInfo.preview.substring(0, 30)}${cursorInfo.preview.length > 30 ? "..." : ""}"` : ""}
            </span>
          </div>
          
          <motion.button
            whileHover={cursorInfo.hasContent && !translating && !translatingLine ? { scale: 1.02 } : {}}
            whileTap={cursorInfo.hasContent && !translating && !translatingLine ? { scale: 0.98 } : {}}
            onClick={handleTranslateSelection}
            disabled={!cursorInfo.hasContent || translating || translatingLine}
            className="px-4 py-1.5 bg-void-800 hover:bg-void-700 border border-void-600 disabled:border-void-800 disabled:text-slate-600 text-slate-300 rounded-lg text-[11px] font-bold uppercase tracking-wider transition-all disabled:opacity-50 cursor-pointer flex items-center gap-1.5"
          >
            {translatingLine ? <RefreshCw className="w-3.5 h-3.5 animate-spin text-deezer-400" /> : <Target className="w-3.5 h-3.5 text-flow-400" />}
            {cursorInfo.count > 1 ? `Translate ${cursorInfo.count} Lines` : "Translate This Line"}
          </motion.button>
        </div>
      </div>

      {/* GEMINI INTELLIGENCE & DB SEARCH SIDEBAR */}
      <div className="space-y-6 overflow-y-auto custom-scrollbar pr-2 pb-6">
        
        {/* AI Translating Engine Card */}
        <div className="bg-void-900/60 backdrop-blur-md rounded-2xl border border-void-700/50 p-6 relative overflow-hidden shadow-xl group hover:border-deezer-500/50 transition-colors duration-500">
          <div className="absolute top-0 right-0 p-3 opacity-10 group-hover:opacity-20 transition-opacity">
            <Sparkles className="w-24 h-24 text-deezer-400" />
          </div>
          
          <h4 className="text-white font-bold text-sm uppercase tracking-widest mb-3 flex items-center gap-2 relative z-10">
            <Sparkles className="w-4 h-4 text-deezer-400" />
            AI Translator
          </h4>
          <p className="text-slate-400 text-xs mb-5 relative z-10 font-medium leading-relaxed">
            Translates text line-for-line, retaining timed synchronization anchors intact using selected AI engine.
          </p>
          
          <div className="mb-5 relative z-10">
            <label className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-2 block">Translation Mode</label>
            <div className="relative group/select">
              <select
                value={translationMode}
                onChange={(e) => setTranslationMode(e.target.value as any)}
                className="w-full bg-void-950 border border-void-700 rounded-xl py-2.5 px-3 text-white text-xs font-semibold focus:outline-none focus:border-deezer-500/80 transition-all cursor-pointer hover:bg-void-900 appearance-none shadow-inner"
              >
                <option value="translate_and_merge">Bilingual (Side-by-Side)</option>
                <option value="translate_only">Replace with English Translation Only</option>
              </select>
              <div className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none text-slate-400">▼</div>
            </div>
          </div>
          
          <motion.button
            whileHover={!translating && lyricsText.trim() ? { scale: 1.02 } : {}}
            whileTap={!translating && lyricsText.trim() ? { scale: 0.98 } : {}}
            onClick={handleLocalTranslate}
            disabled={translating || !lyricsText.trim()}
            className="w-full py-3.5 bg-gradient-to-r from-deezer-600 to-flow-500 hover:from-deezer-500 hover:to-flow-400 disabled:from-slate-800 disabled:to-slate-800 disabled:text-slate-600 text-white rounded-xl text-xs uppercase tracking-widest font-bold transition-all shadow-lg flex items-center justify-center gap-2 cursor-pointer border-0 relative z-10"
          >
            {translating ? (
              <>
                <RefreshCw className="w-4 h-4 animate-spin text-white" />
                Translating...
              </>
            ) : (
              <>
                <Languages className="w-4 h-4 text-white" />
                Apply AI Translation
              </>
            )}
          </motion.button>
          
          <AnimatePresence>
            {translating && (
              <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}>
                {renderProgressBar(translateProgress, "bg-gradient-to-r from-deezer-500 to-flow-400", "Est. 15-30s based on model")}
              </motion.div>
            )}
          </AnimatePresence>

          {flaggedLines.length > 0 && (
            <motion.div 
              initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
              className="mt-5 pt-4 border-t border-void-700/60 relative z-10"
            >
              <div className="flex items-center justify-between gap-2 mb-3">
                <h5 className="text-amber-400 text-[11px] font-bold uppercase tracking-widest flex items-center gap-1.5">
                  <AlertTriangle className="w-3.5 h-3.5" />
                  {flaggedLines.length} Flagged {flaggedLines.length > 1 ? "Lines" : "Line"}
                </h5>
                <button
                  onClick={handleFixAllFlagged}
                  disabled={translating || translatingLine}
                  className="shrink-0 text-[10px] font-bold text-amber-300 hover:text-amber-200 bg-amber-500/10 px-2 py-1 rounded disabled:opacity-50 uppercase tracking-widest cursor-pointer transition-colors"
                >
                  Fix All
                </button>
              </div>
              <div className="space-y-2 max-h-[160px] overflow-y-auto custom-scrollbar pr-1">
                {flaggedLines.map((f) => (
                  <div key={`${f.index}-${f.rawLine}`} className="flex items-center gap-2 bg-void-950 border border-amber-900/30 hover:border-amber-500/50 rounded-lg px-3 py-2 transition-colors">
                    <span className="text-slate-400 text-xs truncate flex-1 font-mono" title={f.preview}>
                      {f.preview || "(blank)"}
                    </span>
                    <button
                      onClick={() => handleFixFlaggedLine(f)}
                      disabled={translating || translatingLine}
                      title="Re-translate this line"
                      className="shrink-0 text-amber-400 hover:text-amber-300 bg-void-900 p-1.5 rounded disabled:opacity-50 cursor-pointer transition-colors"
                    >
                      <Wand2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            </motion.div>
          )}
        </div>

        {/* Synced LRC Lyrics Database imports */}
        <div className="bg-void-900/60 backdrop-blur-md rounded-2xl border border-void-700/50 p-6 group hover:border-emerald-500/50 transition-colors duration-500 shadow-xl relative overflow-hidden">
          <div className="absolute top-0 right-0 p-3 opacity-5 group-hover:opacity-10 transition-opacity">
            <Database className="w-24 h-24 text-emerald-400" />
          </div>
          
          <h4 className="text-white font-bold text-sm uppercase tracking-widest mb-4 flex items-center gap-2 relative z-10">
            <Database className="w-4 h-4 text-emerald-400" />
            LRCLIB Synced DB
          </h4>
          
          <motion.button
            whileHover={!searching ? { scale: 1.02 } : {}}
            whileTap={!searching ? { scale: 0.98 } : {}}
            onClick={handleFetchDatabase}
            disabled={searching}
            className="w-full py-2.5 bg-void-950 hover:bg-void-800 border border-void-700 hover:border-emerald-500/50 text-white rounded-xl text-xs uppercase tracking-widest font-bold transition-all flex items-center justify-center gap-2 cursor-pointer mb-4 relative z-10 shadow-inner"
          >
            {searching ? <RefreshCw className="w-3.5 h-3.5 animate-spin text-emerald-400" /> : <Database className="w-3.5 h-3.5 text-emerald-400" />}
            Scan Cloud Database
          </motion.button>
          
          <div className="overflow-y-auto max-h-[160px] space-y-2 pr-1 custom-scrollbar relative z-10">
            <AnimatePresence>
              {lrclibResults.map((l, idx) => (
                <motion.div 
                  initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }}
                  key={idx}
                  role="button"
                  tabIndex={0}
                  aria-label={`Apply ${l.synced ? "synced" : "plain"} lyrics: ${l.title} — ${l.artist}`}
                  className="bg-void-950/80 border border-void-700/60 hover:border-emerald-500/50 hover:bg-void-900 rounded-xl p-3 transition-all text-left flex gap-2 items-center cursor-pointer group/item focus:outline-none focus:ring-1 focus:ring-emerald-500/60"
                  onClick={() => handleApplyLyrics(l)}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handleApplyLyrics(l); } }}
                >
                  <div className="min-w-0 flex-1">
                    <h5 className="text-white text-xs font-semibold truncate leading-tight group-hover/item:text-emerald-400 transition-colors">{l.title}</h5>
                    <p className="text-slate-400 text-[10px] truncate mt-0.5">{l.artist} • <span className="text-emerald-400 font-bold bg-emerald-500/10 px-1 py-0.5 rounded">{l.synced ? "Synced LRC" : "Plain Lyrics"}</span></p>
                  </div>
                </motion.div>
              ))}
            </AnimatePresence>
            {lrclibResults.length === 0 && !searching && (
              <div className="text-center py-6 text-slate-500 text-xs font-medium border border-dashed border-void-700/50 rounded-xl">
                Scan the cloud to pull timing blocks from LRCLIB community database.
              </div>
            )}
          </div>
        </div>

        {/* AI Transcription fallback */}
        <div className="bg-void-900/60 backdrop-blur-md rounded-2xl border border-void-700/50 p-6 group hover:border-amber-500/50 transition-colors duration-500 shadow-xl relative overflow-hidden">
          <div className="absolute top-0 right-0 p-3 opacity-5 group-hover:opacity-10 transition-opacity">
            <Mic2 className="w-24 h-24 text-amber-500" />
          </div>

          <h4 className="text-white font-bold text-sm uppercase tracking-widest mb-3 flex items-center gap-2 relative z-10">
            <Mic2 className="w-4 h-4 text-amber-500" />
            AI Transcription
          </h4>
          <p className="text-slate-400 text-xs mb-5 relative z-10 font-medium leading-relaxed">
            No lyrics in any database? Isolate vocals with Demucs and transcribe them locally with Faster-Whisper.
          </p>
          
          <motion.button
            whileHover={!transcribing && filePath ? { scale: 1.02 } : {}}
            whileTap={!transcribing && filePath ? { scale: 0.98 } : {}}
            onClick={handleTranscribe}
            disabled={transcribing || !filePath}
            title={!filePath ? "Upload a track first" : undefined}
            className="w-full py-3.5 bg-gradient-to-r from-amber-600 to-orange-600 hover:from-amber-500 hover:to-orange-500 disabled:from-slate-800 disabled:to-slate-800 disabled:text-slate-600 text-white rounded-xl text-xs uppercase tracking-widest font-bold transition-all shadow-lg flex items-center justify-center gap-2 cursor-pointer border-0 relative z-10"
          >
            {transcribing ? (
              <>
                <RefreshCw className="w-4 h-4 animate-spin text-white" />
                Transcribing...
              </>
            ) : (
              <>
                <Mic2 className="w-4 h-4 text-white" />
                Transcribe with AI
              </>
            )}
          </motion.button>

          <AnimatePresence>
            {transcribing && (
              <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}>
                {renderProgressBar(transcribeProgress, "bg-gradient-to-r from-amber-500 to-orange-400", "Est. 1-3m based on track length")}
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}
