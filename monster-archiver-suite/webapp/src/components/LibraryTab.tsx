import React, { useState, useEffect } from "react";
import {
  FolderOpen,
  RefreshCw,
  ShieldCheck,
  Wrench,
  Merge,
  AlertTriangle,
  CheckCircle2,
  FolderSearch
} from "lucide-react";
import NamingTemplatesPanel from "./NamingTemplatesPanel";

interface ScanIssue {
  path: string;
  issues: string[];
}

interface ScanResult {
  total: number;
  flagged: number;
  clean: number;
  issues: ScanIssue[];
  csv_path: string | null;
}

interface MergeResult {
  candidates: number;
  merged: number;
  skipped: number;
  dry_run: boolean;
}

export default function LibraryTab() {
  const [libraryDir, setLibraryDirState] = useState("");
  const [defaultLibraryDir, setDefaultLibraryDir] = useState("");
  const [dirInput, setDirInput] = useState("");
  const [savingDir, setSavingDir] = useState(false);
  const [dirError, setDirError] = useState<string | null>(null);
  const [loadingSettings, setLoadingSettings] = useState(true);

  const [scanning, setScanning] = useState(false);
  const [fixing, setFixing] = useState(false);
  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [scanError, setScanError] = useState<string | null>(null);

  const [previewingMerge, setPreviewingMerge] = useState(false);
  const [merging, setMerging] = useState(false);
  const [mergePreview, setMergePreview] = useState<MergeResult | null>(null);
  const [mergeResult, setMergeResult] = useState<MergeResult | null>(null);
  const [mergeError, setMergeError] = useState<string | null>(null);

  const loadSettings = async () => {
    setLoadingSettings(true);
    try {
      const res = await fetch("/api/library/settings");
      const data = await res.json();
      setLibraryDirState(data.libraryDir);
      setDirInput(data.libraryDir);
      setDefaultLibraryDir(data.defaultLibraryDir);
    } catch (e: any) {
      setDirError(e.message || String(e));
    } finally {
      setLoadingSettings(false);
    }
  };

  useEffect(() => {
    loadSettings();
  }, []);

  const saveDir = async (path: string) => {
    setSavingDir(true);
    setDirError(null);
    try {
      const res = await fetch("/api/library/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ libraryDir: path })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
      setLibraryDirState(data.libraryDir);
      setDirInput(data.libraryDir);
      // Folder changed — any stale scan/merge results no longer apply.
      setScanResult(null);
      setMergePreview(null);
      setMergeResult(null);
    } catch (e: any) {
      setDirError(e.message || String(e));
    } finally {
      setSavingDir(false);
    }
  };

  const runScan = async (fix: boolean) => {
    if (fix) setFixing(true);
    else setScanning(true);
    setScanError(null);
    try {
      const res = await fetch("/api/library/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fix })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
      setScanResult(data);
    } catch (e: any) {
      setScanError(e.message || String(e));
    } finally {
      setScanning(false);
      setFixing(false);
    }
  };

  const previewMerge = async () => {
    setPreviewingMerge(true);
    setMergeError(null);
    setMergeResult(null);
    try {
      const res = await fetch("/api/library/merge-albums", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dryRun: true })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
      setMergePreview(data);
    } catch (e: any) {
      setMergeError(e.message || String(e));
    } finally {
      setPreviewingMerge(false);
    }
  };

  const confirmMerge = async () => {
    setMerging(true);
    setMergeError(null);
    try {
      const res = await fetch("/api/library/merge-albums", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dryRun: false })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
      setMergeResult(data);
      setMergePreview(null);
    } catch (e: any) {
      setMergeError(e.message || String(e));
    } finally {
      setMerging(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* LIBRARY FOLDER */}
      <div className="bg-[#0f1117] rounded-xl border border-slate-800 p-6 shadow-2xl ring-1 ring-white/5">
        <h3 className="text-white font-medium text-lg mb-5 flex items-center gap-2 border-b border-slate-800/60 pb-3">
          <FolderOpen className="w-5 h-5 text-indigo-400" />
          Library Folder
        </h3>
        <p className="text-slate-500 text-xs mb-4 leading-relaxed">
          The folder rezakir.py archives music into, and the one Health Scan and Merge Split Albums operate on.
          Defaults to the same folder the CLI already uses, so an existing archive works with no migration.
        </p>
        <div className="flex flex-col sm:flex-row gap-2.5">
          <input
            type="text"
            value={dirInput}
            onChange={(e) => setDirInput(e.target.value)}
            disabled={loadingSettings}
            placeholder={defaultLibraryDir}
            className="flex-1 bg-[#0b0e14]/60 border border-slate-800 rounded-lg py-2.5 px-4 text-white text-sm font-mono focus:outline-none focus:border-indigo-500 transition-colors"
          />
          <button
            onClick={() => saveDir(dirInput)}
            disabled={savingDir || loadingSettings || !dirInput.trim() || dirInput === libraryDir}
            className="px-4 py-2.5 bg-indigo-600 hover:bg-indigo-500 disabled:bg-slate-800 disabled:text-slate-600 text-white rounded-lg text-sm font-semibold transition-colors cursor-pointer border-0 shrink-0"
          >
            {savingDir ? "Saving..." : "Save"}
          </button>
          <button
            onClick={() => saveDir(defaultLibraryDir)}
            disabled={savingDir || loadingSettings || libraryDir === defaultLibraryDir}
            className="px-4 py-2.5 bg-[#0b0e14] hover:bg-slate-800 disabled:text-slate-600 text-slate-300 border border-slate-800 rounded-lg text-sm font-medium transition-colors cursor-pointer shrink-0"
          >
            Use Default
          </button>
        </div>
        {dirError && (
          <p className="text-rose-400 text-xs mt-2.5 flex items-center gap-1.5">
            <AlertTriangle className="w-3.5 h-3.5" /> {dirError}
          </p>
        )}
        {!loadingSettings && (
          <p className="text-slate-600 text-[11px] mt-2.5 font-mono">Active: {libraryDir}</p>
        )}
      </div>

      {/* NAMING TEMPLATES */}
      <NamingTemplatesPanel />

      {/* HEALTH SCAN */}
      <div className="bg-[#0f1117] rounded-xl border border-slate-800 p-6 shadow-2xl ring-1 ring-white/5">
        <div className="flex items-center justify-between border-b border-slate-800/60 pb-3 mb-5 flex-wrap gap-3">
          <h3 className="text-white font-medium text-lg flex items-center gap-2">
            <ShieldCheck className="w-5 h-5 text-indigo-400" />
            Library Health Scan
          </h3>
          <div className="flex items-center gap-2.5">
            <button
              onClick={() => runScan(false)}
              disabled={scanning || fixing}
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:bg-slate-800 disabled:text-slate-600 text-white rounded-lg text-sm font-semibold transition-colors cursor-pointer border-0 flex items-center gap-2"
            >
              {scanning ? <RefreshCw className="w-4 h-4 animate-spin" /> : <FolderSearch className="w-4 h-4" />}
              {scanning ? "Scanning..." : "Run Scan"}
            </button>
            {scanResult && scanResult.flagged > 0 && (
              <button
                onClick={() => runScan(true)}
                disabled={scanning || fixing}
                className="px-4 py-2 bg-amber-600 hover:bg-amber-500 disabled:bg-slate-800 disabled:text-slate-600 text-white rounded-lg text-sm font-semibold transition-colors cursor-pointer border-0 flex items-center gap-2"
              >
                {fixing ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Wrench className="w-4 h-4" />}
                {fixing ? "Fixing..." : "Fix All"}
              </button>
            )}
          </div>
        </div>

        {scanError && (
          <p className="text-rose-400 text-xs mb-4 flex items-center gap-1.5">
            <AlertTriangle className="w-3.5 h-3.5" /> {scanError}
          </p>
        )}

        {!scanResult && !scanning && !scanError && (
          <p className="text-slate-600 text-xs">
            Scans for files missing genre, year, BPM, cover art, or lyrics. Run it to see what needs attention.
          </p>
        )}

        {scanResult && (
          <div>
            <div className="grid grid-cols-3 gap-4 mb-5">
              <div className="bg-[#0b0e14]/60 rounded-lg p-3 border border-slate-800/60 text-center">
                <span className="text-[10px] text-slate-500 font-semibold block uppercase tracking-wider">Total</span>
                <span className="text-white font-bold text-lg font-mono">{scanResult.total}</span>
              </div>
              <div className="bg-[#0b0e14]/60 rounded-lg p-3 border border-slate-800/60 text-center">
                <span className="text-[10px] text-slate-500 font-semibold block uppercase tracking-wider">Clean</span>
                <span className="text-emerald-400 font-bold text-lg font-mono">{scanResult.clean}</span>
              </div>
              <div className="bg-[#0b0e14]/60 rounded-lg p-3 border border-slate-800/60 text-center">
                <span className="text-[10px] text-slate-500 font-semibold block uppercase tracking-wider">Flagged</span>
                <span className="text-amber-400 font-bold text-lg font-mono">{scanResult.flagged}</span>
              </div>
            </div>

            {scanResult.flagged === 0 ? (
              <p className="text-emerald-400 text-sm flex items-center gap-1.5">
                <CheckCircle2 className="w-4 h-4" /> Library is clean — no issues found.
              </p>
            ) : (
              <div className="overflow-y-auto max-h-[320px] custom-scrollbar border border-slate-800/60 rounded-lg">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-[#0b0e14] text-slate-500 uppercase tracking-wider">
                    <tr>
                      <th className="text-left font-semibold px-3 py-2">File</th>
                      <th className="text-left font-semibold px-3 py-2">Issues</th>
                    </tr>
                  </thead>
                  <tbody>
                    {scanResult.issues.map((issue, idx) => (
                      <tr key={idx} className="border-t border-slate-800/60">
                        <td className="px-3 py-2 text-slate-300 font-mono break-all">{issue.path}</td>
                        <td className="px-3 py-2 text-amber-400/90">{issue.issues.join(", ")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {scanResult.csv_path && (
              <p className="text-slate-600 text-[11px] mt-2.5 font-mono">Full report saved to {scanResult.csv_path}</p>
            )}
          </div>
        )}
      </div>

      {/* MERGE SPLIT ALBUMS */}
      <div className="bg-[#0f1117] rounded-xl border border-slate-800 p-6 shadow-2xl ring-1 ring-white/5">
        <div className="flex items-center justify-between border-b border-slate-800/60 pb-3 mb-5 flex-wrap gap-3">
          <h3 className="text-white font-medium text-lg flex items-center gap-2">
            <Merge className="w-5 h-5 text-indigo-400" />
            Merge Split Albums
          </h3>
          <button
            onClick={previewMerge}
            disabled={previewingMerge || merging}
            className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:bg-slate-800 disabled:text-slate-600 text-white rounded-lg text-sm font-semibold transition-colors cursor-pointer border-0 flex items-center gap-2"
          >
            {previewingMerge ? <RefreshCw className="w-4 h-4 animate-spin" /> : <FolderSearch className="w-4 h-4" />}
            {previewingMerge ? "Scanning..." : "Preview (Dry Run)"}
          </button>
        </div>

        <p className="text-slate-500 text-xs mb-4 leading-relaxed">
          Finds albums split across multiple folders (usually caused by a featured-artist credit or a stray
          "(Explicit)"/"(Deluxe)" tag) and merges every track into the folder with the most tracks.
        </p>

        {mergeError && (
          <p className="text-rose-400 text-xs mb-4 flex items-center gap-1.5">
            <AlertTriangle className="w-3.5 h-3.5" /> {mergeError}
          </p>
        )}

        {mergePreview && (
          <div className="bg-[#0b0e14]/60 rounded-lg p-4 border border-slate-800/60 flex items-center justify-between flex-wrap gap-3">
            <p className="text-sm text-slate-300">
              {mergePreview.candidates === 0 ? (
                <span className="text-emerald-400 flex items-center gap-1.5">
                  <CheckCircle2 className="w-4 h-4" /> No split albums found.
                </span>
              ) : (
                <>
                  <span className="text-amber-400 font-semibold">{mergePreview.candidates}</span> album(s) would be merged.
                </>
              )}
            </p>
            {mergePreview.candidates > 0 && (
              <button
                onClick={confirmMerge}
                disabled={merging}
                className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:bg-slate-800 disabled:text-slate-600 text-white rounded-lg text-sm font-semibold transition-colors cursor-pointer border-0 flex items-center gap-2"
              >
                {merging ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Merge className="w-4 h-4" />}
                {merging ? "Merging..." : "Confirm Merge"}
              </button>
            )}
          </div>
        )}

        {mergeResult && (
          <div className="bg-[#0b0e14]/60 rounded-lg p-4 border border-slate-800/60 mt-4">
            <p className="text-sm text-emerald-400 flex items-center gap-1.5">
              <CheckCircle2 className="w-4 h-4" />
              Merged {mergeResult.merged} album(s), skipped {mergeResult.skipped}.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
