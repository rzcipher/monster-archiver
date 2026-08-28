import { Router } from "express";
import path from "path";
import fs from "fs";
import crypto from "crypto";
import { runPythonHelper, computeLibraryDestination } from "../lib/pythonBridge";
import { UPLOADS_DIR, OUTPUT_DIR, resolveAllowedFilePath } from "../lib/serverConfig";
import { getLibraryConfig } from "../lib/libraryConfig";

const router = Router();

const AUDIO_MIME_TYPES: Record<string, string> = {
  ".mp3": "audio/mpeg",
  ".flac": "audio/flac",
  ".m4a": "audio/mp4",
  ".aac": "audio/aac",
  ".wav": "audio/wav",
};

// Apply Tags & Cover (with High Resolution Fallbacks)
router.post("/api/apply-tags", async (req, res) => {
  const { metadata, lyricsText, coverUrl } = req.body;

  // Constrained to UPLOADS_DIR/OUTPUT_DIR (same allowlist as /api/stream) —
  // this endpoint rewrites tags in-place, so an unconstrained path would let
  // a request modify arbitrary audio files on disk.
  const filePath = resolveAllowedFilePath(req.body?.filePath);
  if (!filePath) {
    return res.status(400).json({ error: "Valid filePath inside the uploads/output directory is required" });
  }
  if (!metadata || typeof metadata !== "object") {
    return res.status(400).json({ error: "Metadata object is required" });
  }
  // Validate the fields used to build the output filename up front — a
  // numeric/missing track or title used to blow up later as an unhelpful 500.
  if (typeof metadata.title !== "string" || !metadata.title.trim()) {
    return res.status(400).json({ error: "metadata.title must be a non-empty string" });
  }
  const trackStr = String(metadata.track ?? "").trim();
  if (!trackStr) {
    return res.status(400).json({ error: "metadata.track is required" });
  }

  let tempCoverPath = "";
  let tempCoverMime = "image/jpeg";

  try {
    // If a cover URL is provided, download it using fallback resolutions to resolve "stuck covers"
    if (coverUrl) {
      const urlsToTry = [coverUrl];
      if (coverUrl.includes("mzstatic.com") || coverUrl.includes("apple.com")) {
        const base = coverUrl.replace(/\/\d+x\d+bb\.(jpg|png)/i, "/SIZE_PLACEHOLDERbb.jpg");
        if (base !== coverUrl) {
          // 3000x3000 is Apple's own documented ceiling for artwork masters
          // (matches their podcast/album art upload spec), so it's the largest
          // size worth requesting. Previously this asked for 100000x100000 and
          // 5000x5000 first on the theory that mzstatic "doesn't upscale" and
          // would just clamp an oversized request down to the true master —
          // in practice mzstatic rejects those out-of-range requests outright
          // instead of clamping, so those two attempts always failed and just
          // burned the first two tries before ever reaching a size Apple
          // actually has, occasionally exhausting the CDN's tolerance for
          // repeat requests on the same asset before the 3000x3000 attempt
          // even ran. Smaller sizes below remain a safety net for tracks whose
          // stored master genuinely is smaller than 3000x3000.
          urlsToTry.unshift(
            base.replace("SIZE_PLACEHOLDER", "3000x3000"),
            base.replace("SIZE_PLACEHOLDER", "2000x2000"),
            base.replace("SIZE_PLACEHOLDER", "1400x1400"),
            base.replace("SIZE_PLACEHOLDER", "1000x1000")
          );
        }
      }

      for (const url of urlsToTry) {
        try {
          const controller = new AbortController();
          const timer = setTimeout(() => controller.abort(), 8000);
          const imgRes = await fetch(url, { headers: { "User-Agent": "Mozilla/5.0" }, signal: controller.signal });
          clearTimeout(timer);
          const ct = (imgRes.headers.get("content-type") || "").toLowerCase();
          // mzstatic can answer a bad request with a 200 that isn't actually
          // image bytes (an HTML error/placeholder body) — imgRes.ok alone
          // isn't proof this attempt got real artwork, so confirm the
          // content-type before accepting it and stopping the cascade.
          if (imgRes.ok && ct.startsWith("image/")) {
            const buf = await imgRes.arrayBuffer();
            // Random suffix — Date.now() alone can collide across concurrent requests.
            tempCoverPath = path.join(UPLOADS_DIR, `temp_cover_${Date.now()}_${crypto.randomBytes(4).toString("hex")}.jpg`);
            fs.writeFileSync(tempCoverPath, Buffer.from(buf));
            tempCoverMime = ct.split(";")[0].trim();
            break;
          }
        } catch (e) {
          continue;
        }
      }
    }

    // Call server_helper.py to forcefully strip covers, apply new cover, lyrics, and metadata tags
    const pythonArgs = [
      "apply_tags",
      filePath,
      JSON.stringify(metadata),
      lyricsText || "",
      tempCoverPath,
      tempCoverMime
    ];

    const result = await runPythonHelper(pythonArgs);

    // Clean up temporary cover image
    if (tempCoverPath && fs.existsSync(tempCoverPath)) {
      try { fs.unlinkSync(tempCoverPath); } catch (e) {}
    }

    // Archive the now-tagged file into the actual library, using the same
    // Artist/Year - Album (NAMING_FOLDER_TEMPLATE) structure the CLI produces —
    // see monster_archiver/naming.py. filePath already has every tag/cover/lyric
    // embedded at this point, so the library copy is the fully finalized file.
    let libraryPath: string | null = null;
    try {
      const { libraryDir } = getLibraryConfig();
      const dest = await computeLibraryDestination(
        filePath,
        {
          title: metadata.title,
          artist: metadata.artist,
          album_artist: metadata.album_artist,
          album: metadata.album,
          year: metadata.year,
          track: metadata.track,
          disc: metadata.disc,
          genre: metadata.genre,
          composer: metadata.composer,
          isrc: metadata.isrc,
        },
        libraryDir
      );
      fs.mkdirSync(dest.folder, { recursive: true });
      fs.copyFileSync(filePath, dest.path);
      libraryPath = dest.path;
    } catch (archiveErr: any) {
      // Non-fatal — the flat OUTPUT_DIR copy/download below still succeeds even if
      // library archiving fails (e.g. rezakir.py's Python deps aren't installed yet).
      console.error("Library archive failed:", archiveErr.message || archiveErr);
    }

    // Copy the finalized file into the finalized OUTPUT_DIR for easy downloading.
    // Only strip characters that are actually illegal in a filename (control
    // chars + reserved Windows/macOS/Linux symbols) rather than stripping to
    // ASCII — the old regex silently deleted Japanese/Korean/etc. titles
    // entirely, leaving a blank name like "08 - .flac".
    const ext = path.extname(filePath);
    const safeTitle = metadata.title.replace(/[\x00-\x1f<>:"/\\|?*]/g, "").trim();
    const finalFilename = `${trackStr.padStart(2, "0")} - ${safeTitle}${ext}`;
    const downloadPath = path.join(OUTPUT_DIR, finalFilename);
    fs.copyFileSync(filePath, downloadPath);

    res.json({
      status: "success",
      filename: finalFilename,
      downloadUrl: `/api/download/${encodeURIComponent(finalFilename)}`,
      // Tagging happens in-place on filePath above, so it's already the
      // finalized copy — lets the AudioPlayer's Original/Finalized toggle
      // stream it via /api/stream once compiling is done.
      path: filePath,
      libraryPath
    });

  } catch (error: any) {
    if (tempCoverPath && fs.existsSync(tempCoverPath)) {
      try { fs.unlinkSync(tempCoverPath); } catch (e) {}
    }
    res.status(500).json({ error: error.message });
  }
});

// Inline audio playback (Range-enabled, for AudioPlayer's now-playing bar) —
// unlike /api/download below, this doesn't force a Save-As. Restricted to
// UPLOADS_DIR/OUTPUT_DIR, the only two places this app ever writes a file,
// so an arbitrary ?path= can't be used to read anything else on disk.
router.get("/api/stream", (req, res) => {
  const rawPath = req.query.path;
  if (!rawPath || typeof rawPath !== "string") {
    return res.status(400).json({ error: "path query parameter is required" });
  }

  const resolved = path.resolve(rawPath);
  const allowedRoots = [UPLOADS_DIR, OUTPUT_DIR].map((d) => path.resolve(d) + path.sep);
  if (!allowedRoots.some((root) => resolved.startsWith(root))) {
    return res.status(403).json({ error: "Path is outside the allowed upload/output directories" });
  }
  if (!fs.existsSync(resolved)) {
    return res.status(404).json({ error: "File not found" });
  }

  const stat = fs.statSync(resolved);
  const fileSize = stat.size;
  const contentType = AUDIO_MIME_TYPES[path.extname(resolved).toLowerCase()] || "application/octet-stream";
  const range = req.headers.range;

  if (range) {
    const match = /^bytes=(\d*)-(\d*)$/.exec(range);
    const start = match?.[1] ? parseInt(match[1], 10) : 0;
    const end = match?.[2] ? parseInt(match[2], 10) : fileSize - 1;
    if (!match || start >= fileSize || end >= fileSize || start > end) {
      res.status(416).set("Content-Range", `bytes */${fileSize}`);
      return res.end();
    }
    res.status(206).set({
      "Content-Range": `bytes ${start}-${end}/${fileSize}`,
      "Accept-Ranges": "bytes",
      "Content-Length": String(end - start + 1),
      "Content-Type": contentType,
    });
    fs.createReadStream(resolved, { start, end }).pipe(res);
  } else {
    res.set({
      "Content-Length": String(fileSize),
      "Content-Type": contentType,
      "Accept-Ranges": "bytes",
    });
    fs.createReadStream(resolved).pipe(res);
  }
});

// Serves compiled finalized file for download
router.get("/api/download/:filename", (req, res) => {
  const filename = req.params.filename;
  const fullPath = path.join(OUTPUT_DIR, filename);
  if (!fs.existsSync(fullPath)) {
    return res.status(404).send("File not found");
  }
  res.download(fullPath, filename);
});

export default router;
