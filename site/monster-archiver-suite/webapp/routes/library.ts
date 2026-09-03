import { Router } from "express";
import fs from "fs";
import path from "path";
import { execRezakirJson } from "../lib/pythonBridge";
import { getLibraryConfig, setLibraryDir, getDefaultLibraryDir } from "../lib/libraryConfig";

const router = Router();

// GET current library folder (falls back to the CLI's own default so this
// works with zero setup — see lib/libraryConfig.ts).
router.get("/api/library/settings", (_req, res) => {
  const config = getLibraryConfig();
  res.json({ ...config, defaultLibraryDir: getDefaultLibraryDir() });
});

// PUT a new library folder. Only requires the path to already exist — the
// Python side (paths.set_library_dir) creates any missing subdirectories
// (.logs, the sqlite db) on first real use.
router.put("/api/library/settings", (req, res) => {
  const { libraryDir } = req.body;
  if (!libraryDir || typeof libraryDir !== "string") {
    return res.status(400).json({ error: "libraryDir is required" });
  }
  if (!fs.existsSync(libraryDir) || !fs.statSync(libraryDir).isDirectory()) {
    return res.status(400).json({ error: `Path does not exist or is not a directory: ${libraryDir}` });
  }
  try {
    const config = setLibraryDir(libraryDir);
    res.json({ ...config, defaultLibraryDir: getDefaultLibraryDir() });
  } catch (error: any) {
    res.status(500).json({ error: error.message });
  }
});

// Health scan (rezakir.py --scan [--fix] --library-dir <dir> --json). See
// monster_archiver/library_scan.py for the JSON shape returned.
router.post("/api/library/scan", async (req, res) => {
  const { fix } = req.body || {};
  const { libraryDir } = getLibraryConfig();
  try {
    const args = ["--scan", "--library-dir", libraryDir, "--json"];
    // Only a strict boolean true triggers the file-moving fix pass — string
    // values from a forged urlencoded body (e.g. fix=1) never count.
    if (fix === true) args.push("--fix");
    const result = await execRezakirJson(args);
    if (result.error) return res.status(500).json({ error: result.error });
    res.json(result);
  } catch (error: any) {
    res.status(500).json({ error: error.message });
  }
});

// Split-album merge (rezakir.py --merge-albums [--dry-run] --library-dir <dir> --json).
// The web UI always calls this with dryRun:true first and shows the preview
// before letting the user confirm a real (non-dry-run) merge — v1 has no
// per-group confirmation, so a real run merges every detected candidate.
router.post("/api/library/merge-albums", async (req, res) => {
  const { dryRun } = req.body || {};
  const { libraryDir } = getLibraryConfig();
  try {
    const args = ["--merge-albums", "--library-dir", libraryDir, "--json"];
    // Fail-safe default: a real (file-moving) merge only runs when the client
    // explicitly sends dryRun:false. A missing/absent body — e.g. a blind
    // cross-site form POST or a buggy client — gets the harmless dry-run
    // preview instead of silently merging every detected album group.
    if (dryRun !== false) args.push("--dry-run");
    const result = await execRezakirJson(args);
    if (result.error) return res.status(500).json({ error: result.error });
    res.json(result);
  } catch (error: any) {
    res.status(500).json({ error: error.message });
  }
});

const AUDIO_EXT_RE = /\.(mp3|flac|m4a|aac|wav)$/i;
const MAX_TRACK_RESULTS = 2000;

// List all audio files in the library directory, newest first.
// (This route used to be defined *after* `export default router` with a
// require("path") call inside it — require() doesn't exist in this ESM
// context, so the endpoint 500'd with "require is not defined" on every
// call. Rewritten to use the module's own imports and fs.withFileTypes,
// which also avoids a per-entry statSync round trip.)
router.get("/api/library/tracks", (_req, res) => {
  const { libraryDir } = getLibraryConfig();
  if (!fs.existsSync(libraryDir)) {
    return res.json([]);
  }

  const results: Array<{ path: string; originalName: string; size: number; mtime: number }> = [];

  function walk(dir: string, depth: number) {
    if (depth > 12) return; // pathological nesting guard
    let entries: fs.Dirent[];
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return; // unreadable directory — skip
    }
    for (const entry of entries) {
      if (results.length >= MAX_TRACK_RESULTS) return;
      // Dirent.isSymbolicLink() dirs can form cycles (a link pointing at an
      // ancestor) — the old statSync-based walk followed them and recursed
      // until the process crashed. Skip links entirely; they're never part of
      // the structure this app writes.
      if (entry.isSymbolicLink()) continue;
      const fullPath = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === ".logs") continue; // the app's own bookkeeping dir
        walk(fullPath, depth + 1);
      } else if (entry.isFile() && AUDIO_EXT_RE.test(entry.name)) {
        try {
          const stat = fs.statSync(fullPath);
          results.push({
            path: fullPath,
            originalName: entry.name,
            size: stat.size,
            mtime: stat.mtimeMs,
          });
        } catch {
          // raced with a delete — skip
        }
      }
    }
  }

  walk(libraryDir, 0);
  results.sort((a, b) => b.mtime - a.mtime); // Newest first
  res.json(results);
});

export default router;
