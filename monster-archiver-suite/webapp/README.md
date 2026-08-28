# Monster Archiver Suite (local web app)

A drag-and-drop GUI for tagging a single audio file: spectral/lossless integrity scan, BPM & key detection, metadata search (iTunes/MusicBrainz/Deezer), lyrics fetch (LRCLIB), AI transcription when no lyrics exist anywhere, AI lyric translation, and cover/tag writing — all served from your own machine.

This is the same engine as [`rezakir.py`](../rezakir.py) wrapped in a browser UI for one-file-at-a-time work. For bulk-archiving a whole library (fingerprinting, Whisper transcription, Demucs stem separation, automatic file organisation), use `rezakir.py` directly instead — see the [root README](../README.md).

**Fully local, nothing hosted:** translation runs through a locally-running [Ollama](https://ollama.com) model, not a cloud AI service, and the server binds to `127.0.0.1` (your machine only) by default. Metadata/lyrics lookups (iTunes, MusicBrainz, Deezer, LRCLIB) still go out over the internet to those free public databases — same as `rezakir.py` and any music tagger — but no lyrics or audio ever leaves your machine for translation, and nothing is deployed or hosted anywhere.

## Requirements

- Node.js 18+
- Python 3.10 or 3.11
- (For AI lyric translation) [Ollama](https://ollama.com), installed separately

## Run it

From this `webapp/` folder:

```
npm install
npm run dev
```

- `npm install` pulls in the Node/React/Express dependencies once.
- `npm run dev` starts the app, self-installs its Python dependencies (librosa, mutagen, numpy, requests, soundfile) on first run, and opens `http://127.0.0.1:3000` in your browser automatically.

Or use the double-click launcher from the repo root instead of typing any of this: `run_webapp_windows.bat` / `run_webapp_mac_linux.sh`. It checks for Node/Python, runs `npm install` for you the first time, and starts the app.

## Local AI translation via Ollama

1. Install Ollama from https://ollama.com and make sure it's running (`ollama serve`, or just have the Ollama desktop app open).
2. Pull a model: `ollama pull gemma4:12b` (or any other Ollama chat model).
3. In the app's **AI Lyrics Studio** tab, click **Apply Local Translation**.

To use a different model or port, copy `.env.example` to `.env` and edit it — see that file for `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `PORT`, and `HOST`.

Translation uses rezakir.py's exact tuned settings: the same custom emotional-fidelity/no-censorship system prompt, thinking disabled (`reasoning_effort="none"`), and a tone pre-read pass (the model first summarizes the song's mood from a sample of lines, then that summary anchors the full translation) — same two-call sequence as the CLI's method 5.

## AI transcription when no lyrics exist anywhere

If LRCLIB and the other lyrics sources come back empty, click **Transcribe with AI** in the **AI Lyrics Studio** tab. This isolates vocals with Demucs and transcribes them with Faster-Whisper — the exact same fallback `rezakir.py` uses, reused via a small `--webui-transcribe` subprocess mode rather than reimplemented, so both stay in sync.

- Fully local — no cloud speech-to-text, same as everything else here.
- Heavy: installs its own large ML dependencies (PyTorch, Demucs, Faster-Whisper) on first use, separate from the lightweight `librosa`/`mutagen` deps `server_helper.py` needs for tagging. Expect the first click to take a while; the app retries automatically once the install finishes.
- Slow: Demucs + multi-pass Whisper on a full song can take a few minutes, especially on CPU-only machines.
- The transcribed lyrics land untranslated in the editor — run **Apply Local Translation** afterward if you want them translated too.

## Troubleshooting: "Python dependency install failed" on Windows

The Node backend needs to run Python as a subprocess. It tries `python` first on Windows (`python3` first everywhere else) and falls back to the other name automatically — but if *neither* works, check:

1. Open Command Prompt and run `python --version`. If that works but `python3 --version` doesn't (or vice versa), that confirms which one is actually installed — the app should already pick the working one on its own.
2. If neither command works at all, reinstall Python from [python.org](https://python.org) and make sure **"Add python.exe to PATH"** is checked during setup.
3. Optional cleanup: Windows ships stub "App execution aliases" for `python.exe`/`python3.exe` that silently open the Microsoft Store when no real Python is behind them, instead of failing with a clear error. If you keep hitting confusing failures, go to **Settings → Apps → Advanced app settings → App execution aliases** and turn those two toggles off — future typos will then fail loudly instead of silently popping open the Store.

## Notes

- Uploaded files land in `uploads/`, finalized/tagged files in `output/` (both git-ignored).
- `server_helper.py` does the actual audio analysis and tag writing (mutagen/librosa) — `server.ts` just orchestrates it and proxies metadata/lyrics lookups.
