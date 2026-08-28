import React, { useState, useRef, useEffect } from "react";
import {
  AlignLeft,
  Sparkles,
  Database,
  Trash2,
  Check,
  RefreshCw,
  Languages,
  HelpCircle,
  Mic2,
  Target,
  AlertTriangle,
  Wand2
} from "lucide-react";
import { LyricsOption, TrackMetadata, FlaggedLyricLine } from "../types";
import { motion } from "motion/react";

interface LyricsEditorProps {
  lyricsText: string;
  metadata: TrackMetadata;
  filePath?: string;
  onChange: (text: string) => void;
}

// Strips [mm:ss.xx] line timestamps and inline <mm:ss.xx> word-level tags for
// display purposes only -- the real (server-side) parsing that actually
// matters for translation lives in webapp/lib/ollamaTranslate.ts.
function stripLinePreview(line: string): string {
  return line
    .replace(/^(?:\[\d+:\d+(?:\.\d+)?\])+/, "")
    .replace(/<\d+:\d+(?:\.\d+)?>/g, "")
    .trim();
}

interface CursorInfo {
  line: number;
  count: number;
  preview: string;
  hasContent: boolean;
}

const EMPTY_CURSOR_INFO: CursorInfo = { line: 0, count: 1, preview: "", hasContent: false };

// Finds where a previously-flagged line now lives in a (possibly re-shuffled)
// lyrics text by exact content match, preferring the occurrence closest to
// where it used to be -- avoids fragile index bookkeeping across edits.
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

