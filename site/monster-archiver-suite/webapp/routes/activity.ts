import { Router } from "express";
import { execRezakirJson } from "../lib/pythonBridge";
import { getLibraryConfig } from "../lib/libraryConfig";

const router = Router();

// Recent-operations log (undo safety net for Fix-All/Merge-All, which can
// move files unattended). See monster_archiver/activity.py and cli.py's
// --activity-log/--revert-activity modes.
router.get("/api/activity", async (req, res) => {
  // Clamp: a negative or 0 limit would make sqlite's LIMIT return nothing,
  // and a huge one would stringify the entire activity table into a response
  // nobody asked for. 1–200 covers the UI (and then some).
  const raw = Number(req.query.limit);
  const limit = Number.isFinite(raw) ? Math.min(200, Math.max(1, Math.floor(raw))) : 20;
  const { libraryDir } = getLibraryConfig();
  try {
    const result = await execRezakirJson(["--activity-log", String(limit), "--library-dir", libraryDir, "--json"]);
    if (result.error) return res.status(500).json({ error: result.error });
    res.json(result);
  } catch (error: any) {
    res.status(500).json({ error: error.message });
  }
});

router.post("/api/activity/:id/revert", async (req, res) => {
  const id = Number(req.params.id);
  if (!Number.isInteger(id)) {
    return res.status(400).json({ error: "Invalid activity id" });
  }
  const { libraryDir } = getLibraryConfig();
  try {
    const result = await execRezakirJson(["--revert-activity", String(id), "--library-dir", libraryDir, "--json"]);
    if (result.error) return res.status(400).json({ error: result.error });
    res.json(result);
  } catch (error: any) {
    res.status(500).json({ error: error.message });
  }
});

export default router;
