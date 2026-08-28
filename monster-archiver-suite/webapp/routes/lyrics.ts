import { Router } from "express";
import { resolveAllowedFilePath } from "../lib/serverConfig";
import { translateWithOllama, translateLinesAtIndices } from "../lib/ollamaTranslate";
import { translateWithGeminiPro, translateLinesAtIndicesGemini } from "../lib/geminiTranslate";
import { transcribeWithRezakir } from "../lib/pythonBridge";
import { readSharedConfig } from "../lib/sharedConfig";

const router = Router();

// Generate & Translate Lyrics using a locally-running Ollama LLM or Google Gemini Pro
// See lib/ollamaTranslate.ts and lib/geminiTranslate.ts.
router.post("/api/generate-lyrics", async (req, res) => {
  const { lyricsText, title, artist, mode } = req.body;
  if (!lyricsText) {
    return res.status(400).json({ error: "Lyrics text is required" });
  }
  try {
    const config = readSharedConfig();
    let result;
    if (String(config.DEFAULT_TRANSLATION_ENGINE) === "6") {
      result = await translateWithGeminiPro(lyricsText, title, artist, mode);
    } else {
      result = await translateWithOllama(lyricsText, title, artist, mode);
    }
    res.json(result);
  } catch (error: any) {
    res.status(500).json({ error: error.message });
  }
});

// Re-translate just one line (or a handful of selected lines)
router.post("/api/translate-line", async (req, res) => {
  const { lyricsText, lineIndex, lineIndices, title, artist, mode } = req.body;
  if (typeof lyricsText !== "string" || !lyricsText) {
    return res.status(400).json({ error: "Lyrics text is required" });
  }
  const indices: number[] = Array.isArray(lineIndices)
    ? lineIndices
    : typeof lineIndex === "number"
      ? [lineIndex]
      : [];
  if (!indices.length) {
    return res.status(400).json({ error: "lineIndex (or lineIndices) is required" });
  }
  try {
    const config = readSharedConfig();
    let result;
    if (String(config.DEFAULT_TRANSLATION_ENGINE) === "6") {
      result = await translateLinesAtIndicesGemini(lyricsText, indices, title, artist, mode);
    } else {
      result = await translateLinesAtIndices(lyricsText, indices, title, artist, mode);
    }
    res.json(result);
  } catch (error: any) {
    res.status(400).json({ error: error.message });
  }
});

// AI transcription fallback (Demucs vocal isolation + multi-pass Faster-Whisper),
// for when no lyrics were found in any database. Slow (can take minutes on
// CPU) and self-installs heavy ML dependencies on first use — see
// transcribeWithRezakir() in lib/pythonBridge.ts.
router.post("/api/transcribe", async (req, res) => {
  const { title, artist, genre, lang } = req.body;
  // Constrained to UPLOADS_DIR/OUTPUT_DIR (same allowlist as /api/stream).
  const filePath = resolveAllowedFilePath(req.body?.filePath);
  if (!filePath) {
    return res.status(400).json({ error: "Valid filePath inside the uploads/output directory is required" });
  }
  try {
    const lrc = await transcribeWithRezakir(filePath, { title, artist, genre, lang });
    res.json({ lrc });
  } catch (error: any) {
    res.status(500).json({ error: error.message });
  }
});

export default router;
