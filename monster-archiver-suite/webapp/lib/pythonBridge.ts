import path from "path";
import fs from "fs";
import os from "os";
import { execFile, spawnSync } from "child_process";

// Resolve which Python launcher actually works on this machine. Most
// Windows python.org installs only register python.exe, not python3.exe —
// and when python3 isn't really installed, Windows' App Execution Alias for
// it can fail silently (or pop open the Microsoft Store) instead of erroring
// cleanly, which made every server_helper.py call fail with a confusing error.
// Linux and most macOS setups are the opposite: python3 exists, plain
// python often doesn't. Try the platform's usual name first, fall back to
// the other, and cache whichever one actually runs.
function resolvePythonCommand(): string {
  const candidates = process.platform === "win32" ? ["python", "python3"] : ["python3", "python"];
  for (const cmd of candidates) {
    try {
      const result = spawnSync(cmd, ["--version"], { stdio: "ignore", timeout: 5000 });
      if (!result.error && result.status === 0) return cmd;
    } catch {
      // try the next candidate
    }
  }
  // Nothing worked — fall back to the platform's usual name so the error that
  // follows points at the binary the user actually needs to install.
  return process.platform === "win32" ? "python" : "python3";
}

export const PYTHON_CMD = resolvePythonCommand();

// Helper to run server_helper.py (the lightweight librosa/mutagen-only side)
export function runPythonHelper(args: string[]): Promise<any> {
  return new Promise((resolve, reject) => {
    const helperPath = path.join(process.cwd(), "server_helper.py");
    execFile(PYTHON_CMD, [helperPath, ...args], { maxBuffer: 1024 * 1024 * 50 }, (error, stdout, stderr) => {
      if (error) {
        reject(new Error(`Python execution error: ${error.message}. Stderr: ${stderr}`));
        return;
      }
      try {
        const parsed = JSON.parse(stdout.trim());
        resolve(parsed);
      } catch (e) {
        reject(new Error(`Failed to parse python output: ${stdout}. Error: ${e}`));
      }
    });
  });
}

// ---------------- AI transcription (Demucs + Faster-Whisper via rezakir.py) ----------------
// When no lyrics exist in any database, rezakir.py falls back to local AI
// transcription. Rather than re-implementing that multi-pass Whisper/Demucs
// pipeline here, this shells out to rezakir.py's own --webui-transcribe mode
// (see monster_archiver/cli.py) so the web GUI and CLI share the exact same
// tuned transcription logic — no duplicated, potentially-drifting behavior.
const REZAKIR_PATH = path.join(process.cwd(), "..", "rezakir.py");

export function execRezakir(args: string[]): Promise<{ stdout: string; stderr: string }> {
  return new Promise((resolve) => {
    // No timeout — Demucs + multi-pass Whisper on a full song can legitimately
    // take minutes. Resolve regardless of exit code: rezakir.py's JSON-emitting
    // modes always print {"error": ...} on failure rather than relying on the
    // exit code alone.
    execFile(PYTHON_CMD, [REZAKIR_PATH, ...args], { maxBuffer: 1024 * 1024 * 200, timeout: 0 }, (_error, stdout, stderr) => {
      resolve({ stdout, stderr });
    });
  });
}

// Shared by every rezakir.py JSON-mode caller (transcribe, library scan/merge):
// a first-ever run self-installs the heavy ML deps (torch/demucs/faster-whisper)
// and exits with empty stdout — same "run it again" bootstrap the CLI has
// always done. Retry once automatically so the user doesn't have to click twice.
export async function execRezakirJson(args: string[]): Promise<any> {
  let { stdout, stderr } = await execRezakir(args);

  if (!stdout.trim()) {
    ({ stdout, stderr } = await execRezakir(args));
  }

  if (!stdout.trim()) {
    throw new Error(`rezakir.py produced no output (dependency install may have failed). ${stderr.slice(-500)}`);
  }

  try {
    return JSON.parse(stdout.trim());
  } catch (e) {
    throw new Error(`Failed to parse rezakir.py output: ${stdout.slice(0, 300)}`);
  }
}

export async function transcribeWithRezakir(
  filePath: string,
  opts: { title?: string; artist?: string; genre?: string; lang?: string }
): Promise<string> {
  const args = ["--webui-transcribe", filePath, "--lang", opts.lang || "auto"];
  if (opts.title) args.push("--webui-title", opts.title);
  if (opts.artist) args.push("--webui-artist", opts.artist);
  if (opts.genre) args.push("--webui-genre", opts.genre);

  const parsed = await execRezakirJson(args);
  if (parsed.error) {
    throw new Error(parsed.error);
  }
  return parsed.lrc;
}

