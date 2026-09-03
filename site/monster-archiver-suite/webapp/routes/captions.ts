import { Router } from "express";
import fs from "fs";
import path from "path";
import { upload, UPLOADS_DIR, OUTPUT_DIR, resolveAllowedFilePath } from "../lib/serverConfig";
import { safeJoinFilename, streamWithRange } from "../lib/fileStream";
import { transcribeVideoCaptions, burnVideoCaptions, CaptionSegment } from "../lib/pythonBridge";
import { translateCaptionSegments, CAPTION_TARGET_LANGUAGES } from "../lib/ollamaTranslate";

const router = Router();

const VIDEO_MIME_TYPES: Record<string, string> = {
  ".mp4": "video/mp4",
  ".mov": "video/quicktime",
  ".mkv": "video/x-matroska",
  ".webm": "video/webm",
  ".m4v": "video/x-m4v",
  ".avi": "video/x-msvideo",
};
const ALLOWED_VIDEO_EXTS = Object.keys(VIDEO_MIME_TYPES);

function isValidSegment(s: any): s is CaptionSegment {
  return (
    s &&
    typeof s.start === "number" &&
    typeof s.end === "number" &&
    typeof s.text === "string" &&
    typeof s.speaker === "string"
  );
}

// Upload a video file for captioning. Reuses the shared multer instance
// (see routes/upload.ts) -- same UTF-8-filename fix and UPLOADS_DIR
// destination -- but rejects non-video extensions server-side since that
// instance has no fileFilter of its own (it's shared with audio uploads).
router.post("/api/captions/upload", upload.single("video"), (req, res) => {
  if (!req.file) {
    return res.status(400).json({ error: "No file uploaded" });
  }
  const ext = path.extname(req.file.originalname).toLowerCase();
  if (!ALLOWED_VIDEO_EXTS.includes(ext)) {
    try {
      fs.unlinkSync(req.file.path);
    } catch {
      // best-effort cleanup
    }
    return res.status(400).json({
      error: `Unsupported video format "${ext || "(none)"}". Use MP4, MOV, MKV, WEBM, M4V, or AVI.`,
    });
  }
  res.json({
    message: "Video uploaded successfully",
    originalName: req.file.originalname,
    filename: req.file.filename,
    path: req.file.path,
    size: req.file.size,
  });
});

// Phase 1: extract audio -> transcribe (Faster-Whisper, Demucs/BPM-key
// skipped since this isn't music) -> diarize speakers (pyannote.audio) ->
// merge into {start,end,text,speaker} segments for review. Nothing is
// burned into the video yet -- see caption_video() in
// monster_archiver/video_captions.py.
router.post("/api/captions/transcribe", async (req, res) => {
  const { lang, title, artist, genre } = req.body;
  // Constrained to UPLOADS_DIR/OUTPUT_DIR (same allowlist as /api/stream).
  const filePath = resolveAllowedFilePath(req.body?.filePath);
  if (!filePath) {
    return res.status(400).json({ error: "Valid filePath inside the uploads/output directory is required" });
  }
  try {
    const result = await transcribeVideoCaptions(filePath, { lang, title, artist, genre });
    res.json(result);
  } catch (error: any) {
    res.status(500).json({ error: error.message });
  }
});

// Optional step between transcribe-review and burn: translate the (possibly
// already user-corrected) segment text via a locally-running Ollama model --
// same offline, no-API-key local LLM pipeline the Lyrics Studio's "Translate"
// button uses (see lib/ollamaTranslate.ts), just with a dialogue/subtitle
// prompt instead of a song-lyric one. Returns the full segments array back
// (start/end/speaker untouched, only .text changes) plus flaggedIndices for
// any line that needed translation but came back unchanged, so the review
// UI can point the user at it instead of silently leaving it un-translated.
router.post("/api/captions/translate", async (req, res) => {
  const { segments, mode, videoTitle, targetLanguage } = req.body;
  if (!Array.isArray(segments) || !segments.length || !segments.every(isValidSegment)) {
    return res.status(400).json({ error: "A non-empty array of {start,end,text,speaker} segments is required" });
  }
  if (mode !== undefined && mode !== "replace" && mode !== "bilingual") {
    return res.status(400).json({ error: 'mode must be "replace" or "bilingual" if provided' });
  }
  if (targetLanguage !== undefined && !CAPTION_TARGET_LANGUAGES[targetLanguage]) {
    return res.status(400).json({
      error: `targetLanguage must be one of: ${Object.keys(CAPTION_TARGET_LANGUAGES).join(", ")}`,
    });
  }
  try {
    const result = await translateCaptionSegments(segments, { mode, videoTitle, targetLanguage });
    res.json(result);
  } catch (error: any) {
    res.status(500).json({ error: error.message });
  }
});

