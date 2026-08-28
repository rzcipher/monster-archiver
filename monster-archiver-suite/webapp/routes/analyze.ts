import { Router } from "express";
import { runPythonHelper } from "../lib/pythonBridge";
import { resolveAllowedFilePath } from "../lib/serverConfig";

const router = Router();

// Analyze File (Metadata, Spectral integrity, BPM & Key)
router.post("/api/analyze", async (req, res) => {
  // Constrained to UPLOADS_DIR/OUTPUT_DIR (same allowlist as /api/stream) —
  // a raw filePath must never let a request point us at arbitrary disk paths.
  const filePath = resolveAllowedFilePath(req.body?.filePath);
  if (!filePath) {
    return res.status(400).json({ error: "Valid filePath inside the uploads/output directory is required" });
  }
  try {
    const result = await runPythonHelper(["analyze", filePath]);
    res.json(result);
  } catch (error: any) {
    res.status(500).json({ error: error.message });
  }
});

export default router;
