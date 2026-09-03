import fs from "fs";
import os from "os";
import path from "path";

// Where the web app's Library panel keeps its one setting: which folder the
// Python side's --library-dir flag should point --scan/--fix/--merge-albums
// at. Defaults to the exact same path rezakir.py's own paths.MUSIC_DIR
// resolves to, so an existing CLI-built archive works immediately with no
// migration step.
const LIBRARY_CONFIG_FILE = path.join(process.cwd(), "library-config.json");
const DEFAULT_LIBRARY_DIR = path.join(os.homedir(), "Music", "Monster_Library");

export interface LibraryConfig {
  libraryDir: string;
}

export function getLibraryConfig(): LibraryConfig {
  try {
    const raw = fs.readFileSync(LIBRARY_CONFIG_FILE, "utf-8");
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed.libraryDir === "string" && parsed.libraryDir.trim()) {
      return { libraryDir: parsed.libraryDir };
    }
  } catch {
    // No file yet, or it's corrupt — fall through to the default.
  }
  return { libraryDir: DEFAULT_LIBRARY_DIR };
}

export function setLibraryDir(libraryDir: string): LibraryConfig {
  const config: LibraryConfig = { libraryDir };
  // Atomic write (temp + rename), matching routes/settings.ts's pattern —
  // a crash mid-write must not leave a truncated/corrupt config file.
  const tmpPath = `${LIBRARY_CONFIG_FILE}.tmp-${process.pid}-${Date.now()}`;
  fs.writeFileSync(tmpPath, JSON.stringify(config, null, 2), "utf-8");
  fs.renameSync(tmpPath, LIBRARY_CONFIG_FILE);
  return config;
}

export function getDefaultLibraryDir(): string {
  return DEFAULT_LIBRARY_DIR;
}