export default function LyricsEditor({
  lyricsText,
  metadata,
  filePath,
  onChange
}: LyricsEditorProps) {
  const [lrclibResults, setLrclibResults] = useState<LyricsOption[]>([]);
  const [searching, setSearching] = useState(false);
  const [translating, setTranslating] = useState(false);
  const [translationMode, setTranslationMode] = useState<"translate_and_merge" | "translate_only">("translate_and_merge");
  const [transcribing, setTranscribing] = useState(false);
  const [translatingLine, setTranslatingLine] = useState(false);
  const [flaggedLines, setFlaggedLines] = useState<FlaggedLyricLine[]>([]);
  const [cursorInfo, setCursorInfo] = useState<CursorInfo>(EMPTY_CURSOR_INFO);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // A new track loaded -- any flagged lines/cursor context from the previous
  // file no longer apply.
  useEffect(() => {
    setFlaggedLines([]);
    setCursorInfo(EMPTY_CURSOR_INFO);
  }, [filePath]);

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

  // Shared tail-end for every /api/translate-line call: apply the returned
  // text and keep the flagged-lines list in sync (a fixed line's exact raw
  // content disappears from the text, so it naturally drops off the list).
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
      setLrclibResults(data);
    } catch (e) {
      console.error(e);
    } finally {
      setSearching(false);
    }
  };

  const handleApplyLyrics = (lyric: LyricsOption) => {
    const text = lyric.synced || lyric.plain;
    onChange(text);
    setFlaggedLines([]); // brand new lyrics -- any earlier flags no longer apply
  };

  const handleTranscribe = async () => {
    if (!filePath) return;
    setTranscribing(true);
    try {
      const res = await fetch("/api/transcribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filePath,
          title: metadata.title,
          artist: metadata.artist,
          genre: metadata.genre
        })
      });
      const data = await res.json();
      if (res.ok && data.lrc) {
        onChange(data.lrc);
        setFlaggedLines([]); // brand new lyrics -- any earlier flags no longer apply
      } else {
        alert(`AI Transcription Error: ${data?.error || `HTTP ${res.status}`}`);
      }
    } catch (e: any) {
      console.error(e);
      alert(`AI Transcription Error: ${e.message || e}`);
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
        body: JSON.stringify({
          lyricsText,
          title: metadata.title,
          artist: metadata.artist,
          mode: translationMode
        })
      });
      const data = await res.json();
      if (data.lyrics) {
        onChange(data.lyrics);
        setFlaggedLines(Array.isArray(data.flaggedLines) ? data.flaggedLines : []);
      } else if (data.error) {
        alert(`AI Translation Error: ${data.error}`);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setTranslating(false);
    }
  };

  // Re-translate just the line at the cursor (or every line spanned by an
  // active textarea selection) -- for fixing the odd line a full-song batch
  // pass above skipped or mistranslated, without re-running the whole song.
  const handleTranslateSelection = async () => {
    if (translating || translatingLine) return;
    const indices = getSelectedLineIndices();
    if (!indices.length) return;
    setTranslatingLine(true);
    try {
      const res = await fetch("/api/translate-line", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lyricsText,
          lineIndices: indices,
          title: metadata.title,
          artist: metadata.artist,
          mode: translationMode
        })
      });
      const data = await res.json();
      if (res.ok && data.lyrics) {
        applyLineTranslationResult(data.lyrics);
      } else {
        alert(`AI Line Translation Error: ${data?.error || `HTTP ${res.status}`}`);
      }
    } catch (e: any) {
      alert(`AI Line Translation Error: ${e.message || e}`);
    } finally {
      setTranslatingLine(false);
    }
  };

  // Re-translate one specific flagged line, located by its exact remembered
  // content rather than a (possibly now-stale) numeric index.
  const handleFixFlaggedLine = async (entry: FlaggedLyricLine) => {
    if (translating || translatingLine) return;
    const idx = locateFlaggedLine(lyricsText.split(/\r\n|\r|\n/), entry);
    if (idx === -1) {
      // The line no longer appears verbatim (already edited by hand) -- just drop it.
      setFlaggedLines((prev) => prev.filter((f) => f !== entry));
      return;
    }
    setTranslatingLine(true);
    try {
      const res = await fetch("/api/translate-line", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lyricsText,
          lineIndices: [idx],
          title: metadata.title,
          artist: metadata.artist,
          mode: translationMode
        })
      });
      const data = await res.json();
      if (res.ok && data.lyrics) {
        applyLineTranslationResult(data.lyrics);
      } else {
        alert(`AI Line Translation Error: ${data?.error || `HTTP ${res.status}`}`);
      }
    } catch (e: any) {
      alert(`AI Line Translation Error: ${e.message || e}`);
    } finally {
      setTranslatingLine(false);
    }
  };

  // Work through every currently-flagged line one at a time (a local model is
  // generally single-request-at-a-time anyway), applying and displaying
  // progress after each one.
  const handleFixAllFlagged = async () => {
    if (translating || translatingLine || !flaggedLines.length) return;
    setTranslatingLine(true);
    try {
      let currentText = lyricsText;
      const queue = flaggedLines;
      for (const entry of queue) {
        const idx = locateFlaggedLine(currentText.split(/\r\n|\r|\n/), entry);
        if (idx === -1) continue; // no longer present -- skip
        try {
          const res = await fetch("/api/translate-line", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              lyricsText: currentText,
              lineIndices: [idx],
              title: metadata.title,
              artist: metadata.artist,
              mode: translationMode
            })
          });
          const data = await res.json();
          if (res.ok && data.lyrics) {
            currentText = data.lyrics;
            onChange(currentText);
          } else if (data?.error) {
            console.warn(`Skipping a flagged line: ${data.error}`);
          }
        } catch (e) {
          console.warn("Skipping a flagged line after an error:", e);
        }
      }
      setFlaggedLines((prev) => recomputeFlaggedLines(currentText, prev));
    } finally {
      setTranslatingLine(false);
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      
      {/* LRC LYRICS TEXTAREA */}
      <div className="lg:col-span-2 bg-[#0f1117] rounded-xl border border-slate-800 p-6 shadow-2xl flex flex-col ring-1 ring-white/5">
        <div className="flex flex-wrap items-center justify-between gap-4 mb-4 border-b border-slate-800/60 pb-3">
          <h3 className="text-white font-medium text-lg flex items-center gap-2">
            <AlignLeft className="w-5 h-5 text-indigo-400" />
            LRC Synced / Plain Lyrics Editor
          </h3>
          <div className="flex items-center gap-2">
            <button 
              onClick={() => {
                onChange("");
                setFlaggedLines([]);
              }}
              className="px-2.5 py-1.5 bg-[#0b0e14]/80 border border-slate-800 hover:bg-slate-800 text-slate-300 rounded-lg text-xs flex items-center gap-1 transition-colors cursor-pointer"
            >
              <Trash2 className="w-3.5 h-3.5 text-rose-500" />
              Wipe
            </button>
          </div>
        </div>

        <textarea
          ref={textareaRef}
          value={lyricsText}
          onChange={(e) => {
            onChange(e.target.value);
            updateCursorInfo();
          }}
          onSelect={updateCursorInfo}
          onClick={updateCursorInfo}
          onKeyUp={updateCursorInfo}
          placeholder="[00:10.00]Line 1
[00:15.50]Line 2..."
          className="w-full h-80 bg-[#0b0e14]/60 border border-slate-800 rounded-lg p-4 text-white text-sm font-mono focus:outline-none focus:border-indigo-500 transition-colors custom-scrollbar resize-none flex-1 leading-relaxed"
        />

        {/* Cursor/selection-targeted re-translate -- fixes the odd line a
            full-song batch pass skipped, without re-running the whole song. */}
        <div className="mt-3 pt-3 border-t border-slate-800/60 flex flex-wrap items-center justify-between gap-3">
          <div className="text-xs text-slate-500 min-w-0 flex-1 truncate">
            {cursorInfo.count > 1 ? (
              <span>
                Selected <span className="text-slate-300 font-medium">{cursorInfo.count} lines</span>, starting at line{" "}
                <span className="text-slate-300 font-medium">{cursorInfo.line + 1}</span>
              </span>
            ) : (
              <span>
                Line <span className="text-slate-300 font-medium">{cursorInfo.line + 1}</span>
                {cursorInfo.preview && (
                  <>
                    {": "}
                    <span className="text-slate-400 italic">"{cursorInfo.preview}"</span>
                  </>
                )}
              </span>
            )}
          </div>
          <button
            onClick={handleTranslateSelection}
            disabled={translating || translatingLine || !cursorInfo.hasContent}
            title="Re-translate just this line (or selection) with the AI Translator"
            className="shrink-0 px-3 py-1.5 bg-cyan-600/20 hover:bg-cyan-600/30 disabled:bg-slate-800/60 disabled:text-slate-600 border border-cyan-700/40 disabled:border-slate-800 text-cyan-300 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition-colors cursor-pointer disabled:cursor-not-allowed"
          >
            {translatingLine ? (
              <RefreshCw className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Target className="w-3.5 h-3.5" />
            )}
            {cursorInfo.count > 1 ? `Translate ${cursorInfo.count} Lines` : "Translate This Line"}
          </button>
        </div>
      </div>

      {/* GEMINI INTELLIGENCE & DB SEARCH SIDEBAR */}
      <div className="space-y-6">
        
        {/* AI Translating Engine Card */}
        <div className="bg-[#0f1117] rounded-xl border border-slate-800 p-6 shadow-2xl relative overflow-hidden ring-1 ring-white/5">
          <div className="absolute top-0 right-0 w-24 h-24 bg-indigo-500/5 rounded-full blur-2xl pointer-events-none" />

          <h4 className="text-white font-semibold text-sm uppercase tracking-wider mb-4 flex items-center gap-1.5">
            <Sparkles className="w-4 h-4 text-indigo-400" />
            AI Translator
          </h4>

          <p className="text-slate-400 text-xs mb-4">
            Uses your selected translation engine (Ollama or Google Gemini Pro) from Settings. Translates text line-for-line, retaining timed synchronization anchors intact.
          </p>

          <div className="mb-4">
            <label className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-1.5 block">Translation Mode</label>
            <select
              value={translationMode}
              onChange={(e) => setTranslationMode(e.target.value as any)}
              className="w-full bg-[#0b0e14] border border-slate-800 rounded-lg py-2 px-3 text-white text-xs focus:outline-none focus:border-indigo-500 transition-colors cursor-pointer"
            >
              <option value="translate_and_merge">Bilingual （Side-by-Side）</option>
              <option value="translate_only">Replace with English Translation Only</option>
            </select>
          </div>

          <button
            onClick={handleLocalTranslate}
            disabled={translating || !lyricsText.trim()}
            className="w-full py-3 bg-gradient-to-r from-indigo-600 to-violet-600 hover:from-indigo-500 hover:to-violet-500 disabled:from-slate-800 disabled:to-slate-800 disabled:text-slate-600 text-white rounded-lg text-sm font-semibold transition-all shadow-lg flex items-center justify-center gap-2 cursor-pointer border-0"
          >
            {translating ? (
              <>
                <RefreshCw className="w-4 h-4 animate-spin text-indigo-200" />
                Translating...
              </>
            ) : (
              <>
                <Languages className="w-4 h-4 text-indigo-200" />
                Apply AI Translation
              </>
            )}
          </button>

          {/* Lines the batch pass above likely skipped or mistranslated --
              still non-English with no translation added. Fix them
              individually or all at once, without re-running the whole song. */}
          {flaggedLines.length > 0 && (
            <div className="mt-4 pt-4 border-t border-slate-800/60">
              <div className="flex items-center justify-between gap-2 mb-2">
                <h5 className="text-amber-400 text-[11px] font-semibold uppercase tracking-wider flex items-center gap-1.5">
                  <AlertTriangle className="w-3.5 h-3.5" />
                  {flaggedLines.length} line{flaggedLines.length > 1 ? "s" : ""} may need attention
                </h5>
                <button
                  onClick={handleFixAllFlagged}
                  disabled={translating || translatingLine}
                  className="shrink-0 text-[10px] font-semibold text-amber-300 hover:text-amber-200 disabled:text-slate-600 uppercase tracking-wider cursor-pointer disabled:cursor-not-allowed"
                >
                  Fix All
                </button>
              </div>
              <div className="space-y-1.5 max-h-[160px] overflow-y-auto custom-scrollbar pr-1">
                {flaggedLines.map((f) => (
                  <div
                    key={`${f.index}-${f.rawLine}`}
                    className="flex items-center gap-2 bg-[#0b0e14]/60 border border-amber-900/30 rounded-lg px-2.5 py-1.5"
                  >
                    <span className="text-slate-400 text-[11px] truncate flex-1" title={f.preview}>
                      {f.preview || "(blank)"}
                    </span>
                    <button
                      onClick={() => handleFixFlaggedLine(f)}
                      disabled={translating || translatingLine}
                      title="Re-translate this line"
                      className="shrink-0 text-amber-400 hover:text-amber-300 disabled:text-slate-600 cursor-pointer disabled:cursor-not-allowed"
                    >
                      <Wand2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Synced LRC Lyrics Database imports */}
        <div className="bg-[#0f1117] rounded-xl border border-slate-800 p-6 shadow-2xl ring-1 ring-white/5">
          <h4 className="text-white font-semibold text-sm uppercase tracking-wider mb-3 flex items-center gap-1.5">
            <Database className="w-4 h-4 text-emerald-400" />
            LRCLIB Synced DB
          </h4>

          <button
            onClick={handleFetchDatabase}
            disabled={searching}
            className="w-full py-2 bg-[#0b0e14]/80 hover:bg-slate-800 border border-slate-800 hover:border-slate-700 text-white rounded-lg text-xs font-semibold transition-all flex items-center justify-center gap-1.5 cursor-pointer mb-3"
          >
            {searching ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Database className="w-3.5 h-3.5" />}
            Scan Cloud Database
          </button>

          <div className="overflow-y-auto max-h-[160px] space-y-2 pr-1 custom-scrollbar">
            {lrclibResults.map((l, idx) => (
              <div 
                key={idx}
                className="bg-[#0b0e14]/60 border border-slate-800/60 hover:border-indigo-500/50 rounded-lg p-2.5 transition-all text-left flex gap-2 items-center cursor-pointer group"
                onClick={() => handleApplyLyrics(l)}
              >
                <div className="min-w-0 flex-1">
                  <h5 className="text-white text-xs font-semibold truncate leading-tight">{l.title}</h5>
                  <p className="text-slate-400 text-[10px] truncate">{l.artist} • <span className="text-emerald-400 font-medium">{l.synced ? "Synced LRC" : "Plain Lyrics"}</span></p>
                </div>
              </div>
            ))}
            {lrclibResults.length === 0 && !searching && (
              <div className="text-center py-6 text-slate-600 text-xs">
                Scan the cloud to pull timing blocks from LRCLIB community database.
              </div>
            )}
          </div>
        </div>

        {/* AI Transcription fallback — Demucs vocal isolation + Faster-Whisper, same
            engine rezakir.py falls back to when no database has lyrics for a track. */}
        <div className="bg-[#0f1117] rounded-xl border border-slate-800 p-6 shadow-2xl ring-1 ring-white/5">
          <h4 className="text-white font-semibold text-sm uppercase tracking-wider mb-3 flex items-center gap-1.5">
            <Mic2 className="w-4 h-4 text-amber-400" />
            AI Transcription (Local)
          </h4>

          <p className="text-slate-400 text-xs mb-4">
            No lyrics in any database? Isolate vocals with Demucs and transcribe them locally with Faster-Whisper —
            the same fallback <code className="font-mono">rezakir.py</code> uses. Runs entirely on your machine;
            can take a few minutes and installs its own (large) AI dependencies on first use.
          </p>

          <button
            onClick={handleTranscribe}
            disabled={transcribing || !filePath}
            title={!filePath ? "Upload a track first" : undefined}
            className="w-full py-3 bg-gradient-to-r from-amber-600 to-orange-600 hover:from-amber-500 hover:to-orange-500 disabled:from-slate-800 disabled:to-slate-800 disabled:text-slate-600 text-white rounded-lg text-sm font-semibold transition-all shadow-lg flex items-center justify-center gap-2 cursor-pointer border-0"
          >
            {transcribing ? (
              <>
                <RefreshCw className="w-4 h-4 animate-spin" />
                Transcribing... (this can take a few minutes)
              </>
            ) : (
              <>
                <Mic2 className="w-4 h-4" />
                Transcribe with AI
              </>
            )}
          </button>
        </div>

      </div>
    </div>
  );
}
