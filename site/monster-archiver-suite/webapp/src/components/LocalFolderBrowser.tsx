import React, { useEffect, useRef, useState } from "react";
import { Folder, Music, PlayCircle, Loader2, ArrowUpDown, HardDrive } from "lucide-react";
import { get, set } from "idb-keyval";

export default function LocalFolderBrowser({ onSelectFile }: { onSelectFile: (file: File) => void }) {
  const [localFiles, setLocalFiles] = useState<File[]>([]);
  const [scanning, setScanning] = useState(false);
  const [sortMode, setSortMode] = useState<"name" | "path">("name");
  
  // File System Access API State
  const [apiSupported, setApiSupported] = useState(true);
  const [hasHandle, setHasHandle] = useState(false);
  const [needsPermission, setNeedsPermission] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    // Check if FSA API is supported
    if (!('showDirectoryPicker' in window)) {
      setApiSupported(false);
      return;
    }
    
    // Check if we have a saved handle
    get("libraryDirHandle").then(async (handle) => {
      if (handle) {
        setHasHandle(true);
        // Check permission without prompting
        const perm = await (handle as any).queryPermission({ mode: 'read' });
        if (perm === 'granted') {
          scanHandle(handle as any);
        } else {
          setNeedsPermission(true);
        }
      }
    });
  }, []);

