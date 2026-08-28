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
const SELF_ORIGINS = new Set(
  ["127.0.0.1", "localhost", "[::1]", HOST].flatMap((h) => [
    `http://${h}:${PORT}`,
    `https://${h}:${PORT}`,
  ])
);

app.use((req, res, next) => {
  if (!req.path.startsWith("/api/")) return next();
  const method = req.method.toUpperCase();
  if (method === "GET" || method === "HEAD" || method === "OPTIONS") return next();

  // Origin check, proxy-aware: the hardcoded SELF_ORIGINS list broke inside
  // preview/proxy environments (AI Studio, cloud sandboxes) where the browser
  // origin is some generated hostname rather than 127.0.0.1:PORT. Instead of
  // disabling the check entirely (which re-opens the drive-by CSRF hole this
  // guard exists to close), also accept any Origin whose host matches the
  // request's own Host header — i.e. the page was served by *this* server,
  // whatever name it's being reached through. Foreign origins still get 403.
  const origin = req.headers.origin;
  if (origin && !SELF_ORIGINS.has(origin)) {
    let originHost = "";
    try {
      originHost = new URL(origin).host;
    } catch {
      // unparsable Origin header — treat as foreign
    }
    const requestHost = String(req.headers["x-forwarded-host"] || req.headers.host || "");
    if (!originHost || originHost !== requestHost) {
      return res.status(403).json({ error: "Cross-origin requests are not allowed" });
    }
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
      // allowedHosts: true — Vite's dev middleware otherwise rejects any
      // request whose Host header isn't localhost-ish ("Blocked request. This
      // host is not allowed."), which breaks the whole app behind preview
      // proxies / cloud sandboxes / LAN hostnames. DNS-rebinding risk (the
      // reason the check exists) doesn't apply here: the express layer in
      // front already guards state-changing /api calls, and dev assets served
      // by Vite carry no secrets.
      server: { middlewareMode: true, allowedHosts: true },
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

  app.listen(PORT, HOST, () => {
    // 0.0.0.0 / :: are *bind* addresses (listen on all interfaces), not
    // browsable URLs — opening http://0.0.0.0:PORT fails on Windows/Chrome
    // with ERR_ADDRESS_INVALID. Always hand the browser a loopback address.
    const browseHost = HOST === "0.0.0.0" || HOST === "::" ? "127.0.0.1" : HOST;
    const url = `http://${browseHost}:${PORT}`;
    console.log(`\nMonster Archiver Suite running at ${url}\n`);
    if (!process.env.NO_BROWSER) {
      openBrowser(url);
    }
  });
}

startServer();
