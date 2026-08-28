import React, { useState, useEffect } from "react";
import { 
  UploadCloud, 
  Settings, 
  Activity, 
  FileAudio, 
  CheckCircle, 
  ChevronRight, 
  ArrowDownToLine, 
  Tag, 
  AlignLeft, 
  Volume2, 
  AlertCircle, 
  Sparkles,
  RefreshCw,
  FileCheck,
  Library,
  Archive,
  History,
  Subtitles
} from "lucide-react";
import { TrackMetadata, AnalysisResult } from "./types";
import SpectrogramHeatmap from "./components/SpectrogramHeatmap";
import MetadataPanel from "./components/MetadataPanel";
import LyricsEditor from "./components/LyricsEditor";
import BootSequence from "./components/BootSequence";
import AmbientBackground from "./components/AmbientBackground";
import MonsterCostume from "./components/MonsterCostume";
import Mascot from "./components/Mascot";
import AudioPlayer from "./components/AudioPlayer";
import ThemePicker from "./components/ThemePicker";
import LibraryTab from "./components/LibraryTab";
import SettingsPanel from "./components/SettingsPanel";
import ActivityLog from "./components/ActivityLog";
import VideoCaptionsTab from "./components/VideoCaptionsTab";
import { motion, AnimatePresence } from "motion/react";

