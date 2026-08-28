import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Clapperboard,
  UploadCloud,
  RefreshCw,
  Trash2,
  Plus,
  Play,
  AlertTriangle,
  CheckCircle2,
  ArrowDownToLine,
  Languages,
  Users,
  Pencil,
  FileVideo,
  Video,
  RotateCcw,
  Sparkles
} from "lucide-react";
import { CaptionSegment } from "../types";

interface UploadedVideo {
  originalName: string;
  filename: string;
  path: string;
  size: number;
}

// Same hex values, same first-appearance-order color assignment as
// SPEAKER_PALETTE / build_ass() in monster_archiver/video_captions.py, so
// the review UI's speaker colors match exactly what ffmpeg actually burns
// into the video.
const SPEAKER_PALETTE = [
  "#FFD400", // amber
  "#00E5FF", // cyan
  "#FF4FA3", // pink
  "#8CFF6B", // green
  "#FF6B4A", // orange-red
  "#B48CFF", // violet
  "#FFFFFF", // white
  "#6BD9FF" // light blue
];

const LANG_OPTIONS = [
  { value: "fa", label: "Persian (فارسی)" },
  { value: "auto", label: "Auto-detect" },
  { value: "en", label: "English" },
  { value: "ar", label: "Arabic" },
  { value: "fr", label: "French" },
  { value: "es", label: "Spanish" }
];

// Mirrors lib/ollamaTranslate.ts's CAPTION_TARGET_LANGUAGES -- keep in sync.
// No "auto" entry here since a translation target has to be a real language.
const TARGET_LANG_OPTIONS = [
  { value: "fa", label: "Persian (فارسی)" },
  { value: "en", label: "English" },
  { value: "ar", label: "Arabic" },
  { value: "fr", label: "French" },
  { value: "es", label: "Spanish" }
];

const ALLOWED_VIDEO_EXTS = [".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"];

function formatTimestamp(seconds: number): string {
  const s = Math.max(0, Number.isFinite(seconds) ? seconds : 0);
  const m = Math.floor(s / 60);
  const secs = s - m * 60;
  return `${m}:${secs.toFixed(1).padStart(4, "0")}`;
}

// Mirrors build_ass()'s speaker->color assignment: walk the segments in
// order, assign the next palette color the first time a speaker label
// appears. Recomputed on every edit so reassigning/reordering speakers
// keeps the preview in sync with what would actually get burned.
function getSpeakerColorMap(segments: CaptionSegment[]): Record<string, string> {
  const order: string[] = [];
  for (const seg of segments) {
    if (!order.includes(seg.speaker)) order.push(seg.speaker);
  }
  const map: Record<string, string> = {};
  order.forEach((sp, i) => {
    map[sp] = SPEAKER_PALETTE[i % SPEAKER_PALETTE.length];
  });
  return map;
}

