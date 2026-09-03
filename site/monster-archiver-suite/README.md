# Archive-monster
Reza

Monster Archiver is an audio archiver: it fingerprints audio files, fetches metadata/lyrics from MusicBrainz, iTunes, and Deezer, transcribes vocals with Whisper (+Demucs stem separation), translates lyrics, and writes fully-tagged FLAC/MP3/M4A files into an organised library.

There are two ways to run it, both fully local (no cloud AI service, no hosting — see the Ollama section below):

- **`rezakir.py`** — the full CLI/TUI engine. Best for batch-archiving a whole library: fingerprinting, Whisper transcription, Demucs stem separation, automatic file organisation.
- **`webapp/`** — a browser GUI for working on one file at a time: drag-and-drop upload, spectral/lossless integrity scan, BPM & key detection, metadata search, lyrics fetch, AI translation, and tag/cover editing. See [`webapp/README.md`](webapp/README.md) for details.

## Requirements

- Python 3.10 or 3.11
- Node.js 18+ (only needed for the `webapp/` GUI)
- (Optional, for local/offline lyric translation) [Ollama](https://ollama.com), installed separately

Everything else (ffmpeg, Whisper, Demucs, the web app's Python/Node packages, etc.) is installed automatically the first time you run either app.

## Running it as an application (no terminal typing needed)

1. Install Python from [python.org](https://python.org) if you don't have it (on Windows, tick **"Add python.exe to PATH"** during install). Install [Node.js](https://nodejs.org) too if you want the GUI.
2. Double-click:
   - **CLI engine — Windows:** `run_windows.bat` · **macOS/Linux:** `run_mac_linux.sh`
   - **Web GUI — Windows:** `run_webapp_windows.bat` · **macOS/Linux:** `run_webapp_mac_linux.sh`
   - (First time only on macOS/Linux, right-click → *Open*, or run `chmod +x run_mac_linux.sh run_webapp_mac_linux.sh` once in a terminal so they're double-clickable afterwards.)
3. The CLI's first launch installs its own dependencies and then exits — just double-click the same launcher again to start the app for real. The web GUI installs everything (Node and Python packages) and opens your browser automatically, no second launch needed.
4. From then on, double-clicking a launcher opens the app — pick your translation engine, source language, and drop in a folder or file path (CLI), or drag-and-drop a track in the browser (GUI). No coding involved.

(You can also run the CLI directly with `python rezakir.py` from a terminal if you prefer command-line flags like `--watch`, `--engine`, `--scan`, etc. — run `python rezakir.py --help` to see all of them.)

## Local AI translation via Ollama (no cloud, no rate limits)

Monster Archiver can translate lyrics with a locally-running LLM through [Ollama](https://ollama.com) instead of a cloud translator — everything stays on your machine and there's no internet requirement or API rate limit.

1. Install Ollama from https://ollama.com and make sure it's running (`ollama serve`, or just have the Ollama desktop app open).
2. Pull a model, e.g.:
   ```
   ollama pull gemma4:12b
   ```
   (Any Ollama-served chat model works — just update `LOCAL_LLM_MODEL` in `config.json` to match if you use a different one.)
3. Launch Monster Archiver and, when asked to choose a translation engine, pick **`5` — Local LLM (Ollama)**.
   - Or make it the default so you're never prompted: set `"DEFAULT_TRANSLATION_ENGINE": "5"` in `config.json`.
   - Or pass it on the command line: `python rezakir.py --engine 5`.

`config.json` (created next to the script on first run) also lets you tune `LOCAL_LLM_BASE_URL` (default `http://localhost:11434/v1`), `LOCAL_LLM_BATCH_SIZE`, and related options.

The `webapp/` GUI uses the same local Ollama setup — just click **Apply Local Translation** in its AI Lyrics Studio tab. See `webapp/.env.example` to point it at a different model or port.