export default function App() {
  const [booting, setBooting] = useState(true);
  const [currentTime, setCurrentTime] = useState("");
  const [screen, setScreen] = useState<"archiver" | "library" | "captions" | "settings">("archiver");
  const [activityOpen, setActivityOpen] = useState(false);
  const [uploadedFile, setUploadedFile] = useState<any>(null);
  const [uploading, setUploading] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [compiling, setCompiling] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  
  const [activeTab, setActiveTab] = useState<"spectral" | "tags" | "lyrics">("spectral");
  const [analysisResult, setAnalysisResult] = useState<AnalysisResult | null>(null);
  const [metadata, setMetadata] = useState<TrackMetadata | null>(null);
  const [lyricsText, setLyricsText] = useState("");
  
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);
  const [downloadFilename, setDownloadFilename] = useState<string | null>(null);
  const [libraryPath, setLibraryPath] = useState<string | null>(null);

  // Now-playing bar: which copy is being previewed, and the finalized
  // copy's absolute (streamable-via-/api/stream) path once one exists.
  const [playerSource, setPlayerSource] = useState<"original" | "finalized">("original");
  const [finalizedPath, setFinalizedPath] = useState<string | null>(null);

  // Update clock every second
  useEffect(() => {
    const updateTime = () => {
      const now = new Date();
      setCurrentTime(now.toISOString().replace("T", " ").substring(0, 19) + " UTC");
    };
    updateTime();
    const interval = setInterval(updateTime, 1000);
    return () => clearInterval(interval);
  }, []);

  // Handle drag-over events
  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
  };

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    if (e.currentTarget.contains(e.relatedTarget as Node)) return;
    setDragActive(false);
  };

  // Handle drop file event
  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(false);
    const files = e.dataTransfer.files;
    if (files.length > 0) {
      await uploadFile(files[0]);
    }
  };

  // Handle manual input file selection
  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (files && files.length > 0) {
      await uploadFile(files[0]);
    }
  };

  // Upload file via API
  const uploadFile = async (file: File) => {
    // Validate file extensions
    const ext = file.name.substring(file.name.lastIndexOf(".")).toLowerCase();
    const allowed = [".mp3", ".flac", ".m4a", ".aac", ".wav"];
    if (!allowed.includes(ext)) {
      alert("Unsupported format. Please upload MP3, FLAC, M4A, AAC, or WAV files.");
      return;
    }

    setUploading(true);
    setAnalysisResult(null);
    setMetadata(null);
    setLyricsText("");
    setDownloadUrl(null);
    setLibraryPath(null);
    setFinalizedPath(null);
    setPlayerSource("original");

    const formData = new FormData();
    formData.append("audio", file);

    try {
      const res = await fetch("/api/upload", {
        method: "POST",
        body: formData
      });
      let data;
      const text = await res.text();
      try {
        data = JSON.parse(text);
      } catch (err) {
        throw new Error(`Server returned HTTP ${res.status}: ${text.slice(0, 100)}... (Likely a file size limit error if the file is large)`);
      }
      
      if (res.ok && data.path) {
        setUploadedFile(data);
        await analyzeFile(data.path);
      } else {
        alert(`Upload failed: ${data?.error || `HTTP ${res.status}`}`);
      }
    } catch (e: any) {
      console.error("Upload failed", e);
      alert(`Upload failed: ${e.message || e}`);
    } finally {
      setUploading(false);
    }
  };

  // Analyze uploaded audio
  const analyzeFile = async (filePath: string) => {
    setAnalyzing(true);
    try {
      const res = await fetch("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filePath })
      });
      let data;
      const text = await res.text();
      try {
        data = JSON.parse(text);
      } catch (err) {
        throw new Error(`Analysis failed (HTTP ${res.status}): Server returned invalid JSON. ${text.slice(0, 100)}`);
      }
      
      // The server can legitimately fail here (corrupt file, decode error, etc.)
      // and returns { error } with a non-2xx status — without this check that
      // error object gets treated as an AnalysisResult, and the "tags"/"spectral"
      // tabs crash rendering (e.g. analysisResult.metadata.has_lyrics on
      // undefined), taking down the whole React tree to a blank screen.
      if (!res.ok) {
        throw new Error(data?.error || `Analysis failed (HTTP ${res.status})`);
      }
      setAnalysisResult(data as AnalysisResult);
      setMetadata(data.metadata);
      // Retrieve empty lyrics placeholder
      setLyricsText("");
    } catch (e: any) {
      console.error("Analysis failed", e);
      alert(`Analysis failed: ${e.message || e}`);
    } finally {
      setAnalyzing(false);
    }
  };

  const handleApplyPreset = (preset: any) => {
    if (!metadata) return;
    setMetadata({
      ...metadata,
      title: preset.title || metadata.title,
      artist: preset.artist || metadata.artist,
      album_artist: preset.album_artist || metadata.album_artist,
      album: preset.album || metadata.album,
      year: preset.year || metadata.year,
      track: preset.track || metadata.track,
      disc: preset.disc || metadata.disc,
      genre: preset.genre || metadata.genre,
      composer: preset.composer || metadata.composer,
      isrc: preset.isrc || metadata.isrc,
      cover: preset.cover || metadata.cover
    });
  };

  // Tag override and save
  const handleCompile = async () => {
    if (!uploadedFile || !metadata) return;
    setCompiling(true);
    try {
      const res = await fetch("/api/apply-tags", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filePath: uploadedFile.path,
          metadata,
          lyricsText,
          coverUrl: metadata.cover
        })
      });
      let data;
      const text = await res.text();
      try {
        data = JSON.parse(text);
      } catch (err) {
        throw new Error(`Export failed (HTTP ${res.status}): Server returned invalid JSON. ${text.slice(0, 100)}`);
      }
      
      if (res.ok && data.status === "success") {
        setDownloadUrl(data.downloadUrl);
        setDownloadFilename(data.filename);
        setLibraryPath(data.libraryPath || null);
        setFinalizedPath(data.path || null);
      } else {
        alert(`Compile failed: ${data?.error || `HTTP ${res.status}`}`);
      }
    } catch (e: any) {
      console.error(e);
      alert(`Compile failed: ${e.message || e}`);
    } finally {
      setCompiling(false);
    }
  };

  // Invalidate a compiled download whenever metadata or lyrics change after
  // compiling — otherwise the download link keeps serving the stale file and
  // there is no way to recompile until the user re-uploads the track.
  useEffect(() => {
    setDownloadUrl(null);
    setDownloadFilename(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [metadata, lyricsText]);

  // Streamable URL for whichever copy the now-playing bar is set to preview.
  // /api/stream (not /api/download) so it plays inline instead of forcing a
  // Save-As, and only switches to the finalized copy once one actually
  // exists — falls back to the original upload otherwise.
  const playerPath =
    playerSource === "finalized" && finalizedPath ? finalizedPath : uploadedFile?.path;
  const playerSrc = playerPath ? `/api/stream?path=${encodeURIComponent(playerPath)}` : null;

  return (
    <div
      className={`min-h-screen font-sans text-slate-300 relative overflow-x-hidden selection:bg-deezer-500/25 selection:text-deezer-200 ${
        uploadedFile ? "pb-20" : ""
      }`}
    >

      <AmbientBackground />
      <MonsterCostume />
      <div className="grain-overlay" />

      <AnimatePresence>
        {booting && <BootSequence onFinished={() => setBooting(false)} />}
      </AnimatePresence>

      {/* HEADER BAR */}
      <header className="border-b border-void-700/80 bg-void-900/70 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            {booting ? (
              <div className="w-9 h-9 shrink-0" />
            ) : (
              <Mascot layoutId="mascot" size={34} spin={false} reactive={false} />
            )}
            <div>
              <h1 className="text-white font-display font-bold text-base tracking-tight">Monster Archiver</h1>
              <p className="text-[10px] text-slate-500 font-mono tracking-wider uppercase font-semibold">Nexus Web Suite v18.4</p>
            </div>
          </div>

          <div className="flex items-center gap-4">
            {/* SCREEN NAV — Archiver workspace vs. Library maintenance tools */}
            {!booting && (
              <div className="hidden md:flex items-center gap-1 bg-void-950/60 border border-void-700/70 rounded-lg p-1">
                {[
                  { id: "archiver", label: "Archiver", icon: Archive },
                  { id: "library", label: "Library", icon: Library },
                  { id: "captions", label: "Captions", icon: Subtitles },
                  { id: "settings", label: "Settings", icon: Settings },
                ].map((s) => {
                  const Icon = s.icon;
                  const active = screen === s.id;
                  return (
                    <button
                      key={s.id}
                      onClick={() => setScreen(s.id as any)}
                      className={`px-3 py-1.5 rounded-md text-xs font-semibold flex items-center gap-1.5 transition-colors cursor-pointer border-0 ${
                        active
                          ? "bg-gradient-deezer text-white shadow-sm"
                          : "bg-transparent text-slate-500 hover:text-slate-300"
                      }`}
                    >
                      <Icon className="w-3.5 h-3.5" />
                      {s.label}
                    </button>
                  );
                })}
              </div>
            )}

            {/* UTC Real-Time Clock */}
            <div className="hidden sm:flex items-center gap-2">
              <span className="relative flex h-1.5 w-1.5">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-flow-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-flow-400" />
              </span>
              <div className="flex flex-col items-end">
                <span className="text-[10px] text-slate-500 font-semibold uppercase tracking-wider font-mono">System Time</span>
                <span className="text-slate-300 text-xs font-mono font-medium">{currentTime || "Loading..."}</span>
              </div>
            </div>

            {!booting && (
              <button
                onClick={() => setActivityOpen(true)}
                title="Recent Activity"
                className="w-9 h-9 flex items-center justify-center rounded-lg border border-void-700 text-slate-400 hover:text-deezer-300 hover:border-deezer-500/40 transition-colors cursor-pointer"
              >
                <History className="w-4 h-4" />
              </button>
            )}

            {!booting && <ThemePicker />}
          </div>
        </div>
        <div className="seam-line absolute left-0 right-0 bottom-0" />
      </header>

      <ActivityLog open={activityOpen} onClose={() => setActivityOpen(false)} />

      {/* MOBILE SCREEN NAV — desktop toggle above is hidden below md */}
      {!booting && (
        <div className="md:hidden border-b border-void-700/60 bg-void-900/50 px-4 py-2 flex items-center gap-1">
          {[
            { id: "archiver", label: "Archiver", icon: Archive },
            { id: "library", label: "Library", icon: Library },
            { id: "captions", label: "Captions", icon: Subtitles },
            { id: "settings", label: "Settings", icon: Settings },
          ].map((s) => {
            const Icon = s.icon;
            const active = screen === s.id;
            return (
              <button
                key={s.id}
                onClick={() => setScreen(s.id as any)}
                className={`flex-1 px-3 py-1.5 rounded-md text-xs font-semibold flex items-center justify-center gap-1.5 transition-colors cursor-pointer border-0 ${
                  active ? "bg-gradient-deezer text-white" : "bg-transparent text-slate-500"
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                {s.label}
              </button>
            );
          })}
        </div>
      )}

      {/* MAIN CONTAINER */}
      <main className="max-w-7xl mx-auto px-6 py-8">
        {screen === "library" ? (
          <LibraryTab />
        ) : screen === "captions" ? (
          <VideoCaptionsTab />
        ) : screen === "settings" ? (
          <SettingsPanel />
        ) : (
        <>
        {/* DRAG AND DROP ZONE */}
        <div
          onDragOver={handleDragOver}
          onDragEnter={handleDragEnter}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          className={`w-full bg-void-900 rounded-xl border-2 border-dashed p-10 text-center transition-all duration-300 shadow-2xl relative overflow-hidden group mb-8 ring-1 ring-white/5 ${
            dragActive
              ? "border-deezer-400 scale-[1.01] shadow-[0_0_60px_-8px_rgba(var(--glow-rgb),0.55)]"
              : "border-void-600 hover:border-deezer-500/50"
          }`}
        >
          <div className={`absolute top-0 right-0 w-56 h-56 rounded-full blur-3xl pointer-events-none transition-colors duration-500 ${dragActive ? "bg-deezer-500/20" : "bg-deezer-500/5 group-hover:bg-deezer-500/10"}`} />
          <div className={`absolute bottom-0 left-0 w-56 h-56 rounded-full blur-3xl pointer-events-none transition-colors duration-500 ${dragActive ? "bg-flow-500/15" : "bg-flow-500/0 group-hover:bg-flow-500/5"}`} />

          <input 
            type="file" 
            id="fileInput" 
            accept=".mp3,.flac,.m4a,.aac,.wav" 
            onChange={handleFileSelect} 
            className="hidden" 
          />

          <label htmlFor="fileInput" className="cursor-pointer block relative">
            <div className={`w-16 h-16 bg-void-950 border rounded-xl flex items-center justify-center mx-auto mb-4 transition-all duration-300 text-slate-400 shadow-lg ${
              dragActive
                ? "scale-110 border-deezer-400/60 text-deezer-300 animate-glow-pulse"
                : "group-hover:scale-105 group-hover:text-deezer-400 group-hover:border-deezer-500/30 border-void-600"
            }`}>
              {uploading ? (
                <RefreshCw className="w-8 h-8 animate-spin text-deezer-400" />
              ) : (
                <UploadCloud className={`w-8 h-8 transition-transform duration-300 ${dragActive ? "animate-float-y" : ""}`} />
              )}
            </div>
            
            <h2 className="text-white font-semibold text-lg leading-snug">
              {uploading ? "Uploading Audio Track..." : dragActive ? "Drop it right here" : "Drag & Drop Audio File Here"}
            </h2>
            <p className="text-slate-500 text-xs mt-1.5 max-w-sm mx-auto leading-relaxed">
              Supports FLAC, MP3, M4A, AAC, and WAV audio containers. Maximum upload size up to 100MB.
            </p>
          </label>
        </div>

        {/* WORKSPACE AREA (Triggered when uploaded) */}
        <AnimatePresence mode="wait">
          {uploadedFile && (
            <motion.div
              key="workspace"
              initial={{ opacity: 0, y: 15 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -15 }}
              transition={{ duration: 0.3 }}
              className="space-y-8"
            >
              {/* CURRENT FILE HEADER SUMMARY */}
              <div className="bg-void-900 rounded-xl border border-void-700 p-5 shadow-xl flex flex-wrap items-center justify-between gap-4 ring-1 ring-white/5">
                <div className="flex items-center gap-3.5">
                  <div className="p-3 bg-deezer-950/30 border border-deezer-800/20 rounded-lg text-deezer-400">
                    <FileAudio className="w-6 h-6" />
                  </div>
                  <div>
                    <h3 className="text-white font-semibold text-base truncate max-w-md">{uploadedFile.originalName}</h3>
                    <p className="text-slate-400 text-xs font-mono">Size: {(uploadedFile.size / (1024 * 1024)).toFixed(1)} MB • Status: <span className="text-emerald-400 font-medium font-sans">Uploaded</span></p>
                    {libraryPath && (
                      <p className="text-slate-500 text-[11px] font-mono truncate max-w-md mt-0.5">Saved to: {libraryPath}</p>
                    )}
                  </div>
                </div>

                {/* Compile Actions */}
                <div className="flex items-center gap-3">
                  {downloadUrl ? (
                    <motion.a 
                      href={downloadUrl}
                      download={downloadFilename || "compiled_track"}
                      whileHover={{ scale: 1.03 }}
                      whileTap={{ scale: 0.97 }}
                      className="px-5 py-2.5 bg-gradient-to-r from-emerald-500 to-teal-600 hover:from-emerald-400 hover:to-teal-500 text-white rounded-lg text-sm font-semibold transition-all shadow-lg shadow-emerald-500/10 flex items-center gap-2 cursor-pointer border-0"
                    >
                      <ArrowDownToLine className="w-4 h-4" />
                      Download finalized audio
                    </motion.a>
                  ) : (
                    <motion.button 
                      onClick={handleCompile}
                      disabled={compiling || !metadata}
                      whileHover={compiling || !metadata ? undefined : { scale: 1.03 }}
                      whileTap={compiling || !metadata ? undefined : { scale: 0.97 }}
                      className="px-5 py-2.5 bg-gradient-to-r from-deezer-600 to-flow-500 hover:from-deezer-500 hover:to-flow-400 disabled:from-void-700 disabled:to-void-700 disabled:text-slate-600 text-white rounded-lg text-sm font-semibold transition-all shadow-lg shadow-deezer-500/10 flex items-center gap-2 cursor-pointer border-0"
                    >
                      {compiling ? (
                        <>
                          <RefreshCw className="w-4 h-4 animate-spin" />
                          Tagging & Compiling...
                        </>
                      ) : (
                        <>
                          <FileCheck className="w-4 h-4" />
                          Compile & Download
                        </>
                      )}
                    </motion.button>
                  )}
                </div>
              </div>

              {/* TABS SELECTOR */}
              <div className="flex border-b border-void-700 gap-1.5 overflow-x-auto pb-px">
                {[
                  { id: "spectral", label: "Lossless Scan", icon: Activity },
                  { id: "tags", label: "Metadata Tags", icon: Tag },
                  { id: "lyrics", label: "AI Lyrics Studio", icon: AlignLeft }
                ].map((t) => {
                  const Icon = t.icon;
                  const active = activeTab === t.id;
                  return (
                    <button
                      key={t.id}
                      onClick={() => setActiveTab(t.id as any)}
                      className={`relative px-5 py-3 flex items-center gap-2 text-sm font-medium transition-colors cursor-pointer bg-transparent outline-none ${
                        active 
                          ? "text-white font-semibold" 
                          : "text-slate-500 hover:text-slate-300"
                      }`}
                    >
                      <Icon className={`w-4 h-4 transition-colors ${active ? "text-deezer-400" : "text-slate-500"}`} />
                      {t.label}
                      {active && (
                        <motion.div
                          layoutId="tab-underline"
                          transition={{ type: "spring", stiffness: 380, damping: 32 }}
                          className="absolute left-0 right-0 -bottom-px h-0.5 bg-gradient-deezer rounded-full"
                        />
                      )}
                    </button>
                  );
                })}
              </div>

              {/* ACTIVE TAB CONTENT */}
              <div className="min-h-[400px]">
                {analyzing ? (
                  <div className="flex flex-col items-center justify-center py-20 bg-void-900 rounded-xl border border-void-700 ring-1 ring-white/5 shadow-2xl">
                    <div className="relative mb-4">
                      <div className="absolute inset-0 rounded-full bg-deezer-500/30 blur-xl animate-breathe" />
                      <RefreshCw className="w-10 h-10 text-deezer-400 animate-spin relative" />
                    </div>
                    <p className="text-slate-400 text-sm font-medium animate-pulse">Running advanced Python spectrogram diagnostic & BPM track analyzer...</p>
                  </div>
                ) : (
                  <>
                    {activeTab === "spectral" && analysisResult && (
                      <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        className="space-y-6"
                      >
                        <SpectrogramHeatmap 
                          spectrogramFull={analysisResult.spectrogramFull}
                          suspect={analysisResult.spectral.suspect}
                          isLossless={analysisResult.spectral.is_lossless}
                          maxActiveFreq={analysisResult.spectral.max_active_freq_hz}
                          trackTitle={uploadedFile?.originalName}
                        />

                        {/* Extra analysis items */}
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                          
                          {/* File specs */}
                          <div className="bg-[#0f1117] rounded-xl border border-slate-800 p-5 shadow-2xl ring-1 ring-white/5">
                            <h4 className="text-white font-medium text-sm mb-3.5 flex items-center gap-1.5 uppercase tracking-wider text-slate-400">
                              <Volume2 className="w-4 h-4 text-indigo-400" />
                              Audio Features Output
                            </h4>
                            <div className="grid grid-cols-2 gap-4">
                              <div className="bg-[#0b0e14]/60 rounded-lg p-3 border border-slate-800/60">
                                <span className="text-[10px] text-slate-500 font-semibold block uppercase tracking-wider">Tempo</span>
                                <span className="text-white font-bold text-lg font-mono">{analysisResult.bpm || "Calculating..."} BPM</span>
                              </div>
                              <div className="bg-[#0b0e14]/60 rounded-lg p-3 border border-slate-800/60">
                                <span className="text-[10px] text-slate-500 font-semibold block uppercase tracking-wider">Musical Key</span>
                                <span className="text-white font-bold text-lg font-mono">{analysisResult.key || "Calculating..."}</span>
                              </div>
                            </div>
                          </div>

                          {/* Quick checklist */}
                          <div className="bg-[#0f1117] rounded-xl border border-slate-800 p-5 shadow-2xl ring-1 ring-white/5 flex flex-col justify-between">
                            <h4 className="text-white font-medium text-sm mb-3 flex items-center gap-1.5 uppercase tracking-wider text-slate-400">
                              <CheckCircle className="w-4 h-4 text-emerald-400" />
                              Pre-Check Checklist
                            </h4>
                            <div className="space-y-2 text-xs">
                              <div className="flex items-center justify-between p-2.5 bg-[#0b0e14]/40 rounded-lg border border-slate-800/60">
                                <span className="text-slate-400">Embedded Lyrics Tag</span>
                                <span className={`font-semibold ${analysisResult.metadata.has_lyrics ? "text-emerald-400" : "text-amber-400"}`}>
                                  {analysisResult.metadata.has_lyrics ? "Wiped & Synced" : "None detected"}
                                </span>
                              </div>
                              <div className="flex items-center justify-between p-2.5 bg-[#0b0e14]/40 rounded-lg border border-slate-800/60">
                                <span className="text-slate-400">Embedded Cover Art</span>
                                <span className={`font-semibold ${analysisResult.metadata.has_cover ? "text-emerald-400" : "text-amber-400"}`}>
                                  {analysisResult.metadata.has_cover ? "Wiped & Replaced" : "None detected"}
                                </span>
                              </div>
                            </div>
                          </div>

                        </div>
                      </motion.div>
                    )}

                    {activeTab === "tags" && metadata && (
                      <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                      >
                        <MetadataPanel 
                          metadata={metadata}
                          onChange={(updated) => setMetadata(updated)}
                          onApplyPreset={handleApplyPreset}
                        />
                      </motion.div>
                    )}

                    {activeTab === "lyrics" && metadata && (
                      <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                      >
                        <LyricsEditor
                          lyricsText={lyricsText}
                          metadata={metadata}
                          filePath={uploadedFile?.path}
                          onChange={(text) => setLyricsText(text)}
                        />
                      </motion.div>
                    )}
                  </>
                )}
              </div>
            </motion.div>
          )}
          
          {!uploadedFile && !uploading && (
            <motion.div
              key="welcome"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="flex flex-col items-center justify-center py-20 text-center"
            >
              <div className="relative p-4 bg-void-900 border border-void-700 rounded-xl mb-4 ring-1 ring-white/5 animate-float-y stitched-frame">
                <Mascot size={64} spin blink />
              </div>
              <h3 className="text-white font-semibold text-lg">No active track in workspace</h3>
              <p className="text-slate-500 text-sm max-w-sm mt-1.5 leading-relaxed">
                Drag and drop your audio files above to initialize the tagging, spectrogram analyzer, and lyric compiler dashboard.
              </p>
            </motion.div>
          )}
        </AnimatePresence>
        </>
        )}

      </main>

      {/* FOOTER BAR */}
      <footer className="border-t border-void-700/80 py-6 mt-16 bg-void-900/40 relative">
        <div className="max-w-7xl mx-auto px-6 text-center text-xs text-slate-600 font-mono">
          Monster Archiver Suite • Built with React & Node full-stack microservices • Licensed under Apache 2.0
        </div>
      </footer>

      {uploadedFile && playerSrc && (
        <AudioPlayer
          src={playerSrc}
          title={metadata?.title || uploadedFile.originalName}
          artist={metadata?.artist}
          coverUrl={metadata?.cover}
          sourceMode={playerSource}
          hasFinalized={!!finalizedPath}
          onSourceModeChange={setPlayerSource}
        />
      )}

    </div>
  );
}