export default function VideoCaptionsTab() {
  const [uploadedVideo, setUploadedVideo] = useState<UploadedVideo | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);

  const [lang, setLang] = useState("fa");
  const [videoTitle, setVideoTitle] = useState("");

  const [transcribing, setTranscribing] = useState(false);
  const [transcribeError, setTranscribeError] = useState<string | null>(null);

  const [segments, setSegments] = useState<CaptionSegment[]>([]);
  const [speakers, setSpeakers] = useState<string[]>([]);
  const [audioDuration, setAudioDuration] = useState(0);

  const [translating, setTranslating] = useState(false);
  const [translateError, setTranslateError] = useState<string | null>(null);
  const [translateMode, setTranslateMode] = useState<"replace" | "bilingual">("replace");
  const [translateTarget, setTranslateTarget] = useState("fa");
  const [flaggedIndices, setFlaggedIndices] = useState<number[]>([]);

  const [burning, setBurning] = useState(false);
  const [burnError, setBurnError] = useState<string | null>(null);
  const [fontSize, setFontSize] = useState(40);
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);
  const [downloadFilename, setDownloadFilename] = useState<string | null>(null);
  const [finalizedPath, setFinalizedPath] = useState<string | null>(null);
  const [previewMode, setPreviewMode] = useState<"original" | "finalized">("original");

  const videoRef = useRef<HTMLVideoElement | null>(null);

  const speakerColors = useMemo(() => getSpeakerColorMap(segments), [segments]);

  const hasResults = segments.length > 0;
  const translateTargetLabel =
    TARGET_LANG_OPTIONS.find((o) => o.value === translateTarget)?.label || "Persian";

  const invalidIndices = useMemo(() => {
    const bad = new Set<number>();
    segments.forEach((seg, i) => {
      if (!seg.text.trim() || !Number.isFinite(seg.start) || !Number.isFinite(seg.end) || seg.start >= seg.end) {
        bad.add(i);
      }
    });
    return bad;
  }, [segments]);

  useEffect(() => {
    if (videoRef.current) {
      videoRef.current.load();
    }
  }, [previewMode, uploadedVideo, finalizedPath]);

  const resetAll = () => {
    setUploadedVideo(null);
    setUploadError(null);
    setTranscribeError(null);
    setSegments([]);
    setSpeakers([]);
    setAudioDuration(0);
    setTranslateError(null);
    setFlaggedIndices([]);
    setBurnError(null);
    setDownloadUrl(null);
    setDownloadFilename(null);
    setFinalizedPath(null);
    setPreviewMode("original");
    setVideoTitle("");
  };

  const uploadVideo = async (file: File) => {
    const ext = file.name.substring(file.name.lastIndexOf(".")).toLowerCase();
    if (!ALLOWED_VIDEO_EXTS.includes(ext)) {
      setUploadError(`Unsupported format "${ext || "(none)"}". Use MP4, MOV, MKV, WEBM, M4V, or AVI.`);
      return;
    }

    resetAll();
    setUploading(true);
    const formData = new FormData();
    formData.append("video", file);

    try {
      const res = await fetch("/api/captions/upload", { method: "POST", body: formData });
      const data = await res.json();
      if (res.ok && data.path) {
        setUploadedVideo(data);
      } else {
        setUploadError(data?.error || `Upload failed (HTTP ${res.status})`);
      }
    } catch (e: any) {
      setUploadError(e.message || String(e));
    } finally {
      setUploading(false);
    }
  };

  const handleDragOver = (e: React.DragEvent) => e.preventDefault();
  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(true);
  };
  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    if (e.currentTarget.contains(e.relatedTarget as Node)) return;
    setDragActive(false);
  };
  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(false);
    if (e.dataTransfer.files.length > 0) await uploadVideo(e.dataTransfer.files[0]);
  };
  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) await uploadVideo(e.target.files[0]);
  };

  const runTranscribe = async () => {
    if (!uploadedVideo) return;
    setTranscribing(true);
    setTranscribeError(null);
    setSegments([]);
    setSpeakers([]);
    setTranslateError(null);
    setFlaggedIndices([]);
    setDownloadUrl(null);
    setDownloadFilename(null);
    setFinalizedPath(null);
    setPreviewMode("original");
    try {
      const res = await fetch("/api/captions/transcribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filePath: uploadedVideo.path,
          lang,
          title: videoTitle || undefined
        })
      });
      const data = await res.json();
      if (res.ok && Array.isArray(data.segments)) {
        setSegments(data.segments);
        setSpeakers(data.speakers || []);
        setAudioDuration(data.audioDuration || 0);
      } else {
        setTranscribeError(data?.error || `HTTP ${res.status}`);
      }
    } catch (e: any) {
      setTranscribeError(e.message || String(e));
    } finally {
      setTranscribing(false);
    }
  };

  const updateSegment = (index: number, patch: Partial<CaptionSegment>) => {
    setSegments((prev) => prev.map((seg, i) => (i === index ? { ...seg, ...patch } : seg)));
  };

  const deleteSegment = (index: number) => {
    setSegments((prev) => prev.filter((_, i) => i !== index));
  };

  const addSegment = () => {
    const last = segments[segments.length - 1];
    const start = last ? last.end : 0;
    setSegments((prev) => [
      ...prev,
      { start, end: start + 2, text: "", speaker: speakers[0] || "Speaker 1" }
    ]);
  };

  const renameSpeaker = (oldLabel: string, newLabel: string) => {
    const trimmed = newLabel.trim();
    if (!trimmed || trimmed === oldLabel) return;
    setSpeakers((prev) => prev.map((sp) => (sp === oldLabel ? trimmed : sp)));
    setSegments((prev) => prev.map((seg) => (seg.speaker === oldLabel ? { ...seg, speaker: trimmed } : seg)));
  };

  const addSpeaker = () => {
    let n = speakers.length + 1;
    while (speakers.includes(`Speaker ${n}`)) n++;
    setSpeakers((prev) => [...prev, `Speaker ${n}`]);
  };

  const seekTo = (time: number) => {
    if (videoRef.current) {
      videoRef.current.currentTime = time;
      videoRef.current.play().catch(() => {});
    }
  };

  const runBurn = async () => {
    if (!uploadedVideo || !segments.length || invalidIndices.size > 0) return;
    setBurning(true);
    setBurnError(null);
    try {
      const res = await fetch("/api/captions/burn", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filePath: uploadedVideo.path,
          segments,
          originalName: uploadedVideo.originalName,
          fontSize
        })
      });
      const data = await res.json();
      if (res.ok && data.status === "success") {
        setDownloadUrl(data.downloadUrl);
        setDownloadFilename(data.filename);
        setFinalizedPath(data.path);
        setPreviewMode("finalized");
      } else {
        setBurnError(data?.error || `HTTP ${res.status}`);
      }
    } catch (e: any) {
      setBurnError(e.message || String(e));
    } finally {
      setBurning(false);
    }
  };

  // Translate the current (possibly hand-edited) segment text via the local
  // Ollama model -- see lib/ollamaTranslate.ts's translateCaptionSegments().
  // Runs on whatever's in `segments` right now, so edits made in the review
  // list above are translated too, not just the raw transcription output.
  // Also clears any edits made to the burned-video preview below, since
  // changing the caption text invalidates whatever was last burned.
  const runTranslate = async () => {
    if (!segments.length || translating) return;
    setTranslating(true);
    setTranslateError(null);
    try {
      const res = await fetch("/api/captions/translate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          segments,
          mode: translateMode,
          targetLanguage: translateTarget,
          videoTitle: videoTitle || undefined
        })
      });
      const data = await res.json();
      if (res.ok && Array.isArray(data.segments)) {
        setSegments(data.segments);
        setFlaggedIndices(Array.isArray(data.flaggedIndices) ? data.flaggedIndices : []);
        // A prior burn no longer reflects the (now-translated) captions.
        setDownloadUrl(null);
        setDownloadFilename(null);
        setFinalizedPath(null);
        setPreviewMode("original");
      } else {
        setTranslateError(data?.error || `HTTP ${res.status}`);
      }
    } catch (e: any) {
      setTranslateError(e.message || String(e));
    } finally {
      setTranslating(false);
    }
  };

  const previewPath = previewMode === "finalized" && finalizedPath ? finalizedPath : uploadedVideo?.path;
  const previewSrc = previewPath ? `/api/captions/stream?path=${encodeURIComponent(previewPath)}` : null;

  return (
    <div className="space-y-6">
      {/* UPLOAD ZONE */}
      {!uploadedVideo && (
        <div
          onDragOver={handleDragOver}
          onDragEnter={handleDragEnter}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          className={`w-full bg-[#0f1117] rounded-xl border-2 border-dashed p-10 text-center transition-all duration-300 shadow-2xl relative overflow-hidden ring-1 ring-white/5 ${
            dragActive ? "border-indigo-400 scale-[1.01]" : "border-slate-800 hover:border-indigo-500/50"
          }`}
        >
          <input
            type="file"
            id="videoInput"
            accept={ALLOWED_VIDEO_EXTS.join(",")}
            onChange={handleFileSelect}
            className="hidden"
          />
          <label htmlFor="videoInput" className="cursor-pointer block relative">
            <div
              className={`w-16 h-16 bg-[#0b0e14] border rounded-xl flex items-center justify-center mx-auto mb-4 transition-all duration-300 text-slate-400 shadow-lg ${
                dragActive ? "scale-110 border-indigo-400/60 text-indigo-300" : "border-slate-800"
              }`}
            >
              {uploading ? (
                <RefreshCw className="w-8 h-8 animate-spin text-indigo-400" />
              ) : (
                <UploadCloud className="w-8 h-8" />
              )}
            </div>
            <h2 className="text-white font-semibold text-lg leading-snug">
              {uploading ? "Uploading video..." : dragActive ? "Drop it right here" : "Drag & Drop Your Video Here"}
            </h2>
            <p className="text-slate-500 text-xs mt-1.5 max-w-sm mx-auto leading-relaxed">
              Supports MP4, MOV, MKV, WEBM, M4V, and AVI. Audio is transcribed and diarized locally; nothing is
              burned in until you review it below.
            </p>
          </label>
          {uploadError && (
            <p className="text-rose-400 text-xs mt-4 flex items-center justify-center gap-1.5">
              <AlertTriangle className="w-3.5 h-3.5" /> {uploadError}
            </p>
          )}
        </div>
      )}

      {uploadedVideo && (
        <>
          {/* FILE SUMMARY */}
          <div className="bg-[#0f1117] rounded-xl border border-slate-800 p-5 shadow-2xl ring-1 ring-white/5 flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-3.5 min-w-0">
              <div className="p-3 bg-indigo-950/30 border border-indigo-800/20 rounded-lg text-indigo-400 shrink-0">
                <FileVideo className="w-6 h-6" />
              </div>
              <div className="min-w-0">
                <h3 className="text-white font-semibold text-base truncate max-w-md">{uploadedVideo.originalName}</h3>
                <p className="text-slate-400 text-xs font-mono">
                  {(uploadedVideo.size / (1024 * 1024)).toFixed(1)} MB
                </p>
              </div>
            </div>
            <button
              onClick={resetAll}
              className="px-3 py-2 bg-[#0b0e14]/80 hover:bg-slate-800 border border-slate-800 text-slate-300 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition-colors cursor-pointer"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              Upload Different Video
            </button>
          </div>

          {/* VIDEO PREVIEW */}
          <div className="bg-[#0f1117] rounded-xl border border-slate-800 p-5 shadow-2xl ring-1 ring-white/5">
            <div className="flex items-center justify-between mb-3">
              <h4 className="text-white font-medium text-sm flex items-center gap-1.5 uppercase tracking-wider text-slate-400">
                <Video className="w-4 h-4 text-indigo-400" />
                Preview
              </h4>
              {finalizedPath && (
                <div className="flex items-center bg-[#0b0e14] rounded-full p-0.5 border border-slate-800">
                  {(["original", "finalized"] as const).map((mode) => (
                    <button
                      key={mode}
                      onClick={() => setPreviewMode(mode)}
                      className={`px-2.5 py-1 rounded-full text-[10px] font-semibold uppercase tracking-wider transition-colors cursor-pointer border-0 ${
                        previewMode === mode
                          ? "bg-gradient-to-r from-indigo-600 to-violet-600 text-white"
                          : "text-slate-500 hover:text-slate-300 bg-transparent"
                      }`}
                    >
                      {mode === "original" ? "Original" : "Captioned"}
                    </button>
                  ))}
                </div>
              )}
            </div>
            {previewSrc && (
              <video ref={videoRef} controls preload="metadata" className="w-full max-h-[420px] rounded-lg bg-black">
                <source src={previewSrc} />
              </video>
            )}
          </div>

          {/* TRANSCRIBE CONTROLS */}
          {!hasResults && (
            <div className="bg-[#0f1117] rounded-xl border border-slate-800 p-6 shadow-2xl ring-1 ring-white/5">
              <h3 className="text-white font-medium text-lg mb-4 flex items-center gap-2 border-b border-slate-800/60 pb-3">
                <Clapperboard className="w-5 h-5 text-indigo-400" />
                Generate Captions
              </h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-5">
                <div>
                  <label className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-1.5 flex items-center gap-1.5">
                    <Languages className="w-3 h-3" />
                    Spoken Language
                  </label>
                  <select
                    value={lang}
                    onChange={(e) => setLang(e.target.value)}
                    className="w-full bg-[#0b0e14] border border-slate-800 rounded-lg py-2.5 px-3 text-white text-sm focus:outline-none focus:border-indigo-500 transition-colors cursor-pointer"
                  >
                    {LANG_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-1.5 block">
                    Video Title (optional vocabulary primer)
                  </label>
                  <input
                    type="text"
                    value={videoTitle}
                    onChange={(e) => setVideoTitle(e.target.value)}
                    placeholder="Helps Whisper recognize names/terms in the video"
                    className="w-full bg-[#0b0e14] border border-slate-800 rounded-lg py-2.5 px-3 text-white text-sm focus:outline-none focus:border-indigo-500 transition-colors"
                  />
                </div>
              </div>

              <p className="text-slate-500 text-xs mb-4 leading-relaxed">
                Extracts the audio track, transcribes it with Faster-Whisper, and identifies speakers with
                pyannote.audio. This can take a few minutes — and installs its own diarization dependencies (plus
                needs a Hugging Face token, see Settings → Video Captions) the first time it runs.
              </p>

              <button
                onClick={runTranscribe}
                disabled={transcribing}
                className="w-full py-3 bg-gradient-to-r from-indigo-600 to-violet-600 hover:from-indigo-500 hover:to-violet-500 disabled:from-slate-800 disabled:to-slate-800 disabled:text-slate-600 text-white rounded-lg text-sm font-semibold transition-all shadow-lg flex items-center justify-center gap-2 cursor-pointer border-0"
              >
                {transcribing ? (
                  <>
                    <RefreshCw className="w-4 h-4 animate-spin" />
                    Transcribing & Identifying Speakers...
                  </>
                ) : (
                  <>
                    <Clapperboard className="w-4 h-4" />
                    Generate Captions
                  </>
                )}
              </button>

              {transcribeError && (
                <div className="mt-4 text-rose-400 text-xs flex items-start gap-1.5 bg-rose-950/20 border border-rose-900/40 rounded-lg p-3">
                  <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                  <span>
                    {transcribeError}
                    {transcribeError.toLowerCase().includes("hugging face") && (
                      <> You can set the token in Settings → Video Captions.</>
                    )}
                  </span>
                </div>
              )}
            </div>
          )}

          {/* REVIEW EDITOR */}
          {hasResults && (
            <>
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {/* SEGMENTS LIST */}
                <div className="lg:col-span-2 bg-[#0f1117] rounded-xl border border-slate-800 p-6 shadow-2xl ring-1 ring-white/5">
                  <div className="flex flex-wrap items-center justify-between gap-3 mb-4 border-b border-slate-800/60 pb-3">
                    <h3 className="text-white font-medium text-lg flex items-center gap-2">
                      <Clapperboard className="w-5 h-5 text-indigo-400" />
                      Caption Review ({segments.length} lines)
                    </h3>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={addSegment}
                        className="px-2.5 py-1.5 bg-[#0b0e14]/80 border border-slate-800 hover:bg-slate-800 text-slate-300 rounded-lg text-xs flex items-center gap-1 transition-colors cursor-pointer"
                      >
                        <Plus className="w-3.5 h-3.5 text-emerald-400" />
                        Add Line
                      </button>
                      <button
                        onClick={runTranscribe}
                        disabled={transcribing}
                        title="Re-run transcription — discards your edits"
                        className="px-2.5 py-1.5 bg-[#0b0e14]/80 border border-slate-800 hover:bg-slate-800 text-slate-300 rounded-lg text-xs flex items-center gap-1 transition-colors cursor-pointer disabled:opacity-50"
                      >
                        {transcribing ? (
                          <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                        ) : (
                          <RotateCcw className="w-3.5 h-3.5" />
                        )}
                        Re-transcribe
                      </button>
                    </div>
                  </div>

                  <div className="space-y-2.5 max-h-[560px] overflow-y-auto custom-scrollbar pr-1">
                    {segments.map((seg, i) => {
                      const color = speakerColors[seg.speaker] || "#94a3b8";
                      const invalid = invalidIndices.has(i);
                      const flagged = !invalid && flaggedIndices.includes(i);
                      return (
                        <div
                          key={i}
                          className={`bg-[#0b0e14]/60 rounded-lg p-3 border transition-colors ${
                            invalid ? "border-rose-800/60" : flagged ? "border-amber-700/50" : "border-slate-800/60"
                          }`}
                          style={{ borderLeft: `3px solid ${color}` }}
                        >
                          <div className="flex flex-wrap items-center gap-2 mb-2">
                            <button
                              onClick={() => seekTo(seg.start)}
                              title="Seek video to this line"
                              className="flex items-center gap-1 text-[11px] font-mono text-slate-400 hover:text-indigo-300 bg-transparent border-0 cursor-pointer px-1"
                            >
                              <Play className="w-3 h-3" />
                              {formatTimestamp(seg.start)}
                            </button>
                            {flagged && (
                              <span title="May not have translated cleanly" className="text-amber-400">
                                <AlertTriangle className="w-3 h-3" />
                              </span>
                            )}
                            <input
                              type="number"
                              step={0.1}
                              min={0}
                              value={seg.start}
                              onChange={(e) => updateSegment(i, { start: Number(e.target.value) })}
                              className="w-16 bg-[#0f1117] border border-slate-800 rounded px-1.5 py-0.5 text-[11px] font-mono text-slate-300 focus:outline-none focus:border-indigo-500"
                            />
                            <span className="text-slate-600 text-xs">→</span>
                            <input
                              type="number"
                              step={0.1}
                              min={0}
                              value={seg.end}
                              onChange={(e) => updateSegment(i, { end: Number(e.target.value) })}
                              className="w-16 bg-[#0f1117] border border-slate-800 rounded px-1.5 py-0.5 text-[11px] font-mono text-slate-300 focus:outline-none focus:border-indigo-500"
                            />
                            <span className="text-[11px] font-mono text-slate-600">{formatTimestamp(seg.end)}</span>

                            <select
                              value={seg.speaker}
                              onChange={(e) => updateSegment(i, { speaker: e.target.value })}
                              className="ml-auto bg-[#0f1117] border border-slate-800 rounded px-2 py-1 text-[11px] font-semibold focus:outline-none focus:border-indigo-500 cursor-pointer"
                              style={{ color }}
                            >
                              {speakers.map((sp) => (
                                <option key={sp} value={sp} style={{ color: speakerColors[sp] || "#94a3b8" }}>
                                  {sp}
                                </option>
                              ))}
                            </select>

                            <button
                              onClick={() => deleteSegment(i)}
                              title="Delete this line"
                              className="text-slate-500 hover:text-rose-400 bg-transparent border-0 cursor-pointer p-1"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                          <input
                            type="text"
                            dir="auto"
                            value={seg.text}
                            onChange={(e) => updateSegment(i, { text: e.target.value })}
                            placeholder="(empty line — will be skipped or should be removed)"
                            className="w-full bg-transparent border-0 text-white text-sm focus:outline-none placeholder:text-slate-600"
                          />
                        </div>
                      );
                    })}
                  </div>

                  {invalidIndices.size > 0 && (
                    <p className="text-amber-400 text-xs mt-3 flex items-center gap-1.5">
                      <AlertTriangle className="w-3.5 h-3.5" />
                      {invalidIndices.size} line{invalidIndices.size > 1 ? "s need" : " needs"} attention (empty text,
                      or end time before start time) before burning.
                    </p>
                  )}
                </div>

                {/* SPEAKERS + BURN SIDEBAR */}
                <div className="space-y-6">
                  <div className="bg-[#0f1117] rounded-xl border border-slate-800 p-6 shadow-2xl ring-1 ring-white/5">
                    <h4 className="text-white font-semibold text-sm uppercase tracking-wider mb-4 flex items-center gap-1.5">
                      <Users className="w-4 h-4 text-indigo-400" />
                      Speakers
                    </h4>
                    <div className="space-y-2">
                      {speakers.map((sp) => (
                        <div key={sp} className="flex items-center gap-2 bg-[#0b0e14]/60 border border-slate-800/60 rounded-lg px-2.5 py-1.5">
                          <span
                            className="w-2.5 h-2.5 rounded-full shrink-0"
                            style={{ backgroundColor: speakerColors[sp] || "#94a3b8" }}
                          />
                          <input
                            type="text"
                            defaultValue={sp}
                            onBlur={(e) => renameSpeaker(sp, e.target.value)}
                            className="flex-1 min-w-0 bg-transparent border-0 text-slate-200 text-xs focus:outline-none"
                          />
                          <Pencil className="w-3 h-3 text-slate-600 shrink-0" />
                        </div>
                      ))}
                    </div>
                    <button
                      onClick={addSpeaker}
                      className="w-full mt-3 py-2 bg-[#0b0e14]/80 hover:bg-slate-800 border border-slate-800 text-slate-300 rounded-lg text-xs font-semibold flex items-center justify-center gap-1.5 transition-colors cursor-pointer"
                    >
                      <Plus className="w-3.5 h-3.5" />
                      Add Speaker
                    </button>
                  </div>

                  <div className="bg-[#0f1117] rounded-xl border border-slate-800 p-6 shadow-2xl relative overflow-hidden ring-1 ring-white/5">
                    <div className="absolute top-0 right-0 w-24 h-24 bg-indigo-500/5 rounded-full blur-2xl pointer-events-none" />
                    <h4 className="text-white font-semibold text-sm uppercase tracking-wider mb-4 flex items-center gap-1.5">
                      <Sparkles className="w-4 h-4 text-indigo-400" />
                      Local LLM Translator
                    </h4>
                    <p className="text-slate-400 text-xs mb-4">
                      Runs on your GPU via <span className="text-indigo-400 font-semibold font-mono">Ollama</span> — no
                      cloud, no API key, no rate limits. Translates each caption line and keeps every timestamp
                      exactly where it was. Requires Ollama running locally (<code className="font-mono">ollama serve</code>).
                    </p>

                    <div className="mb-4">
                      <label className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-1.5 block">
                        Translate To
                      </label>
                      <select
                        value={translateTarget}
                        onChange={(e) => setTranslateTarget(e.target.value)}
                        className="w-full bg-[#0b0e14] border border-slate-800 rounded-lg py-2 px-3 text-white text-xs focus:outline-none focus:border-indigo-500 transition-colors cursor-pointer"
                      >
                        {TARGET_LANG_OPTIONS.map((o) => (
                          <option key={o.value} value={o.value}>
                            {o.label}
                          </option>
                        ))}
                      </select>
                    </div>

                    <div className="mb-4">
                      <label className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-1.5 block">
                        Translation Mode
                      </label>
                      <select
                        value={translateMode}
                        onChange={(e) => setTranslateMode(e.target.value as "replace" | "bilingual")}
                        className="w-full bg-[#0b0e14] border border-slate-800 rounded-lg py-2 px-3 text-white text-xs focus:outline-none focus:border-indigo-500 transition-colors cursor-pointer"
                      >
                        <option value="replace">Replace with {translateTargetLabel} Translation</option>
                        <option value="bilingual">Bilingual （Original + {translateTargetLabel}）</option>
                      </select>
                    </div>

                    <button
                      onClick={runTranslate}
                      disabled={translating || !segments.length}
                      className="w-full py-2.5 bg-[#0b0e14]/80 hover:bg-slate-800 border border-indigo-500/40 text-indigo-300 rounded-lg text-xs font-semibold flex items-center justify-center gap-1.5 transition-colors cursor-pointer disabled:opacity-50"
                    >
                      {translating ? (
                        <>
                          <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                          Translating to {translateTargetLabel}...
                        </>
                      ) : (
                        <>
                          <Languages className="w-3.5 h-3.5" />
                          Translate Captions
                        </>
                      )}
                    </button>

                    {flaggedIndices.length > 0 && (
                      <p className="text-amber-400 text-xs mt-3 flex items-start gap-1.5">
                        <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                        {flaggedIndices.length} line{flaggedIndices.length > 1 ? "s" : ""} may not have translated
                        cleanly — worth a quick look (marked below).
                      </p>
                    )}
                    {translateError && (
                      <p className="text-rose-400 text-xs mt-3 flex items-start gap-1.5">
                        <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" /> {translateError}
                      </p>
                    )}
                  </div>

                  <div className="bg-[#0f1117] rounded-xl border border-slate-800 p-6 shadow-2xl ring-1 ring-white/5">
                    <h4 className="text-white font-semibold text-sm uppercase tracking-wider mb-4 flex items-center gap-1.5">
                      <Clapperboard className="w-4 h-4 text-emerald-400" />
                      Burn Captions
                    </h4>
                    <p className="text-slate-400 text-xs mb-4 leading-relaxed">
                      Renders the lines above as color-coded, per-speaker ASS subtitles and burns them into the
                      video with ffmpeg — RTL scripts like Persian are shaped correctly.
                    </p>

                    <div className="mb-4">
                      <label className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-1.5 flex items-center justify-between">
                        <span>Caption Size</span>
                        <span className="text-slate-500 font-mono normal-case">{fontSize}px</span>
                      </label>
                      <input
                        type="range"
                        min={20}
                        max={64}
                        step={2}
                        value={fontSize}
                        onChange={(e) => setFontSize(Number(e.target.value))}
                        className="w-full accent-indigo-500 cursor-pointer"
                      />
                    </div>

                    {downloadUrl ? (
                      <div className="space-y-2">
                        <a
                          href={downloadUrl}
                          download={downloadFilename || "captioned_video"}
                          className="w-full py-3 bg-gradient-to-r from-emerald-500 to-teal-600 hover:from-emerald-400 hover:to-teal-500 text-white rounded-lg text-sm font-semibold transition-all shadow-lg flex items-center justify-center gap-2 cursor-pointer border-0"
                        >
                          <ArrowDownToLine className="w-4 h-4" />
                          Download Captioned Video
                        </a>
                        <button
                          onClick={runBurn}
                          disabled={burning || invalidIndices.size > 0}
                          title="Burn again with the current Caption Size (or any other edits above)"
                          className="w-full py-2 bg-[#0b0e14]/80 hover:bg-slate-800 border border-slate-800 text-slate-300 rounded-lg text-xs font-semibold flex items-center justify-center gap-1.5 transition-colors cursor-pointer disabled:opacity-50"
                        >
                          {burning ? (
                            <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                          ) : (
                            <RotateCcw className="w-3.5 h-3.5" />
                          )}
                          Re-burn
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={runBurn}
                        disabled={burning || invalidIndices.size > 0}
                        className="w-full py-3 bg-gradient-to-r from-indigo-600 to-violet-600 hover:from-indigo-500 hover:to-violet-500 disabled:from-slate-800 disabled:to-slate-800 disabled:text-slate-600 text-white rounded-lg text-sm font-semibold transition-all shadow-lg flex items-center justify-center gap-2 cursor-pointer border-0"
                      >
                        {burning ? (
                          <>
                            <RefreshCw className="w-4 h-4 animate-spin" />
                            Burning Subtitles...
                          </>
                        ) : (
                          <>
                            <Clapperboard className="w-4 h-4" />
                            Burn Captions
                          </>
                        )}
                      </button>
                    )}

                    {downloadUrl && (
                      <p className="text-emerald-400 text-xs mt-3 flex items-center gap-1.5">
                        <CheckCircle2 className="w-3.5 h-3.5" />
                        Ready — preview above has switched to "Captioned".
                      </p>
                    )}
                    {burnError && (
                      <p className="text-rose-400 text-xs mt-3 flex items-center gap-1.5">
                        <AlertTriangle className="w-3.5 h-3.5" /> {burnError}
                      </p>
                    )}
                  </div>
                </div>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
