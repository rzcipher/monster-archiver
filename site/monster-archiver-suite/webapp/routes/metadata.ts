import { Router } from "express";
import { fetchMetadataCandidates } from "../lib/metadataProviders";

const router = Router();

// Proxy Metadata Query to iTunes + MusicBrainz + Deezer fallback
router.get("/api/search-metadata", async (req, res) => {
  const { title, artist } = req.query;
  if (!title) {
    return res.status(400).json({ error: "Title parameter is required" });
  }
  try {
    const results = await fetchMetadataCandidates(String(title), artist ? String(artist) : undefined);
    res.json(results);
  } catch (error: any) {
    res.status(500).json({ error: error.message });
  }
});

// LRCLIB requests get a hard timeout via AbortController — without one, a
// slow/stalled response from lrclib.net hangs the request indefinitely,
// which is what made lyric scans feel "stuck" rather than just failing fast.
async function fetchLrclib(url: string, timeoutMs = 15000): Promise<Response | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      headers: { "User-Agent": "MonsterArchiverWebSuite" },
      signal: controller.signal,
    });
    return res.ok ? res : null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

// Fetch Synced/Plain Lyrics from LRCLIB
router.get("/api/fetch-lyrics", async (req, res) => {
  const { title, artist } = req.query;
  if (!title) {
    return res.status(400).json({ error: "Title parameter is required" });
  }
  try {
    const q = artist ? `${title} ${artist}` : `${title}`;
    const searchRes = await fetchLrclib(`https://lrclib.net/api/search?q=${encodeURIComponent(String(q))}`);
    if (!searchRes) {
      return res.json([]);
    }
    const data = await searchRes.json();
    if (!Array.isArray(data)) {
      return res.json([]);
    }

    // /api/search often returns candidates with an empty syncedLyrics/
    // plainLyrics body even when the track genuinely has lyrics on file —
    // only /api/get/<id> is guaranteed to include the full text (same issue
    // already fixed on the CLI side, see monster_archiver/lyrics.py). Resolve
    // the top candidates' missing bodies in parallel so the extra round trips
    // don't turn into a slow serial wait.
    const candidates = data.slice(0, 6);
    const resolved = await Promise.all(candidates.map(async (t: any) => {
      let synced = t.syncedLyrics || "";
      let plain = t.plainLyrics || "";

      if (!synced && !plain && t.id) {
        const idRes = await fetchLrclib(`https://lrclib.net/api/get/${t.id}`);
        if (idRes) {
          const idData = await idRes.json();
          synced = idData.syncedLyrics || "";
          plain = idData.plainLyrics || "";
        }
      }

      return {
        source: "LRCLIB",
        id: t.id,
        title: t.trackName || t.name || "Unknown",
        artist: t.artistName,
        synced,
        plain,
        duration: t.duration
      };
    }));

    res.json(resolved.filter((t) => t.synced || t.plain).slice(0, 5));
  } catch (error: any) {
    res.json([]);
  }
});

export default router;
