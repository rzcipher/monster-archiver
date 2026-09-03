import express from "express";
import path from "path";
import { execFile } from "child_process";
import { createServer as createViteServer } from "vite";
import dotenv from "dotenv";

import { PORT, HOST } from "./lib/serverConfig";
import { ensurePythonDeps } from "./lib/pythonBridge";

import uploadRoute from "./routes/upload";
import analyzeRoute from "./routes/analyze";
import metadataRoute from "./routes/metadata";
import lyricsRoute from "./routes/lyrics";
import tagsRoute from "./routes/tags";
import libraryRoute from "./routes/library";
import settingsRoute from "./routes/settings";
import activityRoute from "./routes/activity";
import captionsRoute from "./routes/captions";

dotenv.config();

const app = express();

// ── CSRF / cross-site request guard ─────────────────────────────────────────
// The server binds to 127.0.0.1, but any webpage open in the user's browser
// can still fire a "simple" cross-origin request (form POST / text-plain
// fetch, which skip the CORS preflight) at http://127.0.0.1:PORT. Several
// endpoints here are state-changing (library merge, scan --fix, activity
// revert, tag rewrites), so a drive-by page could reorganize the user's music
// library. Defense in depth, applied to every non-GET /api request:
//  1. If an Origin header is present it must be one of our own origins.
//     Browsers always attach Origin to cross-origin POSTs, so a forged
//     request from another site can't pass this check.
//  2. Sec-Fetch-Site (sent by all modern browsers) must be same-origin /
//     same-site / none when present.
//  3. Content-Type must be JSON or multipart — urlencoded/text-plain bodies
//     (the no-preflight CSRF vectors) are rejected outright.
// Non-browser clients (curl, scripts) send none of these headers and are
// unaffected by 1–2; they just need a JSON content-type for bodied requests.
//
// ALLOWED_ORIGINS (comma-separated, in .env) extends the self-origin set for
// deliberate LAN/proxy setups — e.g. HOST=0.0.0.0 with a reverse proxy or a
// different machine's address, where the browser's Origin header wouldn't
// match any of the localhost forms below. Unset by default, so the stock
// localhost-only setup keeps the strict check.
const SELF_ORIGINS = new Set(
  ["127.0.0.1", "localhost", "[::1]", HOST].flatMap((h) => [
    `http://${h}:${PORT}`,
    `https://${h}:${PORT}`,
  ])
);
// The literal value "*" disables the Origin check entirely (the sec-fetch-site
// and content-type layers below still apply) — for setups where the app is
// fronted by a proxy under an unpredictable host, and for this repo's agent
// previews. Still opt-in; unset/default stays strict.
const ALLOW_ALL_ORIGINS = (process.env.ALLOWED_ORIGINS || "").trim() === "*";
for (const extra of (process.env.ALLOWED_ORIGINS || "").split(",")) {
  const o = extra.trim().replace(/\/$/, "");
  if (o && o !== "*") SELF_ORIGINS.add(o);
}
// Any host is fine as long as it's this server's own port — that's the
// defining trait of a same-origin request. Without this, following the
// documented HOST=0.0.0.0 LAN setup would break every POST, since the
// browser's Origin (http://192.168.x.x:3000) can't be enumerated ahead of
// time. A drive-by page on some other origin still fails this check (its
// Origin carries its own host:port), and the sec-fetch-site/content-type
// layers below remain the primary CSRF defense either way.
// (The ":PORT" is omitted by browsers on a scheme's default port, so accept
// http://host and https://host bare when the app itself runs on 80/443.)
const _portPart = PORT === 80 || PORT === 443 ? `(?::${PORT})?` : `:${PORT}`;
const SELF_PORT_RE = new RegExp(`^https?://[^/:]+${_portPart}$`);

