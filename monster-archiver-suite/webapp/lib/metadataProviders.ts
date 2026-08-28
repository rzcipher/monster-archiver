// Apple storefront used for both the Apple Music web search fallback and the
// iTunes Lookup enrichment calls. Region-locked releases only appear in their
// own storefront's catalog, so making this configurable (APPLE_STOREFRONT in
// .env, e.g. "gb", "jp", "de") lets users outside the US find tracks the
// hardcoded "us" storefront would silently miss.
const APPLE_STOREFRONT = (process.env.APPLE_STOREFRONT || "us").toLowerCase();

// fetch() with a hard timeout. Every upstream call in this module goes through
// this — without it, a single stalled response (Apple's web search in
// particular can hang behind some networks/proxies) stalls the whole
// Promise.allSettled and the user's search never resolves.
async function fetchWithTimeout(url: string, init: RequestInit = {}, timeoutMs = 10000): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

// MusicBrainz's fair-use guideline caps unauthenticated clients at ~1
// request/second (https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting).
// The CLI gets this for free from musicbrainzngs' built-in limiter; here we
// talk to the REST API directly, so we track the last call time ourselves and
// pace every musicbrainz.org request (search + follow-up ISRC lookups) to
// match it. Module-level on purpose — this is a single-user local server, not
// a multi-tenant service, so a simple shared clock is enough.
let mbLastCallAt = 0;
async function mbThrottle(): Promise<void> {
  const minGapMs = 1050;
  const waitFor = mbLastCallAt + minGapMs - Date.now();
  if (waitFor > 0) {
    await new Promise((resolve) => setTimeout(resolve, waitFor));
  }
  mbLastCallAt = Date.now();
}

// Per-recording ISRC lookup. A plain MusicBrainz recording *search* never
// includes ISRCs — only a direct /recording/<mbid>?inc=isrcs lookup does.
// This is the same trick the CLI's fetch_smart_metadata() uses
// (musicbrainzngs.get_recording_by_id(rec_id, includes=['isrcs'])), just
// called directly against the JSON API instead of through that library.
async function fetchMbIsrc(mbid: string): Promise<string> {
  if (!mbid) return "";
  try {
    await mbThrottle();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8000);
    const res = await fetch(`https://musicbrainz.org/ws/2/recording/${mbid}?inc=isrcs&fmt=json`, {
      headers: { "User-Agent": "MonsterArchiverWebSuite/18.4 ( https://github.com/monster-archiver-suite )" },
      signal: controller.signal,
    });
    clearTimeout(timer);
    if (!res.ok) return "";
    const data = await res.json();
    const list: string[] = data.isrcs || data["isrc-list"] || [];
    return list[0] || "";
  } catch {
    // Best-effort — a slow/failed lookup just leaves this one result's ISRC
    // blank instead of failing the whole search.
    return "";
  }
}

