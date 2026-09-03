import { Router } from "express";
import fs from "fs";
import path from "path";
import { SETTINGS_SCHEMA } from "../lib/settingsSchema";

const router = Router();

// The exact same config.json rezakir.py's own monster_archiver/config.py
// reads/writes (BASE_DIR is the repo root, one level up from webapp/) — one
// shared file, so changes made here take effect the next time the CLI or
// any web-app Python call runs.
const CONFIG_FILE = path.join(process.cwd(), "..", "config.json");

function readConfig(): Record<string, any> {
  if (!fs.existsSync(CONFIG_FILE)) return {};
  return JSON.parse(fs.readFileSync(CONFIG_FILE, "utf-8"));
}

function writeConfig(conf: Record<string, any>) {
  // Atomic write (temp file + rename), matching config.py's own save pattern,
  // so a crash mid-write can't leave a truncated/corrupt config.json.
  const dir = path.dirname(CONFIG_FILE);
  const tmpPath = path.join(dir, `.config.json.tmp-${process.pid}-${Date.now()}`);
  fs.writeFileSync(tmpPath, JSON.stringify(conf, null, 4), "utf-8");
  fs.renameSync(tmpPath, CONFIG_FILE);
}

// Coerce a raw JSON value to what the schema says it should be, so a
// malformed request body (e.g. a number sent as a string) can't write a
// type-mismatched value into config.json that later confuses the Python side.
// Numeric fields are also clamped to the schema's declared min/max — the
// web Settings panel already bounds its inputs, but the API is open to other
// callers, and out-of-range values crash the CLI on the next run
// (MAX_WORKERS ≤ 0 makes ThreadPoolExecutor throw before the first file is
// ever touched; batch sizes of 0 would silently stall translation).
function coerce(type: string, value: any, min?: number, max?: number): any {
  switch (type) {
    case "number": {
      let n = Number(value);
      if (!Number.isFinite(n)) n = min !== undefined ? Math.max(min, 0) : 0;
      if (min !== undefined && n < min) n = min;
      if (max !== undefined && n > max) n = max;
      return n;
    }
    case "boolean":
      return Boolean(value);
    default:
      return String(value ?? "");
  }
}

router.get("/api/settings", (_req, res) => {
  try {
    const conf = readConfig();
    const values: Record<string, any> = {};
    for (const field of SETTINGS_SCHEMA) {
      values[field.key] = conf[field.key];
    }
    res.json({ values });
  } catch (error: any) {
    res.status(500).json({ error: error.message });
  }
});

router.put("/api/settings", (req, res) => {
  const { values } = req.body || {};
  if (!values || typeof values !== "object") {
    return res.status(400).json({ error: "values object is required" });
  }
  try {
    const conf = readConfig();
    const schemaByKey = Object.fromEntries(SETTINGS_SCHEMA.map((f) => [f.key, f]));
    for (const [key, value] of Object.entries(values)) {
      const field = schemaByKey[key];
      if (!field) continue; // ignore unknown keys — only schema-listed settings are editable here
      conf[key] = coerce(field.type, value, field.min, field.max);
    }
    writeConfig(conf);
    res.json({ values: Object.fromEntries(SETTINGS_SCHEMA.map((f) => [f.key, conf[f.key]])) });
  } catch (error: any) {
    res.status(500).json({ error: error.message });
  }
});

export default router;