app.use((req, res, next) => {
  if (!req.path.startsWith("/api/")) return next();
  const method = req.method.toUpperCase();
  if (method === "GET" || method === "HEAD" || method === "OPTIONS") return next();

  const origin = req.headers.origin;
  if (!ALLOW_ALL_ORIGINS && origin && !SELF_ORIGINS.has(origin) && !SELF_PORT_RE.test(origin)) {
    return res.status(403).json({ error: "Cross-origin requests are not allowed" });
  }

  const secFetchSite = req.headers["sec-fetch-site"];
  if (typeof secFetchSite === "string" && !["same-origin", "same-site", "none"].includes(secFetchSite)) {
    return res.status(403).json({ error: "Cross-site requests are not allowed" });
  }

  const contentType = (req.headers["content-type"] || "").toLowerCase();
  const hasBody = req.headers["content-length"] !== undefined || req.headers["transfer-encoding"] !== undefined;
  if (hasBody && contentType && !contentType.startsWith("application/json") && !contentType.startsWith("multipart/form-data")) {
    return res.status(415).json({ error: "Content-Type must be application/json or multipart/form-data" });
  }

  return next();
});

app.use(express.json({ limit: "50mb" }));
// NOTE: no express.urlencoded() — urlencoded bodies are a classic
// no-CORS-preflight CSRF vector and nothing in this app sends them.

app.use(uploadRoute);
app.use(analyzeRoute);
app.use(metadataRoute);
app.use(lyricsRoute);
app.use(tagsRoute);
app.use(libraryRoute);
app.use(settingsRoute);
app.use(activityRoute);
app.use(captionsRoute);

app.use((err: any, req: express.Request, res: express.Response, next: express.NextFunction) => {
  // Malformed JSON bodies arrive here as body-parser SyntaxErrors with a
  // 400-ish status — answer 400 with a short message instead of logging a
  // stack and echoing parser internals as a 500.
  if (err?.type === "entity.parse.failed") {
    return res.status(400).json({ error: "Request body is not valid JSON" });
  }
  // multer's size cap surfaces as a MulterError, not a thrown HTTP status.
  if (err?.code === "LIMIT_FILE_SIZE") {
    return res.status(413).json({ error: "File exceeds the maximum upload size" });
  }
  console.error("Express Error:", err);
  res.status(err.status || 500).json({ error: err.message || "Internal Server Error" });
});

function openBrowser(url: string) {
  // execFile reports spawn failures (e.g. no xdg-open on a headless box) via an
  // async "error" event, not a thrown exception — an unhandled one would crash
  // the server, so it's caught here rather than with try/catch.
  const child =
    process.platform === "win32"
      ? execFile("cmd", ["/c", "start", "", url])
      : execFile(process.platform === "darwin" ? "open" : "xdg-open", [url]);
  child.on("error", () => {
    // Non-fatal — just means the user opens the URL themselves.
  });
}

// Serve static compiled files in production
async function startServer() {
  ensurePythonDeps();

  if (process.env.NODE_ENV !== "production") {
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: "spa"
    });
    app.use(vite.middlewares);
  } else {
    const distPath = path.join(process.cwd(), "dist");
    app.use(express.static(distPath));
    app.get("*all", (req, res) => {
      res.sendFile(path.join(distPath, "index.html"));
    });
  }

  const server = app.listen(PORT, HOST, () => {
    const url = `http://${HOST}:${PORT}`;
    console.log(`\nMonster Archiver Suite running at ${url}\n`);
    if (!process.env.NO_BROWSER) {
      openBrowser(url);
    }
  });
  server.on("error", (err: NodeJS.ErrnoException) => {
    // The friendly-launcher case: double-clicking the run script while the
    // app is already up used to die with a raw EADDRINUSE stack trace from an
    // unhandled 'error' event. Explain, and still open the running instance.
    if (err.code === "EADDRINUSE") {
      const url = `http://${HOST === "0.0.0.0" ? "127.0.0.1" : HOST}:${PORT}`;
      console.log(
        `\nPort ${PORT} is already in use — Monster Archiver appears to be\n` +
        `already running at ${url}. Opening it in your browser instead of\n` +
        `starting a second copy.\n`
      );
      if (!process.env.NO_BROWSER) openBrowser(url);
      process.exit(0);
    }
    console.error(`Failed to start server on ${HOST}:${PORT}: ${err.message}`);
    process.exit(1);
  });
}

startServer();
