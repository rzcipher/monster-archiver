import fs from "fs";
import path from "path";

// The exact same config.json monster_archiver/config.py reads/writes
// (BASE_DIR is the repo root, one level up from webapp/) — one shared file,
// so a setting changed in the web app's Settings panel (routes/settings.ts)
// takes effect immediately for anything on the web-app side that reads it
// here too, no restart or Python round-trip required.
const CONFIG_FILE = path.join(process.cwd(), "..", "config.json");

// Read-only accessor. Returns {} if the file doesn't exist yet or is
// corrupt — callers are expected to fall back to their own defaults for any
// key that's missing, same as monster_archiver/config.py's DEFAULT_CONFIG
// merge behavior.
export function readSharedConfig(): Record<string, any> {
  try {
    if (!fs.existsSync(CONFIG_FILE)) return {};
    return JSON.parse(fs.readFileSync(CONFIG_FILE, "utf-8"));
  } catch {
    return {};
  }
}