// Metadata lookup aggregation: iTunes + MusicBrainz concurrently, Deezer as a
// backfill whenever neither of those supplied an ISRC. Extracted from the
// /api/search-metadata route so the route file stays a thin HTTP wrapper
// around this logic.
export async function fetchMetadataCandidates(title: string, artist?: string): Promise<any[]> {
  const queryTerm = artist ? `${title} ${artist}` : `${title}`;
  const cleanTitle = String(title).replace(/\(.*?\)|\[.*?\]|\b(official|video|audio|mv)\b/gi, "").trim();

  // Concurrently fetch from iTunes Search, Apple Music Web, and MusicBrainz Search
  const [itunesRes, amRes, mbRes] = await Promise.allSettled([
    // explicit=Yes is required — Apple's Search API silently drops explicit-tagged
    // tracks from results when this param is left off (storefront-dependent; the
    // undocumented default isn't reliably "include everything"), so a search for
    // an explicit song could come back with zero matches, or only a "clean"/
    // radio-edit version if one happens to exist in the catalog.
    fetchWithTimeout(`https://itunes.apple.com/search?term=${encodeURIComponent(queryTerm)}&entity=song&limit=5&explicit=Yes&country=${APPLE_STOREFRONT}`)
      .then(r => r.json())
      .catch(() => ({ results: [] })),
    // Apple Music Web Catalog fallback (for streaming-only releases not in the
    // iTunes Store search index — a surprisingly common case for new/indie
    // releases, which is the main reason "songs that are on Apple Music don't
    // show up" here without this).
    (async () => {
      try {
        const amRes = await fetchWithTimeout(`https://music.apple.com/${APPLE_STOREFRONT}/search?term=${encodeURIComponent(queryTerm)}`, {
          headers: { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" }
        }, 12000);
        if (!amRes.ok) return { am_results: [] };
        const html = await amRes.text();
        // [\s\S] instead of `.` so an embedded newline in the JSON payload (or
        // future pretty-printing by Apple) can't silently break the match; the
        // attribute matcher is order-agnostic in case Apple reorders type/id.
        const match = html.match(/<script[^>]+id="serialized-server-data"[^>]*>([\s\S]*?)<\/script>/);
        if (!match) return { am_results: [] };
        const payload = JSON.parse(match[1]);
        // Apple has shipped this payload both as a bare array ([{intent, data}])
        // and wrapped in an object ({data: [{intent, data}]}) — accept either
        // so a serialization-shape change doesn't zero out the fallback again.
        const root = Array.isArray(payload) ? payload[0] : (payload?.data?.[0] ?? payload);
        const sections = root?.data?.sections || [];
        // Find the song/track section (id looks like "track-section - song")
        const trackSection = sections.find((s: any) => typeof s?.id === "string" && (s.id.includes("track-section") || s.id.includes("song")));
        if (!trackSection || !Array.isArray(trackSection.items)) return { am_results: [] };

        const parsed = trackSection.items.slice(0, 5).map((t: any) => {
          const rawArt = t.artwork?.dictionary?.url || "";
          const hqArt = rawArt.replace("{w}x{h}bb.{f}", "3000x3000bb.jpg");
          // The album ID is in the URL: /us/album/title/albumId?i=trackId
          const url = t.contentDescriptor?.url || "";
          // storeAdamID is Apple's canonical track id — the same id space the
          // iTunes Lookup API uses, which makes the enrichment below possible.
          const adamId = String(t.contentDescriptor?.identifiers?.storeAdamID || (url.match(/[?&]i=(\d+)/)?.[1] ?? ""));
          const albumMatch = url.match(/\/album\/([^\/]+)\/(\d+)/);
          const albumSlug = albumMatch ? albumMatch[1].replace(/-/g, " ") : "";
          // Capitalize album slug for a fallback display if enrichment misses
          const albumName = albumSlug ? albumSlug.replace(/\b\w/g, (l: string) => l.toUpperCase()) : "Unknown Album";
          const artistName = t.subtitleLinks?.[0]?.title || "Unknown";

          return {
            adamId,
            base: {
              source: "Apple Music",
              apple_id: adamId,
              title: t.title || "Unknown",
              artist: artistName,
              album_artist: artistName,
              album: albumName,
              year: "Unknown",
              track: "1",
              disc: "1",
              genre: "Unknown", // Web search JSON doesn't expose genre at this level
              composer: artistName,
              isrc: "",
              explicit: t.showExplicitBadge === true ? true : (t.showExplicitBadge === false ? false : null),
              cover: hqArt,
              release_type: "Album"
            }
          };
        });

        // ── Enrichment: iTunes Lookup by adamID ─────────────────────────────
        // The iTunes *Search* API misses streaming-only releases entirely, but
        // the *Lookup* API resolves them fine by track id — one batched call
        // upgrades every web-scraped candidate from "Unknown" year/genre/album
        // (and a slug-guessed album title) to Apple's real catalog metadata:
        // proper album name ("… - Single"), release year, genre, track/disc
        // numbers and an authoritative explicitness flag. Best-effort — if the
        // lookup fails we still return the scraped candidates as-is.
        const ids = parsed.map((p: any) => p.adamId).filter(Boolean);
        const lookupById = new Map<string, any>();
        if (ids.length) {
          try {
            const lookupRes = await fetchWithTimeout(`https://itunes.apple.com/lookup?id=${ids.join(",")}&country=${APPLE_STOREFRONT}&entity=song`, {}, 8000);
            if (lookupRes.ok) {
              const lookupData = await lookupRes.json();
              for (const r of lookupData?.results || []) {
                if (r.kind === "song" && r.trackId) lookupById.set(String(r.trackId), r);
              }
            }
          } catch {
            // best-effort — scraped candidates go out unenriched
          }
        }

        return {
          am_results: parsed.map((p: any) => {
            const d = lookupById.get(p.adamId);
            if (!d) return p.base;
            const rawArt = d.artworkUrl100 || "";
            const lookupArt = rawArt.replace(/\d+x\d+bb\.(jpg|png)/i, "3000x3000bb.$1");
            return {
              ...p.base,
              title: d.trackName || p.base.title,
              artist: d.artistName || p.base.artist,
              album_artist: d.artistName || p.base.album_artist,
              album: d.collectionName || p.base.album,
              year: d.releaseDate ? d.releaseDate.substring(0, 4) : p.base.year,
              track: String(d.trackNumber || p.base.track),
              disc: String(d.discNumber || p.base.disc),
              genre: d.primaryGenreName || p.base.genre,
              composer: d.composerName || d.artistName || p.base.composer,
              explicit: d.trackExplicitness === "explicit" ? true : (d.trackExplicitness === "notExplicit" ? false : p.base.explicit),
              cover: lookupArt || p.base.cover,
              release_type: /- single$/i.test(d.collectionName || "") ? "Single" : (/- ep$/i.test(d.collectionName || "") ? "EP" : "Album")
            };
          })
        };
      } catch (err) {
        return { am_results: [] };
      }
    })(),
    (async () => {
      await mbThrottle();
      // limit=5 caps this at parity with the iTunes call above — it also
      // bounds how many per-recording ISRC follow-up calls we'll make below,
      // since each one is a separate throttled musicbrainz.org round trip.
      // encodeURIComponent protects the URL, not Lucene syntax — an embedded
      // double-quote would still terminate the phrase server-side, so escape
      // Lucene metacharacters first.
      const luceneEscape = (v: string) => v.replace(/([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)/g, "\\$1");
      return fetchWithTimeout(`https://musicbrainz.org/ws/2/recording/?query=recording:"${encodeURIComponent(luceneEscape(cleanTitle))}"${artist ? ` AND artist:"${encodeURIComponent(luceneEscape(String(artist)))}"` : ""}&fmt=json&limit=5`, {
        headers: { "User-Agent": "MonsterArchiverWebSuite/18.4 ( https://github.com/monster-archiver-suite )" }
      }, 12000)
        .then(r => r.json())
        .catch(() => ({ recordings: [] }));
    })()
  ]);

  const results: any[] = [];

  // Process iTunes results
  if (itunesRes.status === "fulfilled" && itunesRes.value.results) {
    for (const t of itunesRes.value.results) {
      const rawArt = t.artworkUrl100 || "";
      // Substitute 3000x3000bb.jpg in the iTunes artwork URL pattern — 3000x3000
      // is Apple's own documented ceiling for artwork masters, so it's the
      // largest size worth asking for. Asking for something absurd like
      // 100000x100000 doesn't get you more: mzstatic doesn't clamp oversized
      // requests down to the true master the way earlier comments here assumed
      // — it just fails them outright, which was silently knocking the whole
      // cascade in routes/tags.ts down to a smaller fallback size instead of
      // the 3000x3000 Apple actually had on file (same trick the CLI uses in
      // monster_archiver/metadata.py, which has the same bug unfixed there).
      // MetadataPanel's <img onError> still cascades to smaller sizes as a backstop
      // for the rare case even 3000x3000 fails to load.
      const hqArt = rawArt.replace(/\d+x\d+bb\.(jpg|png)/i, "3000x3000bb.$1");
      results.push({
        source: "iTunes",
        // Same id space as the Apple Music web fallback's storeAdamID — used
        // below to drop duplicate candidates for tracks both paths can see.
        apple_id: t.trackId ? String(t.trackId) : "",
        title: t.trackName || "Unknown",
        artist: t.artistName || "Unknown",
        // iTunes' Search API doesn't expose a distinct collection/album artist
        // for music tracks — artistName is the closest available value.
        album_artist: t.artistName || "Unknown",
        album: t.collectionName || "Unknown Album",
        year: t.releaseDate ? t.releaseDate.substring(0, 4) : "Unknown",
        track: String(t.trackNumber || "1"),
        disc: String(t.discNumber || "1"),
        genre: t.primaryGenreName || "Unknown",
        composer: t.composerName || t.artistName || "Unknown",
        // iTunes Search API doesn't expose ISRC.
        isrc: "",
        explicit: t.trackExplicitness === "explicit" ? true : (t.trackExplicitness === "notExplicit" ? false : null),
        cover: hqArt,
        release_type: "Album"
      });
    }
  }

  // Process Apple Music Web fallback results. When iTunes Search *did* find a
  // track, the web fallback usually finds the same one — dedupe on Apple's
  // canonical track id so the picker isn't cluttered with identical entries,
  // while still keeping every streaming-only track iTunes couldn't see.
  if (amRes.status === "fulfilled" && Array.isArray((amRes.value as any).am_results)) {
    const seenAppleIds = new Set(results.map(r => r.apple_id).filter(Boolean));
    for (const cand of (amRes.value as any).am_results) {
      if (cand.apple_id && seenAppleIds.has(cand.apple_id)) continue;
      if (cand.apple_id) seenAppleIds.add(cand.apple_id);
      results.push(cand);
    }
  }

  // Process MusicBrainz results
  if (mbRes.status === "fulfilled" && mbRes.value.recordings) {
    for (const rec of mbRes.value.recordings) {
      const release = rec.releases?.[0] || {};
      const releaseMbid = release.id || "";
      const coverUrl = releaseMbid ? `https://coverartarchive.org/release/${releaseMbid}/front` : "";

      const mbArtist = rec["artist-credit"]?.[0]?.name || "Unknown";
      // The release's own artist-credit (when present) reflects a compilation's
      // billed album artist, which can differ from the recording's own credit
      // (e.g. a variety-artist compilation vs. this one track's performer).
      const mbAlbumArtist = release["artist-credit"]?.[0]?.name || mbArtist;

      // A plain recording search (no `inc=isrcs`) doesn't return ISRCs — do
      // the same follow-up per-recording lookup the CLI does, throttled to
      // MusicBrainz's rate-limit guideline via mbThrottle(). Sequential
      // (await inside this for-loop, not Promise.all) so the throttle can't
      // race between concurrent calls.
      const isrc = await fetchMbIsrc(rec.id || "");

      results.push({
        source: "MusicBrainz",
        title: rec.title || "Unknown",
        artist: mbArtist,
        album_artist: mbAlbumArtist,
        album: release.title || "Unknown Album",
        year: release.date ? release.date.substring(0, 4) : "Unknown",
        track: String(release["medium-list"]?.[0]?.["track-list"]?.[0]?.number || "1"),
        disc: String(release["medium-list"]?.[0]?.position || "1"),
        genre: rec.tags?.[0]?.name?.toUpperCase() || "Unknown",
        composer: mbArtist,
        isrc,
        explicit: null,
        cover: coverUrl,
        release_type: release["release-group"]?.type || "Album"
      });
    }
  }

  // Add a Deezer lookup whenever nothing found so far carries an ISRC.
  // iTunes' Search API never exposes an ISRC at all (see comment above), and
  // MusicBrainz only has one when the recording happens to be catalogued
  // with one — so gating this on "zero results total" left it effectively
  // dead, since iTunes alone almost always returns *something*, even with a
  // blank ISRC. That meant this block essentially never ran for any query
  // iTunes could match at all. (Deezer needs its own follow-up call to
  // actually surface an ISRC too — see comment further down.)
  if (!results.some(r => r.isrc)) {
    try {
      const dzRes = await fetchWithTimeout(`https://api.deezer.com/search?q=${encodeURIComponent(queryTerm)}&limit=3`, {}, 8000).then(r => r.json());
      if (dzRes && dzRes.data) {
        // Deezer's /search listing is a simplified track object — isrc (like
        // track_position, disk_number, bpm and release_date) only reliably
        // populates on the full per-track resource, not the search list. A
        // follow-up /track/<id> call per candidate is needed to actually get
        // an ISRC back, the same way MusicBrainz's recording search needs a
        // follow-up per-recording lookup above. Deezer's rate limit (~50
        // req/5s) is generous enough that these can run concurrently rather
        // than needing mbThrottle()-style pacing.
        const detailed = await Promise.all(dzRes.data.map(async (t: any) => {
          try {
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), 6000);
            const detailRes = await fetch(`https://api.deezer.com/track/${t.id}`, { signal: controller.signal });
            clearTimeout(timer);
            if (detailRes.ok) {
              const detail = await detailRes.json();
              return { ...t, isrc: detail.isrc || t.isrc || "" };
            }
          } catch {
            // best-effort — fall through to the plain search-result object,
            // which just means this one candidate keeps a blank ISRC
          }
          return t;
        }));

        for (const t of detailed) {
          results.push({
            source: "Deezer",
            title: t.title || "Unknown",
            artist: t.artist?.name || "Unknown",
            album_artist: t.artist?.name || "Unknown",
            album: t.album?.title || "Unknown Album",
            year: "Unknown",
            track: String(t.track_position || "1"),
            disc: String(t.disk_number || "1"),
            genre: "Unknown",
            composer: t.artist?.name || "Unknown",
            isrc: t.isrc || "",
            explicit: t.explicit_lyrics === true ? true : (t.explicit_lyrics === false ? false : null),
            cover: t.album?.cover_xl || "",
            release_type: "Album"
          });
        }
      }
    } catch (dzErr) {
      // best-effort fallback — swallow and return whatever we have (nothing)
    }
  }

  return results;
}
