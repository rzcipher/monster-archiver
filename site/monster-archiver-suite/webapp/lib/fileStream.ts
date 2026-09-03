import fs from "fs";
import path from "path";
import { UPLOADS_DIR, OUTPUT_DIR } from "./serverConfig";

// Shared helpers for the file-serving endpoints (/api/stream, /api/captions/stream,
// /api/download/:filename, /api/captions/download/:filename). Both the audio and
// video routes used to hand-roll the same allowlist + Range logic separately;
// keeping it here means a fix in one place applies to both (the Range parser in
// particular had drifted between copies).

function allowedRoots(): string[] {
  // Trailing path.sep on each root so "/uploads-evil" can't prefix-match "/uploads".
  return [UPLOADS_DIR, OUTPUT_DIR].map((d) => path.resolve(d) + path.sep);
}

/**
 * Resolve a raw path to a real file inside UPLOADS_DIR/OUTPUT_DIR, or null.
 * Same contract as resolveAllowedFilePath() in serverConfig, for callers that
 * also need the stat (avoids a second statSync round trip).
 */
export function resolveServedFile(rawPath: string): string | null {
  const resolved = path.resolve(rawPath);
  if (!allowedRoots().some((root) => resolved.startsWith(root))) return null;
  try {
    if (!fs.statSync(resolved).isFile()) return null;
  } catch {
    return null;
  }
  return resolved;
}

/**
 * Join a URL-supplied *single filename segment* safely onto dir.
 * Guards the classic `%2e%2f` encoded-traversal that defeats naive
 * `path.join(dir, req.params.filename)` — Express decodes route params, so
 * `..%2F..%2Fsecret` arrives here as `../../secret`. Returns null for
 * anything that isn't exactly one plain filename inside dir.
 */
export function safeJoinFilename(dir: string, filename: string): string | null {
  // basename alone already strips any directory component, but also verify
  // the decoded param didn't contain NULs / lone dots, then confirm the
  // resolved path still sits directly inside dir.
  const base = path.basename(filename);
  if (!base || base === "." || base === ".." || base.includes("\0")) return null;
  const resolved = path.resolve(dir, base);
  const root = path.resolve(dir) + path.sep;
  if (!resolved.startsWith(root)) return null;
  try {
    if (!fs.statSync(resolved).isFile()) return null;
  } catch {
    return null;
  }
  return resolved;
}

/**
 * Parse an HTTP Range header into a concrete {start, end} for a file of
 * `fileSize` bytes. Returns null for a syntax-invalid or unsatisfiable
 * range (caller should answer 416). Supports the open-ended (`bytes=N-`)
 * and suffix (`bytes=-N`) forms the HTML media elements actually send,
 * which the previous regex-only parser got wrong (a suffix range was
 * served from byte 0 instead of the file's tail).
 */
export function parseRange(range: string, fileSize: number): { start: number; end: number } | null {
  const m = /^bytes=(\d*)-(\d*)$/i.exec(range.trim());
  if (!m || fileSize <= 0) return null;
  const [, rawStart, rawEnd] = m;
  if (!rawStart && !rawEnd) return null; // "bytes=-" — nothing meaningful

  let start: number;
  let end: number;
  if (!rawStart) {
    // suffix range: last N bytes
    const n = parseInt(rawEnd, 10);
    if (!Number.isFinite(n) || n <= 0) return null;
    start = Math.max(0, fileSize - n);
    end = fileSize - 1;
  } else {
    start = parseInt(rawStart, 10);
    end = rawEnd ? parseInt(rawEnd, 10) : fileSize - 1;
    if (start >= fileSize || end < start) return null;
    end = Math.min(end, fileSize - 1);
  }
  return { start, end };
}

/** Stream a validated file with correct single-range handling; 416 on unsatisfiable. */
export function streamWithRange(
  req: { headers: Record<string, any> },
  res: any,
  filePath: string,
  contentType: string
): void {
  const stat = fs.statSync(filePath);
  const fileSize = stat.size;
  const range = req.headers.range as string | undefined;

  if (range) {
    const parsed = parseRange(range, fileSize);
    if (!parsed) {
      res.status(416).set("Content-Range", `bytes */${fileSize}`);
      res.end();
      return;
    }
    const { start, end } = parsed;
    res.status(206).set({
      "Content-Range": `bytes ${start}-${end}/${fileSize}`,
      "Accept-Ranges": "bytes",
      "Content-Length": String(end - start + 1),
      "Content-Type": contentType,
    });
    fs.createReadStream(filePath, { start, end }).pipe(res);
  } else {
    res.set({
      "Content-Length": String(fileSize),
      "Content-Type": contentType,
      "Accept-Ranges": "bytes",
    });
    fs.createReadStream(filePath).pipe(res);
  }
}
