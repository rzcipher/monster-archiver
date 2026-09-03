import React, { useState, useEffect } from "react";
import {  
  UploadCloud,
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
  History,
  AudioLines,
  Database,
  MessageSquareText,
  SlidersHorizontal
, ArrowLeft } from "lucide-react";
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
import LocalFolderBrowser from "./components/LocalFolderBrowser";
import ActivityLog from "./components/ActivityLog";
import VideoCaptionsTab from "./components/VideoCaptionsTab";
import ToastHost, { toast } from "./Toasts";
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
      toast("Unsupported format. Please upload MP3, FLAC, M4A, AAC, or WAV files.");
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
        toast(`Upload failed: ${data?.error || `HTTP ${res.status}`}`);
      }
    } catch (e: any) {
      console.error("Upload failed", e);
      toast(`Upload failed: ${e.message || e}`);
    } finally {
      setUploading(false);
    }
  };

  
    
  const closeTrack = () => {
    setUploadedFile(null);
    setAnalysisResult(null);
    setMetadata(null);
    setLyricsText("");
    setDownloadUrl(null);
    setLibraryPath(null);
    setFinalizedPath(null);
    setPlayerSource("original");
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
      toast(`Analysis failed: ${e.message || e}`);
    } finally {
      setAnalyzing(false);
    }
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
        toast("Track compiled — tags, artwork and lyrics embedded; archived copy ready below.", "ok");
      } else {
        toast(`Compile failed: ${data?.error || `HTTP ${res.status}`}`);
      }
    } catch (e: any) {
      console.error(e);
      toast(`Compile failed: ${e.message || e}`);
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
  const isAlac = playerPath?.toLowerCase().endsWith(".m4a") && analysisResult?.spectral?.is_lossless;
  const transcodeParam = isAlac ? "&transcode=1" : "";
  const playerSrc = playerPath ? `/api/stream?path=${encodeURIComponent(playerPath)}${transcodeParam}` : null;

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

      
      {/* SIDEBAR NAVIGATION */}
      <div className="flex h-screen w-full overflow-hidden">
        <aside className="w-64 flex-shrink-0 bg-void-950/80 border-r border-void-700 backdrop-blur-md flex flex-col z-40 hidden md:flex">
          {/* Brand */}
          <div className="h-16 flex items-center gap-3 px-6 border-b border-void-700/80">
            {booting ? (
              <div className="w-8 h-8 shrink-0" />
            ) : (
              <Mascot layoutId="mascot" size={32} spin={false} reactive={false} />
            )}
            <div>
              <h1 className="text-white font-display font-bold text-sm tracking-tight leading-tight">Monster Archiver</h1>
              <p className="text-[9px] text-deezer-400 font-mono tracking-widest uppercase font-semibold">Nexus Suite v18.4</p>
            </div>
          </div>

          {/* Nav Links */}
          <div className="flex-1 overflow-y-auto py-6 px-4 space-y-1.5 custom-scrollbar">
            <div className="text-[10px] font-mono text-slate-500 uppercase tracking-widest mb-3 px-2">Workspaces</div>
            {[
              { id: "archiver", label: "Archiver Engine", icon: AudioLines },
              { id: "library", label: "Library Management", icon: Database },
              { id: "captions", label: "Video Captions", icon: MessageSquareText },
              { id: "settings", label: "System Settings", icon: SlidersHorizontal },
            ].map((s) => {
              const Icon = s.icon;
              const active = screen === s.id;
              return (
                <button
                  key={s.id}
                  onClick={() => setScreen(s.id as any)}
                  className={`w-full px-3 py-2.5 rounded-lg text-sm font-medium flex items-center gap-3 transition-all cursor-pointer border-0 ${
                    active
                      ? "bg-gradient-to-r from-deezer-600/20 to-flow-500/10 text-white shadow-sm border border-deezer-500/30"
                      : "bg-transparent text-slate-400 hover:text-slate-200 hover:bg-void-800"
                  }`}
                >
                  <Icon strokeWidth={1.5} className={`w-5 h-5 ${active ? "text-deezer-400 drop-shadow-[0_0_8px_rgba(var(--glow-rgb),0.5)]" : "text-slate-500"}`} />
                  {s.label}
                  {active && (
                    <motion.div
                      layoutId="sidebar-indicator"
                      className="absolute left-0 w-1 h-6 bg-deezer-500 rounded-r-full"
                      transition={{ type: "spring", stiffness: 300, damping: 30 }}
                    />
                  )}
                </button>
              );
            })}
          </div>

          {/* Footer Controls */}
          <div className="p-4 border-t border-void-700/80 space-y-4">
            <div className="flex items-center justify-between px-2">
              <ThemePicker />
              <button
                onClick={() => setActivityOpen(true)}
                title="Recent Activity"
                className="w-8 h-8 flex items-center justify-center rounded-lg border border-void-700 text-slate-400 hover:text-deezer-300 hover:border-deezer-500/40 hover:bg-void-800 transition-colors cursor-pointer"
              >
                <History className="w-4 h-4" />
              </button>
            </div>
            
            <div className="flex items-center gap-2 px-2 pb-1">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-flow-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-flow-400" />
              </span>
              <div className="flex flex-col">
                <span className="text-[9px] text-slate-500 font-semibold uppercase tracking-wider font-mono">SYS Time</span>
                <span className="text-slate-300 text-[10px] font-mono font-medium">{currentTime || "Loading..."}</span>
              </div>
            </div>
          </div>
        </aside>

        {/* MAIN CONTENT AREA */}
        <div className="flex-1 flex flex-col h-full relative overflow-hidden z-10 bg-void-950/40">
          {/* MOBILE HEADER */}
          <header className="md:hidden border-b border-void-700/80 bg-void-900/70 backdrop-blur-md sticky top-0 z-50">
             <div className="h-14 flex items-center justify-between px-4">
                <div className="flex items-center gap-2">
                  <Mascot layoutId="mascot-mobile" size={24} spin={false} reactive={false} />
                  <h1 className="text-white font-display font-bold text-sm">Monster Archiver</h1>
                </div>
                <ThemePicker />
             </div>
             <div className="flex border-t border-void-700/60 overflow-x-auto custom-scrollbar">
                {[
                  { id: "archiver", label: "Archiver", icon: AudioLines },
                  { id: "library", label: "Library", icon: Database },
                  { id: "captions", label: "Captions", icon: MessageSquareText },
                  { id: "settings", label: "Settings", icon: SlidersHorizontal },
                ].map((s) => {
                  const Icon = s.icon;
                  const active = screen === s.id;
                  return (
                    <button
                      key={s.id}
                      onClick={() => setScreen(s.id as any)}
                      className={`flex-shrink-0 px-4 py-2 text-xs font-semibold flex items-center justify-center gap-1.5 transition-colors cursor-pointer border-0 ${
                        active ? "text-deezer-400 border-b-2 border-deezer-500 bg-void-800/50" : "text-slate-500 bg-transparent"
                      }`}
                    >
                      <Icon strokeWidth={1.5} className="w-4 h-4" />
                      {s.label}
                    </button>
                  );
                })}
             </div>
          </header>

          <main className="flex-1 overflow-y-auto overflow-x-hidden p-4 md:p-8 custom-scrollbar relative">
            <div className="max-w-6xl mx-auto">
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
          className={`w-full bg-void-900 rounded-xl border-2 border-dashed p-10 text-center transition-all duration-300 relative overflow-hidden group mb-8 ${
            dragActive
              ? "border-deezer-400 bg-void-800 scale-[1.01]"
              : "border-void-600 hover:border-deezer-500 hover:bg-void-950/50"
          }`}
        >
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
              <div className="flex flex-col gap-3">
                <button
                  onClick={closeTrack}
                  className="self-start px-3 py-1.5 bg-void-900 hover:bg-void-800 border border-void-700 rounded-lg text-xs font-semibold text-slate-300 transition-colors flex items-center gap-1.5 cursor-pointer"
                >
                  <ArrowLeft className="w-4 h-4" />
                  Close Track &amp; New Upload
                </button>
                <div className="bg-void-900 rounded-xl border border-void-700 p-5 flex flex-wrap items-center justify-between gap-4">
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
                  <div className="flex flex-col items-center justify-center py-20 bg-void-900 rounded-xl border border-void-700">
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
                          <div className="bg-void-900 rounded-xl border border-void-700 p-5">
                            <h4 className="text-white font-medium text-sm mb-3.5 flex items-center gap-1.5 uppercase tracking-wider text-slate-400">
                              <Volume2 className="w-4 h-4 text-deezer-400" />
                              Audio Features Output
                            </h4>
                            <div className="grid grid-cols-2 gap-4">
                              <div className="bg-void-950 rounded-lg p-3 border border-void-700/60">
                                <span className="text-[10px] text-slate-500 font-semibold block uppercase tracking-wider">Tempo</span>
                                <span className="text-white font-bold text-lg font-mono">{analysisResult.bpm || "Calculating..."} BPM</span>
                              </div>
                              <div className="bg-void-950 rounded-lg p-3 border border-void-700/60">
                                <span className="text-[10px] text-slate-500 font-semibold block uppercase tracking-wider">Musical Key</span>
                                <span className="text-white font-bold text-lg font-mono">{analysisResult.key || "Calculating..."}</span>
                              </div>
                            </div>
                          </div>

                          {/* Quick checklist */}
                          <div className="bg-void-900 rounded-xl border border-void-700 p-5 flex flex-col justify-between">
                            <h4 className="text-white font-medium text-sm mb-3 flex items-center gap-1.5 uppercase tracking-wider text-slate-400">
                              <CheckCircle className="w-4 h-4 text-emerald-400" />
                              Pre-Check Checklist
                            </h4>
                            <div className="space-y-2 text-xs">
                              <div className="flex items-center justify-between p-2.5 bg-void-950 rounded-lg border border-void-700/60">
                                <span className="text-slate-400">Embedded Lyrics Tag</span>
                                <span className={`font-semibold ${analysisResult.metadata.has_lyrics ? "text-emerald-400" : "text-amber-400"}`}>
                                  {analysisResult.metadata.has_lyrics ? "Wiped & Synced" : "None detected"}
                                </span>
                              </div>
                              <div className="flex items-center justify-between p-2.5 bg-void-950 rounded-lg border border-void-700/60">
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
            <>
            <motion.div
              key="welcome"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="flex flex-col items-center justify-center py-20 text-center"
            >
              <div className="relative p-4 bg-void-900 border border-void-700 rounded-xl mb-4 animate-float-y stitched-frame">
                <Mascot size={64} spin blink />
              </div>
              <h3 className="text-white font-semibold text-lg">No active track in workspace</h3>
              <p className="text-slate-500 text-sm max-w-sm mt-1.5 leading-relaxed">
                Drag and drop your audio files above to initialize the tagging, spectrogram analyzer, and lyric compiler dashboard.
              </p>
            </motion.div>
            <LocalFolderBrowser onSelectFile={uploadFile} />
          </>
          )}
        </AnimatePresence>
          </>
        )}
      </div>
    </main>
  </div>
  
  <ToastHost />

  {uploadedFile && playerSrc && (
    <AudioPlayer
      lyricsText={lyricsText}
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
</div>
  );
}