// Phase 2: burn the (user-reviewed/corrected) segments into the video as
// color-coded, per-speaker ASS subtitles, then copy the result into
// OUTPUT_DIR under a friendly filename for download -- same
// tag-then-copy-to-OUTPUT_DIR convention /api/apply-tags uses (routes/tags.ts).
router.post("/api/captions/burn", async (req, res) => {
  const { segments, originalName, fontSize } = req.body;
  // Constrained to UPLOADS_DIR/OUTPUT_DIR (same allowlist as /api/stream).
  const filePath = resolveAllowedFilePath(req.body?.filePath);
  if (!filePath) {
    return res.status(400).json({ error: "Valid filePath inside the uploads/output directory is required" });
  }
  if (!Array.isArray(segments) || !segments.length || !segments.every(isValidSegment)) {
    return res.status(400).json({ error: "A non-empty array of {start,end,text,speaker} segments is required" });
  }
  if (fontSize !== undefined && (typeof fontSize !== "number" || !Number.isFinite(fontSize))) {
    return res.status(400).json({ error: "fontSize must be a number if provided" });
  }

  try {
    const outputPath = await burnVideoCaptions(filePath, segments, { fontSize });
    if (!outputPath || !fs.existsSync(outputPath)) {
      return res.status(500).json({ error: "Subtitle burn did not produce an output file" });
    }

    const ext = path.extname(outputPath) || ".mp4";
    const rawBase = originalName
      ? path.basename(String(originalName), path.extname(String(originalName)))
      : path.basename(filePath, path.extname(filePath));
    // Only strip characters actually illegal in a filename, same as tags.ts's
    // finalFilename sanitizer, so Persian/Unicode titles survive intact.
    const safeBase = rawBase.replace(/[\x00-\x1f<>:"/\\|?*]/g, "").trim() || "video";
    const finalFilename = `${safeBase}_captioned${ext}`;
    const downloadPath = path.join(OUTPUT_DIR, finalFilename);
    fs.copyFileSync(outputPath, downloadPath);

    res.json({
      status: "success",
      filename: finalFilename,
      downloadUrl: `/api/captions/download/${encodeURIComponent(finalFilename)}`,
      path: outputPath,
    });
  } catch (error: any) {
    res.status(500).json({ error: error.message });
  }
});

// Inline video playback (Range-enabled), for previewing the original upload
// or the finalized captioned copy in the review UI -- shared Range/allowlist
// handling with /api/stream in routes/tags.ts (see lib/fileStream.ts), scoped
// to the UPLOADS_DIR/OUTPUT_DIR allowlist (the only two places this app ever
// writes a file, so an arbitrary ?path= can't read anything else on disk).
router.get("/api/captions/stream", (req, res) => {
  const rawPath = req.query.path;
  if (!rawPath || typeof rawPath !== "string") {
    return res.status(400).json({ error: "path query parameter is required" });
  }

  const resolved = resolveAllowedFilePath(rawPath);
  if (!resolved) {
    return res.status(403).json({ error: "Path is outside the allowed upload/output directories (or does not exist)" });
  }

  const contentType = VIDEO_MIME_TYPES[path.extname(resolved).toLowerCase()] || "application/octet-stream";
  streamWithRange(req, res, resolved, contentType);
});

// Serves the finalized captioned video for download.
router.get("/api/captions/download/:filename", (req, res) => {
  // Same encoded-traversal guard as /api/download/:filename in routes/tags.ts —
  // never path.join a decoded URL param onto a directory unchecked.
  const fullPath = safeJoinFilename(OUTPUT_DIR, req.params.filename);
  if (!fullPath) {
    return res.status(404).send("File not found");
  }
  res.download(fullPath, path.basename(fullPath));
});

export default router;
