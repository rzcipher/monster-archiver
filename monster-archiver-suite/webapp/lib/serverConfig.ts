import path from "path";
import fs from "fs";
import multer from "multer";

export const PORT = Number(process.env.PORT) || 3000;
// Bind to 0.0.0.0 for container accessibility
export const HOST = process.env.HOST || "0.0.0.0";

export const UPLOADS_DIR = path.join(process.cwd(), "uploads");
export const OUTPUT_DIR = path.join(process.cwd(), "output");
for (const dir of [UPLOADS_DIR, OUTPUT_DIR]) {
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

// Multer disk storage for uploaded audio files.
const storage = multer.diskStorage({
  destination: (req, file, cb) => {
    cb(null, UPLOADS_DIR);
  },
  filename: (req, file, cb) => {
    // Busboy (which multer uses under the hood) decodes multipart header
    // values — including the filename field — as latin1 by default, per the
    // old multipart spec. Browsers send the filename as raw UTF-8 bytes, so
    // any non-Latin text (Japanese, Korean, etc.) comes through mis-decoded
    // one byte at a time (e.g. "こんにちは" -> "ã\x81\x93â¦..."). Re-interpreting
    // the mangled string as latin1 bytes and decoding *those* as UTF-8
    // recovers the original text. This mutates file.originalname in place,
    // so the fix also applies to req.file.originalname used later (API
    // response, UI display, etc.), not just the sanitized name below.
    file.originalname = Buffer.from(file.originalname, "latin1").toString("utf8");

    // Keep the original extension but sanitize the on-disk staging name —
    // this is just the temp filename multer writes to UPLOADS_DIR, not what
    // gets shown to the user or saved into the library, so ASCII-only here
    // is fine and keeps things safe across filesystems.
    const ext = path.extname(file.originalname);
    const base = path.basename(file.originalname, ext).replace(/[^a-zA-Z0-9_-]/g, "_");
    cb(null, `${base}_${Date.now()}${ext}`);
  }
});
// Uploads are staged to disk, so a hard cap mainly guards against runaway/
// abusive requests filling the disk. 2 GB comfortably covers even long
// lossless audio and large source videos for the captions tab; the UI's
// advertised "up to 100MB" for audio is a soft guideline, not the limit.
const MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024;
export const upload = multer({ storage, limits: { fileSize: MAX_UPLOAD_BYTES } });

// Validate that a client-supplied file path points at a real file inside one
// of the directories this app itself writes to (UPLOADS_DIR/OUTPUT_DIR).
// Every state-changing endpoint that accepts a raw filePath must go through
// this — otherwise a request could point the tagger/transcriber at any file
// on disk (see /api/stream, which has always enforced the same rule).
export function resolveAllowedFilePath(rawPath: unknown): string | null {
  if (!rawPath || typeof rawPath !== "string") return null;
  const resolved = path.resolve(rawPath);
  const allowedRoots = [UPLOADS_DIR, OUTPUT_DIR].map((d) => path.resolve(d) + path.sep);
  if (!allowedRoots.some((root) => resolved.startsWith(root))) return null;
  if (!fs.existsSync(resolved) || !fs.statSync(resolved).isFile()) return null;
  return resolved;
}