// ---------------- Video captions (audio extract + Faster-Whisper + pyannote diarization via rezakir.py) ----------------
// Two-phase, mirroring rezakir.py's own design (see monster_archiver/video_captions.py):
//   1. transcribeVideoCaptions() -- extract audio, transcribe, diarize, merge into
//      {start,end,text,speaker} segments for the web UI to review/correct. Nothing
//      is burned into the video yet.
//   2. burnVideoCaptions() -- takes the *corrected* segments back and burns them in.
export interface CaptionSegment {
  start: number;
  end: number;
  text: string;
  speaker: string;
}

export interface CaptionTranscribeResult {
  segments: CaptionSegment[];
  speakers: string[];
  audioDuration: number;
}

export async function transcribeVideoCaptions(
  filePath: string,
  opts: { lang?: string; title?: string; artist?: string; genre?: string }
): Promise<CaptionTranscribeResult> {
  const args = ["--webui-video-caption", filePath, "--lang", opts.lang || "auto"];
  if (opts.title) args.push("--webui-title", opts.title);
  if (opts.artist) args.push("--webui-artist", opts.artist);
  if (opts.genre) args.push("--webui-genre", opts.genre);

  const parsed = await execRezakirJson(args);
  if (parsed.error) {
    throw new Error(parsed.error);
  }
  return parsed;
}

export async function burnVideoCaptions(
  filePath: string,
  segments: CaptionSegment[],
  opts: { fontSize?: number } = {}
): Promise<string> {
  // A full video transcript can exceed the Windows command-line length limit,
  // so segments go through a temp file rather than an inline argument -- same
  // reasoning as cli.py's --webui-segments-file (see its --help text).
  const tmpPath = path.join(os.tmpdir(), `monster-archiver-captions_${Date.now()}_${process.pid}.json`);
  fs.writeFileSync(tmpPath, JSON.stringify(segments), "utf-8");
  try {
    const args = ["--webui-burn-subtitles", filePath, "--webui-segments-file", tmpPath];
    if (opts.fontSize) args.push("--webui-font-size", String(Math.round(opts.fontSize)));
    const parsed = await execRezakirJson(args);
    if (parsed.error) {
      throw new Error(parsed.error);
    }
    return parsed.outputPath;
  } finally {
    try {
      fs.unlinkSync(tmpPath);
    } catch {
      // best-effort cleanup -- a leftover temp JSON file isn't worth failing the request over
    }
  }
}

// ---------------- Library destination (Artist/Year - Album naming) ----------------
// Computes the same NAMING_FOLDER_TEMPLATE/NAMING_FILENAME_TEMPLATE destination
// the CLI would use for this track (see monster_archiver/naming.py), so the web
// GUI's "Compile" step archives into the library with the exact folder structure
// the CLI produces, instead of a flat unorganized dump. Only computes the path —
// it doesn't copy/move filePath itself, since Node already owns that file I/O
// (see routes/tags.ts).
export interface LibraryDestination {
  folder: string;
  path: string;
  primaryArtist: string;
}

export async function computeLibraryDestination(
  filePath: string,
  metadata: { title?: string; artist?: string; album_artist?: string; album?: string; year?: string; track?: string; disc?: string; genre?: string; composer?: string; isrc?: string },
  libraryDir: string
): Promise<LibraryDestination> {
  const args = [
    "--webui-archive", filePath,
    "--webui-metadata", JSON.stringify(metadata),
    "--library-dir", libraryDir,
  ];
  const parsed = await execRezakirJson(args);
  if (parsed.error) {
    throw new Error(parsed.error);
  }
  return { folder: parsed.folder, path: parsed.path, primaryArtist: parsed.primary_artist };
}

// Self-install the Python side (librosa, mutagen, numpy, requests, soundfile) the
// same way rezakir.py's ensure_dependencies() does, so a first run needs nothing
// but Node + Python installed. Runs once at startup with output streamed straight
// to the console (not JSON — this isn't a request/response call).
export function ensurePythonDeps() {
  const helperPath = path.join(process.cwd(), "server_helper.py");
  console.log(`Checking Python dependencies via "${PYTHON_CMD}" (librosa, mutagen, numpy, requests, soundfile)...`);
  const result = spawnSync(PYTHON_CMD, [helperPath, "ensure_deps"], { stdio: "inherit" });
  if (result.error || result.status !== 0) {
    console.error(
      `\n[WARNING] Automatic Python dependency install failed (tried to run "${PYTHON_CMD}"). ` +
      `Install manually with: ${PYTHON_CMD} -m pip install librosa mutagen numpy requests soundfile\n` +
      `If this is Windows and neither "python" nor "python3" work from Command Prompt, ` +
      `reinstall Python from https://python.org and make sure "Add python.exe to PATH" is checked.\n`
    );
  }
}