const getAudioFiles = async (dirHandle: any, path: string): Promise<File[]> => {
    let files: File[] = []
    // Pending getFile() promises materialized in batches: the old code
    // awaited one File object at a time, so mounting a multi-thousand-track
    // library meant one serial IPC round trip per file before the list
    // painted anything.
    let pending: Promise<void>[] = [];
    const flush = async () => {
      await Promise.all(pending);
      pending = [];
    };
    for await (const entry of dirHandle.values()) {
      if (entry.kind === 'file') {
        const ext = entry.name.substring(entry.name.lastIndexOf('.')).toLowerCase();
        if (['.mp3', '.flac', '.m4a', '.aac', '.wav'].includes(ext)) {
           const rel = path + entry.name;
           pending.push(
             entry.getFile().then((file: File) => {
               // Polyfill webkitRelativePath for sorting/display
               Object.defineProperty(file, 'webkitRelativePath', {
                 value: rel,
                 writable: false
               });
               files.push(file);
             })
           );
           if (pending.length >= 64) await flush();
        }
      } else if (entry.kind === 'directory') {
        await flush();
        files.push(...await getAudioFiles(entry, path + entry.name + '/'));
      }
    }
    await flush();
    return files;
  };

  const sortFiles = (files: File[], mode: "name" | "path") => {
    return [...files].sort((a, b) => {
      if (mode === "path") {
        const pathA = a.webkitRelativePath || a.name;
        const pathB = b.webkitRelativePath || b.name;
        return pathA.localeCompare(pathB);
      } else {
        return a.name.localeCompare(b.name);
      }
    });
  };

  const scanHandle = async (dirHandle: any) => {
    setScanning(true);
    try {
      const files = await getAudioFiles(dirHandle, dirHandle.name + '/');
      setLocalFiles(sortFiles(files, sortMode));
    } catch (e) {
      console.error("Failed to scan directory", e);
    } finally {
      setScanning(false);
    }
  };

  const handleMountFSA = async () => {
    try {
      const dirHandle = await (window as any).showDirectoryPicker({ mode: 'read' });
      await set("libraryDirHandle", dirHandle);
      setHasHandle(true);
      setNeedsPermission(false);
      scanHandle(dirHandle);
    } catch (e) {
      // User aborted or error
    }
  };

  const handleRestoreFSA = async () => {
    const handle = await get("libraryDirHandle");
    if (handle) {
      const perm = await (handle as any).requestPermission({ mode: 'read' });
      if (perm === 'granted') {
        setNeedsPermission(false);
        scanHandle(handle);
      }
    }
  };

  // Fallback for Firefox/Safari
  const handleFallbackSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files || e.target.files.length === 0) return;
    setScanning(true);
    
    const filesArray = Array.from(e.target.files);
    const audioFiles = filesArray.filter(file => {
      const ext = file.name.substring(file.name.lastIndexOf(".")).toLowerCase();
      return [".mp3", ".flac", ".m4a", ".aac", ".wav"].includes(ext);
    });

    setLocalFiles(sortFiles(audioFiles, sortMode));
    setScanning(false);
  };

  const handleSortChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const mode = e.target.value as "name" | "path";
    setSortMode(mode);
    setLocalFiles(sortFiles(localFiles, mode));
  };

  return (
    <div className="w-full bg-void-900/60 backdrop-blur-md rounded-xl border border-void-700 p-6 mt-4 shadow-xl">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-5">
        <h3 className="text-white font-medium text-lg flex items-center gap-2">
          <Folder className="w-5 h-5 text-deezer-400" />
          Local Auto-Mount Library
        </h3>
        
        <div className="flex items-center gap-3">
          {localFiles.length > 0 && (
            <div className="flex items-center gap-2 bg-void-950 border border-void-800 rounded-lg px-2 py-1">
              <ArrowUpDown className="w-3.5 h-3.5 text-slate-400" />
              <select 
                value={sortMode}
                onChange={handleSortChange}
                className="bg-transparent text-xs text-slate-300 outline-none border-none cursor-pointer"
              >
                <option value="name">Sort by File Name</option>
                <option value="path">Sort by Artist (Folder Path)</option>
              </select>
            </div>
          )}

          {apiSupported ? (
            <button
              onClick={handleMountFSA}
              className="px-4 py-2 bg-void-800 hover:bg-void-700 border border-void-600 rounded-lg text-sm text-white font-medium transition-colors cursor-pointer"
            >
              {localFiles.length > 0 || hasHandle ? "Change Folder" : "Mount Folder"}
            </button>
          ) : (
            <>
              <button
                onClick={() => inputRef.current?.click()}
                className="px-4 py-2 bg-void-800 hover:bg-void-700 border border-void-600 rounded-lg text-sm text-white font-medium transition-colors cursor-pointer"
              >
                {localFiles.length > 0 ? "Change Folder" : "Mount Folder"}
              </button>
              <input 
                type="file" 
                ref={inputRef}
                onChange={handleFallbackSelect}
                className="hidden"
                // @ts-ignore
                webkitdirectory=""
                directory=""
                multiple
              />
            </>
          )}
        </div>
      </div>

      {apiSupported && needsPermission && !scanning && localFiles.length === 0 && (
        <div 
          onClick={handleRestoreFSA}
          className="bg-deezer-900/10 hover:bg-deezer-900/20 border-2 border-dashed border-deezer-500/40 hover:border-deezer-500/70 rounded-xl p-10 mb-4 flex flex-col items-center justify-center cursor-pointer transition-all group shadow-lg"
        >
          <Folder className="w-12 h-12 text-deezer-400 mb-4 group-hover:scale-110 transition-transform" />
          <h3 className="text-xl font-bold text-white mb-2">Resume Local Library</h3>
          <p className="text-sm text-slate-400 text-center max-w-sm mb-6">
            Your folder is securely remembered! Browsers require a single click per session to reconnect to local files.
          </p>
          <button 
            className="px-8 py-3 bg-deezer-500 hover:bg-deezer-400 text-white text-sm font-bold rounded-lg shadow-lg shadow-deezer-500/20 transition-all group-hover:shadow-deezer-500/40"
          >
            Click to Reconnect Library
          </button>
        </div>
      )}

      {scanning ? (
        <div className="text-center py-10 text-slate-400 flex flex-col items-center">
          <Loader2 className="w-8 h-8 animate-spin mb-3 text-deezer-400" />
          <p className="text-sm font-medium">Scanning folders...</p>
        </div>
      ) : localFiles.length > 0 ? (
        <div className="overflow-y-auto max-h-72 custom-scrollbar pr-2 space-y-2">
          {localFiles.map((file, i) => (
            <div
              key={i}
              onClick={() => onSelectFile(file)}
              className="flex items-center justify-between p-3 bg-void-950/50 hover:bg-void-800 rounded-lg border border-void-800 hover:border-deezer-500/50 cursor-pointer transition-all group"
            >
              <div className="flex items-center gap-3 overflow-hidden">
                <div className="w-8 h-8 rounded bg-void-900 flex items-center justify-center shrink-0 group-hover:bg-deezer-500/20 text-slate-500 group-hover:text-deezer-400 transition-colors">
                  <Music className="w-4 h-4" />
                </div>
                <div className="truncate">
                  <p className="text-sm text-slate-200 font-medium truncate">{file.name}</p>
                  <p className="text-[10px] text-slate-500 font-mono mt-0.5 truncate max-w-lg">
                    {file.webkitRelativePath || file.name} • {(file.size / (1024 * 1024)).toFixed(1)} MB
                  </p>
                </div>
              </div>
              <PlayCircle className="w-5 h-5 text-slate-600 group-hover:text-deezer-400 opacity-0 group-hover:opacity-100 transition-all shrink-0" />
            </div>
          ))}
        </div>
      ) : (
        <div className="text-center py-10 flex flex-col items-center justify-center">
          <HardDrive className="w-12 h-12 text-slate-600 mb-3" />
          <p className="text-slate-400 text-sm max-w-md mx-auto">
            No folder mounted. Mount a local folder to list your tracks here for quick processing without manual dragging.
          </p>
        </div>
      )}
    </div>
  );
}
