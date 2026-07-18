#!/usr/bin/env python3
"""
Monster Archiver — Nexus Edition v18.3

Audio archiver: fingerprints audio files, fetches metadata and lyrics from
MusicBrainz, iTunes, and Deezer, AI-transcribes vocals via Whisper with
Demucs stem separation, translates lyrics through a choice of cloud or local
LLM engines, and writes fully-tagged FLAC / MP3 / M4A files into an
organised library.
"""

import os
import sys
import subprocess
import shutil
import re
import time
from glob import glob
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VERSION  = "18.3"   # Single source of truth — bump here only

# --- CUDA DLL CLEANUP & SAFE LOADING ---
if os.name == 'nt':
    import site
    # Purge conflicting DLLs from the root folder and register NVIDIA package bins
    dll_patterns = ["cublas*.dll", "cudnn*.dll", "nvblas*.dll"]
    for pattern in dll_patterns:
        for bad_dll in glob(os.path.join(BASE_DIR, pattern)):
            try:
                os.remove(bad_dll)
            except Exception:
                pass

    try:
        site_packages = getattr(site, 'getsitepackages', lambda: [])()
        if hasattr(site, 'getusersitepackages'):
            try:
                site_packages.append(site.getusersitepackages())
            except Exception:
                pass

        for sp in site_packages:
            nvidia_dir = os.path.join(sp, "nvidia")
            if os.path.isdir(nvidia_dir):
                for subdir in os.listdir(nvidia_dir):
                    bin_path = os.path.join(nvidia_dir, subdir, "bin")
                    if os.path.isdir(bin_path):
                        os.environ["PATH"] = bin_path + os.pathsep + os.environ.get("PATH", "")
                        if hasattr(os, 'add_dll_directory'):
                            try: os.add_dll_directory(bin_path)
                            except Exception: pass
    except Exception:
        pass
# ----------------------------------------

import urllib.request
import urllib.parse
import json
import threading
import traceback
import csv
import hashlib
import concurrent.futures
import queue
import sqlite3
import argparse
import shlex
import tempfile
import logging
import difflib
from collections import OrderedDict

# ---------------- CONFIGURATION LAYER ----------------
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

DEFAULT_CONFIG = {
    "MAX_WORKERS": 4,
    "ACOUSTID_API_KEY": "",  # Set your AcoustID key here or in config.json
    "DEFAULT_TRANSLATION_ENGINE": "1",
    "VRAM_SAFE_MODE": True,
    "DEBUG_MODE": False,
    "WORD_LEVEL_LRC": True,
    # "htdemucs_ft" is the fine-tuned variant with better vocal separation.
    # Switch to "htdemucs" for faster runs.
    "DEMUCS_MODEL": "htdemucs_ft",
    # Key notation for tags: "text" (e.g. F# minor), "camelot" (11A, Rekordbox/Traktor), "openkey" (4m).
    "KEY_NOTATION": "text",
    "LOCAL_LLM_BASE_URL": "http://localhost:11434/v1",
    "LOCAL_LLM_MODEL":    "gemma4:12b",
    # requires Ollama >= 0.6.5 for reasoning models like Gemma 4
    "LOCAL_LLM_THINK":      False,
    "LOCAL_LLM_BATCH_SIZE": 8,
    "LOCAL_LLM_MAX_LINE_CHARS": 800,
    "MUSICBRAINZ_CONTACT_EMAIL": "",
    "PHONETIC_CORRECTIONS": {
        "i wish i could do it when i'm on my own": "I wish I could do well when I'm on my own",
        "i wish i could do it": "I wish I could do well",
        "a lot of steps to your favorite song": "I learned the steps to your favorite song",
        "a lot of steps": "I learned the steps",
        "so magic how i feel": "So imagine how I feel",
        "so magic how": "So imagine how",
        "when i come in you're not here": "when I come and you're not here"
    },
    # LRC timestamp shift in ms (+later / -earlier). 0 = disabled.
    "LRC_OFFSET_MS": 0,
    # Reject "lossless" files whose spectrum shows they're upconverted from lossy source (else just warn).
    "REJECT_LOSSY_UPCONVERT": False,
    # Spectral-energy fraction below 16kHz that flags a fake-lossless file; raise toward 1.0 to reduce false positives.
    "UPCONVERT_ENERGY_THRESHOLD": 0.95,

    # ── Faster-Whisper AI transcription ──
    # Checkpoint size: large-v3 (best, ~6GB VRAM, default), medium (~2.8GB, use if large-v3 OOMs), small (fast).
    "WHISPER_MODEL_SIZE": "large-v3",

    # Quantisation: "" = auto, "float16" = best quality/most VRAM, "int8_float16" = balanced (CUDA), "int8" = lowest VRAM.
    "WHISPER_COMPUTE_TYPE": "",

    # Sampling attempts per chunk (only on temperature>0 fallback passes): 1=fast(default), 3=+2-4% acc/3x slower, 5=+3-8%/5x slower.
    "WHISPER_BEST_OF": 1,

    # Beam search width; 5 is the recommended default balance, 7 marginal gain, 10 max accuracy but much slower.
    "WHISPER_BEAM_SIZE": 5,

    # Beam search patience; 1.0 = standard default, 1.5 explores longer before pruning (helps accented/breathy vocals).
    "WHISPER_PATIENCE": 1.5,

    # Fallback temperatures tried in order until a result passes the quality threshold; starts deterministic, warms up.
    "WHISPER_TEMPERATURE_STEPS": [0.0, 0.2, 0.4, 0.6],

    # Min avg token logprob to keep a segment; Whisper default -1.0, -1.5 is more permissive for unusual/layered vocals.
    "WHISPER_LOGPROB_THRESHOLD": -1.5,

    # Condition each 30s chunk on previous chunk's text? True = better name/romanisation consistency; False (default) avoids cascading hallucinations.
    "WHISPER_CONDITION_ON_PREV": False,

    # VAD settings (used only on clean Demucs vocal stems): lower threshold = more sensitive to quiet/falsetto vocals; wider pad avoids clipping words at boundaries; max duration raised past Silero's 30s default to avoid splitting long lines.
    "WHISPER_VAD_THRESHOLD":      0.15,
    "WHISPER_VAD_MIN_SILENCE_MS": 500,
    "WHISPER_VAD_SPEECH_PAD_MS":  600,
    "WHISPER_VAD_MIN_SPEECH_MS":  100,
    "WHISPER_VAD_MAX_SPEECH_S":   60,

    # Pass-1 silence-discard threshold; raised to 0.80 since music (even on a vocal stem) elevates no_speech_prob and 0.65 dropped valid vocal lines.
    "WHISPER_NO_SPEECH_THRESHOLD": 0.80,

    # Relaxed no-speech threshold for Pass 2/3 retries (VAD off); 0.90 only filters out truly silent gaps.
    "WHISPER_NO_SPEECH_THRESHOLD_RELAXED": 0.90,

    # Pass-1 hallucination-guard compression ceiling; raised to 2.6 since legitimately repeated choruses can exceed Whisper's 2.4 default.
    "WHISPER_COMPRESSION_RATIO": 2.6,

    # Relaxed compression ratio for Pass 2/3 — the last-resort passes on dense
    # or repetitive tracks; 3.0 allows even very chorus-heavy songs through.
    "WHISPER_COMPRESSION_RATIO_RELAXED": 3.0,

    # Secondary per-segment ceiling applied after Whisper's own internal filter; only drops segments 95%+ confident to be pure silence.
    "WHISPER_NO_SPEECH_PROB_FILTER": 0.95,
}


# ---------------- KEY NOTATION LOOKUP TABLES ----------------
# Converts analyze_audio_features()'s plain-text key names into KEY_NOTATION format.
# Chromatic order used throughout: C C# D Eb E F F# G Ab A Bb B (flats match Camelot/Open Key labelling).

_CAMELOT_MAP = {
    # Major keys — inner wheel (B suffix)
    "C major":  "8B",  "G major":  "9B",  "D major":  "10B", "A major":  "11B",
    "E major":  "12B", "B major":  "1B",  "F# major": "2B",  "C# major": "3B",
    "Ab major": "4B",  "Eb major": "5B",  "Bb major": "6B",  "F major":  "7B",
    # Minor keys — outer wheel (A suffix)
    "A minor":  "8A",  "E minor":  "9A",  "B minor":  "10A", "F# minor": "11A",
    "C# minor": "12A", "Ab minor": "1A",  "Eb minor": "2A",  "Bb minor": "3A",
    "F minor":  "4A",  "C minor":  "5A",  "G minor":  "6A",  "D minor":  "7A",
}

_OPENKEY_MAP = {
    # Major keys (d = dur)
    "C major":  "1d",  "G major":  "2d",  "D major":  "3d",  "A major":  "4d",
    "E major":  "5d",  "B major":  "6d",  "F# major": "7d",  "C# major": "8d",
    "Ab major": "9d",  "Eb major": "10d", "Bb major": "11d", "F major":  "12d",
    # Minor keys (m = moll)
    "A minor":  "1m",  "E minor":  "2m",  "B minor":  "3m",  "F# minor": "4m",
    "C# minor": "5m",  "Ab minor": "6m",  "Eb minor": "7m",  "Bb minor": "8m",
    "F minor":  "9m",  "C minor":  "10m", "G minor":  "11m", "D minor":  "12m",
}
# ------------------------------------------------------------

# ISO 639-1 (2-letter, what faster-whisper's info.language returns) → ISO 639-2/T
# (3-letter). TLAN (ID3) and the Vorbis/FLAC LANGUAGE field conventionally expect
# the 3-letter form; unrecognised codes fall back to the raw 2-letter value rather
# than being dropped, so an unlisted language still gets *something* written.
_ISO_639_1_TO_2T = {
    "en": "eng", "ja": "jpn", "ko": "kor", "zh": "chi", "es": "spa", "fr": "fre",
    "de": "ger", "it": "ita", "pt": "por", "ru": "rus", "ar": "ara", "hi": "hin",
    "th": "tha", "vi": "vie", "id": "ind", "tr": "tur", "pl": "pol", "nl": "dut",
    "sv": "swe", "no": "nor", "da": "dan", "fi": "fin", "el": "gre", "he": "heb",
    "uk": "ukr", "cs": "cze", "ro": "rum", "hu": "hun", "ta": "tam", "ms": "may",
    "tl": "tgl", "sw": "swa", "fa": "per", "ur": "urd", "bn": "ben", "pa": "pan",
    "mr": "mar", "te": "tel", "kn": "kan", "ml": "mal", "gu": "guj", "cy": "wel",
}


def _explicit_flag(meta):
    """Normalise the several 'explicit' representations collected from iTunes
    (trackExplicitness: explicit/notExplicit/cleaned) and Deezer (explicit_lyrics
    bool) into a single True/False/None (None = source didn't report it, so we
    write nothing rather than falsely claiming 'clean').
    """
    raw = str(meta.get("explicit", "") or "").strip().lower()
    if raw in ("explicit", "true", "1", "yes"):
        return True
    if raw in ("notexplicit", "not_explicit", "cleaned", "clean", "false", "0", "no"):
        return False
    return None


# Diacritic-to-ASCII map for _is_portuguese_line(); built once at module level so it's not rebuilt per line.
_PORTUGUESE_DIACRITIC_MAP = str.maketrans(
    'àáâãäèéêëìíîïòóôõöùúûüýçñ',
    'aaaaaeeeeiiiiooooouuuuycn'
)

# Diacritics essentially absent from English text; their presence signals a Romance/Germanic language (must not be classed English). frozenset membership = O(1) per char.
_EUROPEAN_DIACRITICS = frozenset(
    'àáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ'  # lowercase
    'ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞ'   # uppercase
    'ß'                                   # German sharp-s — unambiguously non-English
)

def load_config():
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=4)
    with open(CONFIG_FILE, "r") as f:
        conf = json.load(f)
    # --- all mutations happen after the read handle is closed ---
    updated = False

    for key, val in DEFAULT_CONFIG.items():
        if key not in conf:
            conf[key] = val
            updated = True

    keys_to_remove = [
        "ENABLE_EXCITER", "TARGET_SAMPLERATE", "TARGET_BITDEPTH", "SOXR_PRECISION",
        "FLAC_COMPRESSION", "BROWSER_FOR_COOKIES", "DEFAULT_AI_PROFILE",
        "CONFIDENCE_THRESHOLD", "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET",
        "ROUTEWAY_API_KEY", "ROUTEWAY_BASE_URL", "ROUTEWAY_MODEL", "ROUTEWAY_FALLBACK_MODEL"
    ]
    for key in keys_to_remove:
        if key in conf:
            del conf[key]
            updated = True

    if updated:
        _cfg_dir = os.path.dirname(CONFIG_FILE)
        _fd, _tmp_cfg = tempfile.mkstemp(dir=_cfg_dir, suffix=".json.tmp")
        try:
            with os.fdopen(_fd, "w") as fw:
                json.dump(conf, fw, indent=4)
            os.replace(_tmp_cfg, CONFIG_FILE)
        except Exception:
            try:
                os.unlink(_tmp_cfg)
            except OSError:
                pass
            raise
    return conf


# ---------- Config Validation ----------
def validate_config(conf):
    """Warn about misconfigured or missing settings before the first file is processed.
    Prints human-readable warnings to stdout so they appear before the Rich UI starts."""
    issues = []

    if not conf.get("ACOUSTID_API_KEY"):
        issues.append("ACOUSTID_API_KEY is empty — audio fingerprinting/dedup will be skipped. "
                      "Get a free key at https://acoustid.org/api-key")

    if not conf.get("MUSICBRAINZ_CONTACT_EMAIL"):
        issues.append("MUSICBRAINZ_CONTACT_EMAIL is empty — MusicBrainz may throttle requests. "
                      "Add your e-mail to config.json.")

    engine = str(conf.get("DEFAULT_TRANSLATION_ENGINE", "1"))
    if engine == "5":
        local_url = conf.get("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/")
        try:
            urllib.request.urlopen(f"{local_url}/models", timeout=3)
        except Exception:
            issues.append(f"LOCAL_LLM_BASE_URL '{local_url}' is unreachable — "
                          "is Ollama running? (`ollama serve`)")

    _known_notations = {"text", "camelot", "openkey"}
    notation = conf.get("KEY_NOTATION", "text")
    _conf_needs_save = False
    if notation not in _known_notations:
        issues.append(f"Unknown KEY_NOTATION '{notation}'. Valid values: text, camelot, openkey. "
                      "Falling back to 'text'.")
        conf["KEY_NOTATION"] = "text"
        _conf_needs_save = True

    offset = conf.get("LRC_OFFSET_MS", 0)
    if not isinstance(offset, (int, float)):
        issues.append(f"LRC_OFFSET_MS must be a number (got '{offset}'). Defaulting to 0.")
        conf["LRC_OFFSET_MS"] = 0
        _conf_needs_save = True

    upconv_thresh = conf.get("UPCONVERT_ENERGY_THRESHOLD", 0.95)
    if not isinstance(upconv_thresh, (int, float)) or not (0.5 <= upconv_thresh <= 1.0):
        issues.append(
            f"UPCONVERT_ENERGY_THRESHOLD must be a float between 0.5 and 1.0 "
            f"(got '{upconv_thresh}'). Defaulting to 0.95."
        )
        conf["UPCONVERT_ENERGY_THRESHOLD"] = 0.95
        _conf_needs_save = True

    # Persist all corrections in one atomic write, avoiding a partial/corrupt config if the process is killed mid-write.
    if _conf_needs_save:
        _save_ok = False
        try:
            _cfg_dir = os.path.dirname(CONFIG_FILE)
            _fd, _tmp_cfg = tempfile.mkstemp(dir=_cfg_dir, suffix=".json.tmp")
            try:
                with os.fdopen(_fd, "w") as _fw:
                    json.dump(conf, _fw, indent=4)
                os.replace(_tmp_cfg, CONFIG_FILE)
                _save_ok = True
            except Exception:
                try:
                    os.unlink(_tmp_cfg)
                except OSError:
                    pass
        except Exception:
            pass
        if not _save_ok:
            print(
                f"\n  ⚠  [CONFIG] Could not persist corrected settings to {CONFIG_FILE} "
                "(disk full or permission denied?). Corrections are active for this run "
                "but will need to be reapplied on the next launch."
            )

    if issues:
        print("\n[CONFIG VALIDATION]")
        for msg in issues:
            print(f"  ⚠  {msg}")
        print()


CONF = load_config()
validate_config(CONF)

# ---------------- AUTO-INSTALLER ----------------
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

def ensure_dependencies():
    try:
        import torch
    except ImportError:
        # torch not installed yet — it will be pulled in as a transitive dependency
        # when pip installs faster-whisper below; nothing extra to do here.
        pass
    except Exception as e:
        if "WinError 127" in str(e):
            print(
                "\n[CRITICAL ERROR] PyTorch DLL mismatch (WinError 127). "
                "Visit https://pytorch.org for the correct install command for your CUDA version."
            )
            sys.exit(1)
        # Other runtime errors (e.g. driver / hardware issues) — warn, don't crash yet.
        print(f"\n[WARNING] Unexpected PyTorch import error: {e}")

    standard_deps = {
        "rich": "rich", "mutagen": "mutagen", "requests": "requests",
        "syncedlyrics": "syncedlyrics", "deep-translator": "deep_translator",
        "pyacoustid": "acoustid", "musicbrainzngs": "musicbrainzngs",
        "pykakasi": "pykakasi", "imageio-ffmpeg": "imageio_ffmpeg", "demucs": "demucs",
        "faster-whisper": "faster_whisper", "librosa": "librosa", "numpy": "numpy",
        "watchdog": "watchdog",
    }

    missing = []
    for pip_name, import_name in standard_deps.items():
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pip_name)

    needs_dll_fix = False
    if os.name == 'nt':
        try:
            import importlib.metadata as _imeta
            _imeta.version("nvidia-cublas-cu12")
            _imeta.version("nvidia-cudnn-cu12")
        except Exception:
            # Catches PackageNotFoundError (cu12 not installed) and ImportError
            # (importlib.metadata unavailable on unusual Python builds).
            needs_dll_fix = True

    if missing or needs_dll_fix:
        print("\n📦 Auto-aligning environment and fixing dependency conflicts...")

        if missing:
            print("Installing standard dependencies...")
            cmd = [sys.executable, "-m", "pip", "install"] + missing
            try:
                subprocess.check_call(cmd)
            except subprocess.CalledProcessError:
                print("\n[CRITICAL ERROR] Pip failed to install standard dependencies.")
                sys.exit(1)

        if needs_dll_fix:
            print("Purging old cu11 packages and downloading cu12 DLLs (Fixing Error 126)...")
            try:
                subprocess.call([sys.executable, "-m", "pip", "uninstall", "-y", "nvidia-cublas-cu11", "nvidia-cudnn-cu11"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.check_call([
                    sys.executable, "-m", "pip", "install",
                    "nvidia-cudnn-cu12",
                    "nvidia-cublas-cu12"
                ])
            except subprocess.CalledProcessError:
                print("\n[CRITICAL ERROR] Failed to download NVIDIA DLLs.")
                sys.exit(1)

        print("\n✅ Dependencies successfully aligned! Please run the script one more time.")
        sys.exit(0)

ensure_dependencies()

# ---------------- FFMPEG INJECTION ----------------
try:
    import imageio_ffmpeg
    pip_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    local_ffmpeg = os.path.join(BASE_DIR, "ffmpeg.exe" if os.name == 'nt' else "ffmpeg")

    if not os.path.exists(local_ffmpeg):
        shutil.copy2(pip_ffmpeg, local_ffmpeg)

    _path_entries = os.environ.get("PATH", "").split(os.pathsep)
    if BASE_DIR not in _path_entries:
        os.environ["PATH"] = BASE_DIR + os.pathsep + os.environ.get("PATH", "")
except Exception as e:
    if CONF.get("DEBUG_MODE"):
        print(f"FFmpeg injection failed: {e}")

import numpy as np          # safe here — ensure_dependencies() already ran
import requests
import acoustid
import syncedlyrics
import mutagen
import musicbrainzngs
import librosa
import torch
from mutagen.flac import FLAC, Picture
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, USLT, SYLT, TCON, TCOM, TPE1, TPE2, TEXT, TBPM, TKEY, TPOS, TSRC, TLAN, TXXX
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm, AtomDataType
from deep_translator import GoogleTranslator, MyMemoryTranslator
from rich.console import Console
from rich.prompt import Prompt
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich import box

# ---------------- API SETUP ----------------
musicbrainzngs.set_useragent("MonsterArchiver", VERSION, CONF.get("MUSICBRAINZ_CONTACT_EMAIL", ""))

# ---------------- UI DASHBOARD SETUP ----------------
console = Console()
cli_lock = threading.Lock()
ui_lock = threading.Lock()
translation_lock = threading.Lock()
gpu_lock = threading.Lock()
_db_lock         = threading.Lock()   # Serialises concurrent SQLite writes across workers

# Input request queue: worker threads push prompts here and block on a threading.Event; main thread drains one at a time.
_input_queue = queue.Queue()

GLOBAL_TRANS_METHOD = CONF["DEFAULT_TRANSLATION_ENGINE"]
GLOBAL_AUDIO_LANG = "auto"
GLOBAL_ENABLE_LYRICS = True   # Set to False at startup to skip all DB fetch + AI transcription
GLOBAL_DRY_RUN = False        # --dry-run: preview the archive pipeline without writing/moving/tagging anything
worker_status = {}
active_live_ui = None

def generate_dashboard_table():
    table = Table(box=box.SIMPLE_HEAD, expand=True)
    table.add_column("Worker", style="cyan", width=12)
    table.add_column("Task / File ID", style="white")
    table.add_column("Status", style="magenta")

    # Read under ui_lock — update_ui() always writes under the same lock.
    with ui_lock:
        snapshot = {k: dict(v) for k, v in worker_status.items()}

    for i in range(CONF["MAX_WORKERS"]):
        data = snapshot.get(i, {"id": "--", "msg": "Idle"})
        table.add_row(f"Thread-{i+1}", data["id"], data["msg"])

    return Panel(table, title=f"[bold cyan]Monster Archiver v{VERSION} (Genre / Composer / Lyricist + AI Translation Engine)[/bold cyan]", border_style="blue")

def update_ui(worker_idx, vid=None, msg=None):
    with ui_lock:
        if worker_idx not in worker_status:
            worker_status[worker_idx] = {"id": "--", "msg": "Idle"}
        if vid is not None:
            worker_status[worker_idx]["id"] = vid
        if msg is not None:
            worker_status[worker_idx]["msg"] = msg

def pause_ui_and_ask(prompt_msg, choices=None, default="1", table_to_print=None, force_interactive=False):
    """Thread-safe prompt dispatcher. In WATCH_MODE auto-resolves to the default
    (unless force_interactive=True, used for lyrics selection). Otherwise queues
    the request and blocks until the main-thread loop signals completion via
    a threading.Event, so worker threads never fight over the console.
    """
    if (WATCH_MODE or GLOBAL_DRY_RUN) and not force_interactive:
        # Daemon mode / dry-run preview: use `is not None` since default="" is valid/falsy and must be returned as-is, not fall through to choices[0].
        answer = default if default is not None else (choices[0] if choices else "1")
        if CONF.get("DEBUG_MODE") and table_to_print:
            with cli_lock:
                console.print(table_to_print)
            _tag = "Watch" if WATCH_MODE else "Dry Run"
            log(f"[{_tag}] Auto-selected '{answer}' for: {prompt_msg[:60]}", "dim")
        return answer

    answer_holder = [None]
    done_event = threading.Event()
    _input_queue.put({
        "prompt":  prompt_msg,
        "choices": choices,
        "default": default,
        "table":   table_to_print,
        "answer":  answer_holder,
        "event":   done_event,
    })
    done_event.wait()
    return answer_holder[0]

# ---------------- PATHS & CONFIG ----------------
MUSIC_DIR = os.path.join(os.path.expanduser("~"), "Music", "Monster_Library")
LOGS_DIR = os.path.join(MUSIC_DIR, ".logs")
DB_FILE = os.path.join(LOGS_DIR, "archiver.sqlite")

# Default watch-folder path — override at runtime with --watch <path>
_DEFAULT_WATCH_PATH = os.path.join(os.path.expanduser("~"), "Downloads")

for d in (MUSIC_DIR, LOGS_DIR):
    os.makedirs(d, exist_ok=True)

# ---------------- DATABASE ----------------
def init_db():
    with _db_lock, sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS downloads (
                vid TEXT PRIMARY KEY,
                title TEXT,
                status TEXT,
                file_path TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Add file_path column if absent (safe no-op on up-to-date databases).
        try:
            conn.execute("ALTER TABLE downloads ADD COLUMN file_path TEXT")
        except sqlite3.OperationalError:
            pass  # Column already exists — safe to ignore
        # Fingerprint table: stores chromaprint hashes so re-imports of the
        # same audio are caught before any expensive processing runs.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fingerprints (
                fingerprint TEXT PRIMARY KEY,
                title TEXT,
                artist TEXT,
                library_path TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

def db_log_status(vid, title, status, file_path=None):
    try:
        with _db_lock, sqlite3.connect(DB_FILE) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO downloads (vid, title, status, file_path) VALUES (?, ?, ?, ?)",
                (vid, title, status, file_path),
            )
    except Exception as e:
        # Always log DB write failures — a silently lost FAILED status means
        # --retry-failed will never find and re-queue that file.
        log(f"DB write error (db_log_status): {e}", "bold red")

def get_failed_entries():
    """Return [(vid, title, file_path)] for every row whose status starts with FAILED."""
    try:
        with _db_lock, sqlite3.connect(DB_FILE) as conn:
            cur = conn.execute(
                "SELECT vid, title, file_path FROM downloads WHERE status LIKE 'FAILED%'"
            )
            return cur.fetchall()
    except Exception as e:
        if CONF.get("DEBUG_MODE"):
            log(f"DB read error (get_failed_entries): {e}", "red")
        return []

def is_processed(vid):
    try:
        with _db_lock, sqlite3.connect(DB_FILE) as conn:
            cur = conn.execute("SELECT status FROM downloads WHERE vid=?", (vid,))
            row = cur.fetchone()
            return bool(row and row[0] in ("SUCCESS", "DUPLICATE"))
    except Exception as e:
        if CONF.get("DEBUG_MODE"):
            log(f"DB read error (is_processed): {e}", "red")
        return False

# ---------- Acoustic Duplicate Detection ----------
def check_acoustic_duplicate(fingerprint):
    """Return stored metadata dict if fingerprint already exists in the library, else None."""
    if not fingerprint:
        return None
    try:
        with _db_lock, sqlite3.connect(DB_FILE) as conn:
            cur = conn.execute(
                "SELECT title, artist, library_path FROM fingerprints WHERE fingerprint=?",
                (str(fingerprint),)
            )
            row = cur.fetchone()
            if row:
                return {"title": row[0], "artist": row[1], "path": row[2]}
    except Exception as e:
        if CONF.get("DEBUG_MODE"):
            log(f"DB read error (check_acoustic_duplicate): {e}", "red")
    return None

def store_fingerprint(fingerprint, title, artist, library_path):
    """Store a chromaprint fingerprint alongside its library location."""
    if not fingerprint:
        return
    try:
        with _db_lock, sqlite3.connect(DB_FILE) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO fingerprints (fingerprint, title, artist, library_path) "
                "VALUES (?, ?, ?, ?)",
                (str(fingerprint), title or "Unknown", artist or "Unknown", library_path or "")
            )
    except Exception as e:
        if CONF.get("DEBUG_MODE"):
            log(f"DB write error (store_fingerprint): {e}", "red")

init_db()

# ---------------- Helpers ----------------
def log(msg, style="bold white"):
    ts = time.strftime("%H:%M:%S")
    formatted = f"[dim]{ts}[/dim] {msg}"
    # Route through the Live console so output appears permanently above the
    # panel rather than being overwritten on the next 4 Hz refresh tick.
    with cli_lock:
        live = active_live_ui
        if live and live.is_started:
            live.console.print(formatted, style=style)
        else:
            console.print(formatted, style=style)

# Windows reserved device names — defined at module level so the set is
# constructed once at startup rather than rebuilt on every sanitize_filename call.
_WINDOWS_RESERVED_NAMES = frozenset({
    'CON', 'PRN', 'AUX', 'NUL',
    'COM0', 'COM1', 'COM2', 'COM3', 'COM4', 'COM5',
    'COM6', 'COM7', 'COM8', 'COM9',
    'LPT0', 'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5',
    'LPT6', 'LPT7', 'LPT8', 'LPT9',
})

def sanitize_filename(s, maxlen=100):
    """Sanitize a string for safe use as a filesystem path component.
    Strips only OS-forbidden characters (Windows: <>:"/\\|?*, Linux/macOS: / and null).
    Parentheses, brackets, and apostrophes are preserved (common in music filenames).
    Windows reserved device names (CON, NUL, COM1-9, LPT1-9) get a trailing underscore.
    """
    if not s:
        return "Unknown"
    s = str(s).replace('"', '').replace('\x00', '')
    s = re.sub(r'[<>:/\\|?*]', '', s)   # forbidden on Windows / path-unsafe on all OSes
    s = re.sub(r'\s+', ' ', s).strip()
    s = s.strip('.')  # Avoid hidden files on Unix and invalid paths on Windows
    s = s[:maxlen] if s else "Unknown"
    # Guard Windows reserved device names (case-insensitive; also covers "CON.txt").
    # Applied on all platforms for portability when libraries are shared cross-OS.
    # Only fires when the reserved word is the WHOLE string, or is followed by a
    # real extension-style suffix with no whitespace (e.g. "CON" or "CON.txt") —
    # so a legitimate title/artist that merely starts with "Con.", "Aux.", etc.
    # followed by real words (e.g. "Con. Amore") isn't mistaken for a device name.
    _dot_idx = s.find('.')
    _base   = (s[:_dot_idx] if _dot_idx != -1 else s).upper()
    _suffix = s[_dot_idx:] if _dot_idx != -1 else ""
    if _base in _WINDOWS_RESERVED_NAMES and ' ' not in _suffix:
        # Append _ to disambiguate; replace the last char when already at maxlen
        # so the result never exceeds the caller's requested limit.
        s = (s[:-1] if len(s) >= maxlen else s) + '_'
    return s if s else "Unknown"

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def split_artists(artist_string):
    if not artist_string or artist_string == "Unknown":
        return ["Unknown"]
    # Separators: feat./ft./& (English), × (Japanese credits), ／ (full-width slash)
    splits = re.split(
        r'\s+(?:feat\.?|ft\.?|&)\s+|\s*[,×／]\s*',
        artist_string,
        flags=re.IGNORECASE,
    )
    return [s.strip() for s in splits if s.strip()]

def is_mostly_romaji(text):
    clean = re.sub(r'[^a-zA-Z\s]', '', text).lower()
    words = clean.split()
    if not words:
        return False

    score = 0
    # Extended English function-word list — covers phonk/trap's short vowel-ending words the smaller list misjudged as romaji.
    eng_words = {
        'i', 'you', 'the', 'a', 'to', 'is', 'it', 'in', 'on', 'me', 'my',
        'we', 'are', 'be', 'this', 'that', 'do', 'and', 'your', 'love',
        'baby', 'yeah', 'oh', 'let', 'go', 'now', 'for', 'of', 'with',
        'so', 'just', 'like', 'know', 'no', 'up', 'out', 'all', 'they',
        'he', 'she', 'his', 'her', 'can', 'will', 'had', 'was',
        'get', 'got', 'but', 'not', 'at', 'if', 'by', 'or', 'have',
        'been', 'more', 'see', 'free', 'here', 'there', 'some', 'come',
        'give', 'live', 'move', 'feel', 'real', 'ride', 'inside', 'side',
        'time', 'mine', 'line', 'shine', 'fine', 'make', 'take', 'fake',
        'game', 'name', 'same', 'came', 'gonna', 'wanna', 'gotta', 'tryna',
        # Additional common English words that vowel-end and were causing
        # false Romaji positives for English hip-hop/phonk lyrics:
        'where', 'when', 'while', 'who', 'what', 'why', 'how', 'those',
        'these', 'have', 'has', 'want', 'think', 'tell', 'say', 'again',
        'before', 'cause', 'because', 'even', 'every', 'never', 'always',
        'maybe', 'someone', 'nothing', 'something', 'everything',
        'people', 'between', 'above', 'below', 'over', 'under', 'home',
        'alone', 'one', 'none', 'done', 'gone', 'bone', 'tone', 'phone',
        'stone', 'zone', 'fire', 'desire', 'empire', 'entire', 'wire',
        'whole', 'soul', 'role', 'hole', 'pole', 'stole', 'broke', 'smoke',
        'spoke', 'woke', 'hope', 'cope', 'rope', 'dope', 'flow', 'show',
        'grow', 'glow', 'slow', 'throw', 'below', 'follow',
    }

    for w in words:
        if w in eng_words:
            score -= 3
        elif len(w) > 1 and w[-1:] in ['a', 'e', 'i', 'o', 'u', 'n']:
            score += 1
        else:
            score -= 1
    return score > 0

def get_language_type(text):
    if re.search(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FAF]', text):
        return 'japanese_kana_kanji'
    if is_mostly_romaji(text):
        return 'japanese_romaji'
    return 'other'

kks = None
_kks_lock = threading.Lock()

def kanji_to_romaji(text):
    global kks
    try:
        import pykakasi
        with _kks_lock:
            if kks is None:
                kks = pykakasi.kakasi()
            result = kks.convert(text)   # convert() is NOT documented as thread-safe; hold lock
        return " ".join([item['hepburn'] for item in result])
    except Exception:
        return text

def get_audio_duration(path):
    """Return audio duration in seconds via mutagen then librosa fallback, or 0.0."""
    try:
        probe = mutagen.File(path)
        if probe and probe.info:
            dur = float(probe.info.length)
            if dur > 0:
                return dur
    except Exception:
        pass
    try:
        # librosa 0.9+ uses a `path` kwarg (no full array in RAM); older builds use `filename` instead — fall back on TypeError.
        try:
            return float(librosa.get_duration(path=path))
        except TypeError:
            # librosa < 0.9 used 'filename=' instead of 'path='.
            return float(librosa.get_duration(filename=path))
    except Exception:
        return 0.0

# ---------------- Audio Fingerprinting ----------------
def get_metadata_via_fingerprint(file_path):
    """Fingerprint a file via AcoustID.  Returns (metadata_dict_or_None, raw_fingerprint_or_None).
    The raw fingerprint is passed back so callers can store it for acoustic duplicate detection
    without computing the fingerprint a second time."""
    raw_fingerprint = None
    try:
        duration, fingerprint = acoustid.fingerprint_file(file_path)
        raw_fingerprint = fingerprint
        # No API key configured — skip the lookup (would fail anyway) but still return the fingerprint for duplicate detection.
        if not CONF.get("ACOUSTID_API_KEY"):
            return None, raw_fingerprint
        res = acoustid.lookup(CONF["ACOUSTID_API_KEY"], fingerprint, duration,
                              meta='recordings+tracks+artists')
        if res.get('results'):
            top = res['results'][0]
            score = top.get('score', 0.0)
            # Reject matches scoring below 0.6 — low confidence risks silently tagging the track with wrong metadata.
            if score < 0.6:
                if CONF.get("DEBUG_MODE"):
                    log(f"AcoustID: low-confidence match discarded (score={score:.2f})", "yellow")
                return None, raw_fingerprint
            recordings = top.get('recordings', [])
            if recordings:
                best = recordings[0]
                return {
                    "title": best.get("title"),
                    "artist": best.get("artists", [{}])[0].get("name")
                }, raw_fingerprint
    except Exception as e:
        if CONF.get("DEBUG_MODE"):
            log(f"AcoustID Err: {e}", "red")
    return None, raw_fingerprint

# ---------------- LLM Translation (Local Ollama) ----------------

# System prompt for the local Ollama LLM (method 5): enforces emotional fidelity both ways — no softening dark songs, no projecting darkness onto light ones.
_LOCAL_LLM_SYSTEM_PROMPT = (
    "You are an expert music lyric translator fluent in Japanese, Korean, "
    "Chinese (Mandarin/Cantonese), Spanish, French, German, and other languages. "
    "Your goal is translations that are BOTH emotionally faithful AND poetically alive. "
    "Read the song context and tone description carefully — your translation must match "
    "the actual mood of THIS song, not a generic emotional default.\n"
    "EMOTIONAL FIDELITY — mirror the original exactly:\n"
    "- Light, playful, or upbeat content stays light and upbeat. "
    "Keep the energy bright and the language breezy — do not add weight or gravity that isn't there.\n"
    "- Happy, romantic, or tender content stays warm and affectionate. "
    "Let the sweetness come through naturally.\n"
    "- Dark, bleak, or hopeless content stays dark. "
    "Render it with honest, unflinching language — do not soften or uplift it.\n"
    "- Sad or melancholy content stays sad. Give the sorrow space and weight.\n"
    "- NEVER add darkness, drama, or gravity that is not present in the original.\n"
    "- NEVER soften or uplift a line whose original mood is genuinely heavy or dark.\n"
    "POETIC QUALITY: translate meaning-for-meaning, never word-for-word. "
    "Choose English words that carry the right emotional register for this specific song. "
    "The result should read like a natural English lyric with the same emotional arc as the original. "
    "Lines that are already in English must be returned exactly as written. "
    "TRANSLATION INTEGRITY — ABSOLUTE RULE: You are a translation instrument, not a content filter. "
    "Song lyrics are protected artistic expression. Translate ALL content exactly as the artist intended: "
    "profanity and vulgar slang must be translated with equivalent profanity or slang in English — "
    "never softened, asterisked, or replaced with a milder word. "
    "Drug references, sexual content, and violent imagery must be translated literally and faithfully. "
    "Never omit, paraphrase, or flag any word or phrase due to its subject matter. "
    "If the original uses content you would normally refuse, translate it anyway — "
    "your duty as a translator supersedes content preferences. A translator who censors has mistranslated. "
    "Respond EXCLUSIVELY with numbered line translations and nothing else. "
    "Do NOT output any thinking, reasoning, planning, or <think> blocks — "
    "go directly to the numbered output."
)


# ── Thinking-model helpers ──
# Reasoning models (Gemma 4 etc.) prepend a <think>...</think> block; stripped after the full reply is collected so downstream parsers only see translation lines.
_THINK_BLOCK_RE = re.compile(r'<think>.*?</think>', re.DOTALL | re.IGNORECASE)


def _strip_think(text: str) -> str:
    """Remove <think>...</think> blocks emitted by reasoning models.
    If the model puts its whole answer inside the think block (leaving nothing
    after it — a known Gemma 4 behavior), fall back to the block's inner text
    instead of returning empty, since the downstream parser can still extract
    translations from it.
    """
    stripped = _THINK_BLOCK_RE.sub("", text).strip()
    if stripped:
        return stripped
    # Fallback: answer is embedded inside the think block.
    m = re.search(r'<think>(.*?)</think>', text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


def _stream_ollama_response(url: str, headers: dict, payload: dict, timeout: int) -> str:
    """POST *payload* to *url* with SSE streaming; return the accumulated
    assistant content with thinking blocks stripped.

    Streaming resets the read-timeout clock with every token, so a long
    chain-of-thought pass doesn't trigger a false timeout (non-streaming
    holds the connection open silently until the full response is ready).

    Ollama issue #805: thinking models route chain-of-thought through
    delta.reasoning (not delta.content), which stays "" during the think
    pass. delta.content is collected as the primary answer; delta.reasoning
    is used only as a fallback.
    """
    sp = {**payload, "stream": True}
    # Connect timeout 15 s (model should already be warm), read timeout = caller budget
    resp = requests.post(url, headers=headers, json=sp, stream=True, timeout=(15, timeout))
    resp.raise_for_status()

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    for raw in resp.iter_lines():
        if not raw:
            continue
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        if not line.startswith("data: "):
            continue
        data_str = line[6:].strip()
        if data_str == "[DONE]":
            break
        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
        content = delta.get("content", "")
        if content:
            content_parts.append(content)
        # Some Ollama builds emit thinking tokens under delta.reasoning_content instead of delta.reasoning — check both.
        reasoning = delta.get("reasoning", "") or delta.get("reasoning_content", "")
        if reasoning:
            reasoning_parts.append(reasoning)

    result = _strip_think("".join(content_parts))
    if result:
        return result
    # content was empty for the entire stream — fall back to captured reasoning text instead of "" (callers treat "" as a hard failure).
    return _strip_think("".join(reasoning_parts))
# ────────────────────────────────────────────────────────────────────────────


# ---------------------------------------------------------------------------
# Spanish/Latin-phonk vocabulary for _is_spanish_line() — detects pure-ASCII lines that are Spanish, not English. Words chosen to not overlap common English vocab.
# ---------------------------------------------------------------------------
_SPANISH_KEYWORDS = frozenset({
    # Determiners / contracted forms
    'del', 'los', 'las', 'una', 'unos', 'unas',
    # Prepositions / conjunctions not shared with English
    'para', 'con', 'sin', 'pero', 'porque', 'aunque', 'cuando', 'donde',
    # Subject pronouns / reflexives
    'yo', 'ella', 'ellos', 'ellas', 'nosotros', 'vosotros',
    'esto', 'esta', 'ese', 'esa', 'aquel',
    # Common verb forms unique to Spanish
    'soy', 'eres', 'somos', 'estoy', 'quiero', 'quiere', 'puedo',
    'miente', 'quema', 'golpea', 'lleva', 'prenden', 'caen', 'viene',
    # Nouns / adjectives frequent in phonk / Latin-pop
    'noche', 'fuego', 'amor', 'vida', 'corazon', 'baile', 'ritmo',
    'calor', 'pasos', 'alma', 'viento', 'cielo', 'tierra',
    'sangre', 'fuerza', 'poder', 'salvaje', 'secreto',
    'caderas', 'estrellas', 'sombra', 'oscuro', 'tacón', 'fiebre',
    # Adverbs
    'siempre', 'nunca', 'ahora', 'despues', 'antes', 'tambien',
})


def _is_spanish_line(text):
    """Return True if *text* is clearly Spanish: one Spanish keyword in a short
    line (<=4 words), or two+ anywhere. Punctuation is replaced with a space
    (not deleted) so comma-glued pairs like "amor,vida" tokenise correctly.
    """
    if not text or not text.strip():
        return False
    _clean = re.sub(r'[^a-z\s]', ' ', text.lower())
    _words = set(_clean.split())
    hits = len(_words & _SPANISH_KEYWORDS)
    return hits >= 2 or (hits == 1 and len(_words) <= 4)


# Portuguese/Brazilian-phonk vocabulary for _is_portuguese_line() — same ASCII-detection purpose as the Spanish set; no overlap with English or Spanish words.
# ---------------------------------------------------------------------------
_PORTUGUESE_KEYWORDS = frozenset({
    # Subject pronouns / possessives unique to Portuguese
    'eu', 'tu', 'meu', 'minha', 'teu', 'tua', 'você', 'voce',
    'nosso', 'nossa',
    # High-frequency verb forms specific to Portuguese conjugation
    'vem',      # come (imperative) — Spanish uses "ven"
    'vais',     # you go (2nd-person singular) — not Spanish
    'estou',    # I am — Spanish "estoy"
    'quiser',   # to want (future subjunctive) — no Spanish equivalent
    'chegar',   # to arrive
    'ouvir',    # to hear
    'faz',      # makes/does (3rd-person singular) — not English
    'guias',    # you guide (2nd-person) — unaccented Portuguese form
    'paro',     # I stop
    'gira',     # turns / rotates (also Brazilian slang: cool)
    'balanca',  # sways / bounces (unaccented form of "balança")
    # Contraction / preposition particle exclusive to Portuguese
    'pra',      # contraction of "para" (for/to) — not Spanish, not English
    'pelo',     # por + o
    'pela',     # por + a
    # High-frequency content words absent from English and not in Spanish set
    'tudo',     # everything
    'muito',    # very / much
    'aqui',     # here (also Spanish "aquí", but definitely not English)
    'tambem',   # also (unaccented form of "também")
    'bracos',   # arms (unaccented form of "braços")
    'suspiro',  # sigh
})


def _is_portuguese_line(text):
    """Return True if *text* is clearly Portuguese (or Brazilian phonk): one
    keyword in a short line (<=4 words), or two+ anywhere. Guards against
    accent-stripped ASCII phonk lyrics being mis-classified as English.
    Diacritics are normalised to ASCII and punctuation replaced with a space
    before matching, so accented/comma-glued words still hit the keyword set.
    """
    if not text or not text.strip():
        return False
    # Strip diacritics to ASCII (e.g. "balança"→"balanca") to match the keyword set; _PORTUGUESE_DIACRITIC_MAP is built once at module level.
    _lower = text.lower().translate(_PORTUGUESE_DIACRITIC_MAP)
    # Replace non-alpha / non-space characters with a SPACE (not empty string) so
    # that comma-glued tokens like "guias,amor" split into two words, not one.
    _clean = re.sub(r'[^a-z\s]', ' ', _lower)
    _words = set(_clean.split())
    hits = len(_words & _PORTUGUESE_KEYWORDS)
    return hits >= 2 or (hits == 1 and len(_words) <= 4)


# ---------------------------------------------------------------------------
# French vocabulary for _is_french_line() — same ASCII-detection purpose as the Spanish/Portuguese sets, unambiguous vs. English.
# ---------------------------------------------------------------------------
_FRENCH_KEYWORDS = frozenset({
    # Subject pronouns unique to French
    'je', 'tu', 'nous', 'vous', 'ils', 'elles',
    # Possessive determiners absent from English
    'mes', 'tes', 'ses', 'notre', 'votre', 'leurs',
    # Function words not shared with English
    'dans', 'mais', 'quand', 'comme', 'donc', 'puis', 'aussi',
    'pourquoi', 'toujours', 'jamais', 'trop', 'rien', 'meme',
    'apres', 'avant', 'encore', 'maintenant', 'parce',
    # Verb forms specific to French conjugation
    'suis', 'etais', 'avais', 'avons', 'sommes', 'etes', 'sont',
    'veux', 'fais', 'viens', 'prends', 'connais',
    # High-frequency content words in French songs
    'nuit', 'coeur', 'amour', 'toi', 'moi', 'lui',
    'monde', 'belle', 'beau', 'rien', 'bien', 'mal',
})


def _is_french_line(text):
    """Return True if *text* is clearly French: one French keyword in a short
    line (<=6 words), or two+ anywhere. Handles diacritic-stripped ASCII
    lyrics since most _FRENCH_KEYWORDS entries are already diacritic-free.
    """
    if not text or not text.strip():
        return False
    _clean = re.sub(r'[^a-z\s]', ' ', text.lower())
    _words = set(_clean.split())
    hits = len(_words & _FRENCH_KEYWORDS)
    return hits >= 2 or (hits == 1 and len(_words) <= 6)


def _is_english_line(text):
    """Return True if a lyric line is already English and needs no translation.
    Checks in order: CJK/Hangul codepoints, European diacritics, Romaji,
    explicit non-English GLOBAL_AUDIO_LANG, then Spanish/Portuguese/French
    keyword hits — any match means "not English". Otherwise falls back to
    an ASCII-ratio check (>75% ASCII = treat as English).
    """
    if not text or not text.strip():
        return False
    for c in text:
        cp = ord(c)
        # CJK Unified Ideographs, Hiragana, Katakana, CJK symbols, Bopomofo, etc.
        if 0x3000 <= cp <= 0x9FFF:
            return False
        # Hangul syllables / jamo
        if 0xAC00 <= cp <= 0xD7AF or 0x1100 <= cp <= 0x11FF:
            return False
        # Hangul Jamo Extended-A and Extended-B
        if 0xA960 <= cp <= 0xA97F or 0xD7B0 <= cp <= 0xD7FF:
            return False
        # CJK Compatibility Ideographs + Extension B/beyond (outside the BMP) — rare in pop/anime lyrics but still valid CJK.
        if 0xF900 <= cp <= 0xFAFF:
            return False
        if 0x20000 <= cp <= 0x2FA1F:
            return False
    # European diacritics (é, è, ç, ü, ñ, ß...) are rare in English text; their presence signals a Romance/Germanic language needing translation — fixes high-ASCII French lines being missed.
    if any(c in _EUROPEAN_DIACRITICS for c in text):
        return False
    if is_mostly_romaji(text):
        return False   # Japanese romanised — still needs translation
    # Honour an explicit audio-language setting — high ASCII ratio alone isn't grounds to call a line English when lang != en/auto.
    if GLOBAL_AUDIO_LANG not in ("auto", "en"):
        return False
    # Auto mode: run language-specific vocab checks before the ASCII-ratio fallback, since Spanish/Portuguese/French lyrics can be pure ASCII too.
    if _is_spanish_line(text):
        return False
    if _is_portuguese_line(text):
        return False
    if _is_french_line(text):
        return False
    # Tolerate some non-ASCII punctuation (curly quotes, em-dashes) common in hip-hop lyrics; threshold lowered to 0.75.
    ascii_ratio = sum(1 for c in text if ord(c) < 128) / max(len(text), 1)
    return ascii_ratio > 0.75


def _assess_song_tone(
    texts,
    *,
    base_url,
    extra_headers,
    model,
    fuse_system_prompt=False,
    meta_title=None,
    meta_artist=None,
    timeout=300,
    use_streaming=False,
    keep_alive=None,
    disable_thinking=False,
):
    """Sample lyric lines and ask the LLM for a plain-language tone/mood summary,
    threaded into every batch's context header for full-song emotional awareness.
    Returns a short string (1-2 sentences) or None on failure (silent — translation
    continues without tone context). Samples up to 20 evenly-spaced lines to keep
    the prompt compact and reduce time-to-first-token.
    """
    if not texts:
        return None

    # ── Sample up to 20 representative lines ──────────────────────────────
    MAX_TONE_LINES = 20
    if len(texts) > MAX_TONE_LINES:
        step = len(texts) / MAX_TONE_LINES
        sample = [texts[int(i * step)] for i in range(MAX_TONE_LINES)]
    else:
        sample = list(texts)

    ctx_parts = []
    if meta_title and meta_title not in ("Unknown", ""):
        ctx_parts.append(f'Song Title: "{meta_title}"')
    if meta_artist and meta_artist not in ("Unknown", ""):
        ctx_parts.append(f'Artist:     "{meta_artist}"')
    context_header = "\n".join(ctx_parts) + "\n\n" if ctx_parts else ""

    numbered = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(sample))

    user_content = (
        f"{context_header}"
        f"Read the following representative song lyric lines carefully.\n"
        f"Describe the song's overall emotional tone and mood in 1–2 sentences.\n"
        f"Be accurate — do not project darkness onto a light song or lightness onto a "
        f"dark one. Base your description solely on what is actually in the lyrics.\n"
        f"Reply with ONLY the tone description — no preamble, no reasoning, no <think> blocks.\n\n"
        f"{numbered}"
    )

    tone_system = (
        "You are a music analyst. Your only job is to read song lyrics and describe "
        "their actual emotional tone accurately and neutrally in 1-2 sentences."
    )

    if fuse_system_prompt:
        messages = [{"role": "user", "content": f"{tone_system}\n\n{user_content}"}]
    else:
        messages = [
            {"role": "system", "content": tone_system},
            {"role": "user",   "content": user_content},
        ]

    # Thinking models need temperature=1 when thinking is active; when thinking
    # is disabled we drop to 0.2 for more deterministic, consistent output.
    tone_temp = 1 if (use_streaming and not disable_thinking) else 0.2
    payload: dict = {
        "model":       model,
        "messages":    messages,
        "temperature": tone_temp,
        "max_tokens":  200,   # tone summary is 1-2 sentences; cap to avoid bloated responses
    }
    # Ollama issue #14820: think=False is silently ignored on /v1/chat/completions (Gemma 4 auto-enables thinking regardless); reasoning_effort="none" is what actually suppresses it here. think=False kept as a harmless no-op for future Ollama versions.
    if disable_thinking:
        payload["think"] = False
        payload["reasoning_effort"] = "none"
    # Keep the model resident in VRAM for the batch calls that follow immediately.
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive

    try:
        merged_headers = {"Content-Type": "application/json", **extra_headers}
        url = f"{base_url}/chat/completions"

        if use_streaming:
            content = _stream_ollama_response(url, merged_headers, payload, timeout)
        else:
            resp = requests.post(url, headers=merged_headers, json=payload, timeout=timeout)
            resp.raise_for_status()
            data    = resp.json()
            raw     = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
            content = _strip_think(raw)

        return content if content else None
    except Exception as exc:
        log(f"[LLM] Tone pre-read failed (non-fatal): {str(exc)[:80]}", "dim yellow")
        return None


def _translate_with_llm(
    texts,
    *,
    base_url,
    extra_headers,
    model_cascade,
    fuse_system_prompt=False,
    system_prompt=None,
    max_tokens=None,
    timeout=120,
    batch_size=8,
    inter_batch_delay=0.0,
    retry_base_delay=4,
    meta_title=None,
    meta_artist=None,
    song_tone=None,
    log_prefix="[LLM]",
    use_streaming=False,
    keep_alive=None,
    disable_thinking=False,
):
    """LLM translation core, called by translate_with_local_llm() (method 5).
    fuse_system_prompt=True merges the system role into the user message
    (required for Gemma via Ollama's OpenAI-compatible layer). model_cascade
    is tried in order, falling back to originals on exhaustion. song_tone,
    when provided, anchors emotional interpretation across every batch.
    """
    if not texts:
        return []

    effective_prompt = system_prompt if system_prompt is not None else _LOCAL_LLM_SYSTEM_PROMPT

    # Song-context header so the model knows what it's translating; song_tone anchors emotional interpretation to the full-song mood even though each request only sees one batch.
    ctx_parts = []
    if meta_title and meta_title not in ("Unknown", ""):
        ctx_parts.append(f'Song Title: "{meta_title}"')
    if meta_artist and meta_artist not in ("Unknown", ""):
        ctx_parts.append(f'Artist:     "{meta_artist}"')
    if song_tone:
        ctx_parts.append(f'Song tone:  {song_tone}')
    context_header = "\n".join(ctx_parts) + "\n\n" if ctx_parts else ""

    PER_MODEL_RETRIES = 3
    results = [None] * len(texts)

    for batch_start in range(0, len(texts), batch_size):
        if batch_start > 0 and inter_batch_delay:
            time.sleep(inter_batch_delay)

        batch = texts[batch_start : batch_start + batch_size]

        # Pre-filter: English lines are returned immediately without hitting the model.
        english_mask = [_is_english_line(line) for line in batch]
        active = [(i, line) for i, line in enumerate(batch) if not english_mask[i]]

        if not active:
            for i, line in enumerate(batch):
                results[batch_start + i] = line
            continue

        # Clamp overly long lines to avoid overflowing the model's context window; configurable via LOCAL_LLM_MAX_LINE_CHARS (0 = no clamping).
        _max_line = CONF.get("LOCAL_LLM_MAX_LINE_CHARS", 800)
        if _max_line and _max_line > 0:
            active = [(i, line[:_max_line]) for i, line in active]

        numbered  = "\n".join(f"{pos + 1}. {line}" for pos, (_, line) in enumerate(active))
        batch_num = batch_start // batch_size + 1

        user_content = (
            f"{context_header}"
            f"Translate the following song lyric lines into natural, expressive English.\n"
            f"Read ALL lines together first to understand the song's theme, emotional arc, "
            f"and recurring imagery — then translate each line so it flows naturally as part "
            f"of that complete song, preserving metaphor, mood, and poetic intent.\n"
            f"Lines that are already in English must be returned exactly as written.\n"
            f"CRITICAL: Reply with exactly the same number of lines as the input. "
            f"Each line MUST start with its number followed by a period (e.g., '1. ', '2. '). "
            f"Do NOT skip or combine lines, do NOT add commentary, "
            f"and do NOT output thinking, reasoning, or <think> blocks — "
            f"go directly to the numbered translations.\n\n"
            f"{numbered}"
        )

        if fuse_system_prompt:
            messages = [{"role": "user", "content": f"{effective_prompt}\n\n{user_content}"}]
        else:
            messages = [
                {"role": "system", "content": effective_prompt},
                {"role": "user",   "content": user_content},
            ]

        # temperature=1 only needed while chain-of-thought is active; drop to 0.2 when thinking is suppressed (mirrors _assess_song_tone's logic).
        base_payload = {
            "messages":    messages,
            "temperature": 1 if (use_streaming and not disable_thinking) else 0.2,
        }
        if max_tokens:
            base_payload["max_tokens"] = max_tokens
        if keep_alive is not None:
            base_payload["keep_alive"] = keep_alive
        # Same Ollama #14820 issue as above — reasoning_effort="none" is what actually suppresses thinking on this endpoint (think=False alone adds 60-90s/batch of silent latency). think=False kept as a no-op for future Ollama versions.
        if disable_thinking:
            base_payload["think"] = False
            base_payload["reasoning_effort"] = "none"

        batch_success = False
        merged_headers = {"Content-Type": "application/json", **extra_headers}

        for model_idx, current_model in enumerate(model_cascade):
            if model_idx > 0:
                log(f"{log_prefix} Batch {batch_num}: primary exhausted — switching to fallback '{current_model}'", "bold yellow")

            payload = {**base_payload, "model": current_model}

            for attempt in range(PER_MODEL_RETRIES):
                if attempt > 0:
                    wait = retry_base_delay * (2 ** (attempt - 1))   # exponential back-off
                    log(f"{log_prefix} Batch {batch_num} retry {attempt}/{PER_MODEL_RETRIES - 1} in {wait}s...", "yellow")
                    time.sleep(wait)
                try:
                    _url = f"{base_url}/chat/completions"
                    if use_streaming:
                    # SSE streaming keeps the read-timeout alive during a thinking pass; _stream_ollama_response strips <think> blocks and raises on HTTP errors.
                        reply = _stream_ollama_response(_url, merged_headers, payload, timeout)
                    else:
                        resp = requests.post(
                            _url,
                            headers=merged_headers,
                            json=payload,
                            timeout=timeout,
                        )
                        if resp.status_code in (429, 500, 502, 503, 504) and attempt < PER_MODEL_RETRIES - 1:
                            log(f"{log_prefix} Batch {batch_num} got HTTP {resp.status_code} — retrying...", "yellow")
                            continue
                        resp.raise_for_status()
                        data = resp.json()

                        if "error" in data:
                            err_info = data["error"]
                            err_msg  = err_info.get("message") if isinstance(err_info, dict) else str(err_info)
                            raise RuntimeError(f"API error in response body: {err_msg}")

                        choices_list = data.get("choices") or []
                        if not choices_list:
                            raise RuntimeError(
                                f"Empty choices list — server may be down. "
                                f"Response keys: {list(data.keys())}"
                            )

                        content = (choices_list[0].get("message") or {}).get("content")
                        if not content:
                            finish = choices_list[0].get("finish_reason", "unknown")
                            raise RuntimeError(f"Empty content (finish_reason='{finish}')")

                        # Strip <think>…</think> blocks emitted by reasoning models
                        # so they never bleed into the numbered translation lines.
                        reply = _strip_think(content).strip()

                    if not reply:
                        raise RuntimeError("Empty reply after stripping thinking blocks")
                    batch_translated = {}
                    reply_lines = reply.splitlines()

                    # Pass 1: loose regex — handles markdown, spaces, brackets around numbers
                    for line in reply_lines:
                        line_str = line.strip()
                        if not line_str:
                            continue
                        m = re.match(r'^(?:[-\*\s\u2022]*)?(?:\[|\()?(\d+)(?:\]|\))?[\s\.\)\[\]:,-]*\s*(.*)', line_str)
                        if m:
                            local_idx    = int(m.group(1)) - 1
                            # lstrip handles markdown bold stuck right after the number (e.g. "**1.** text");
                            # rstrip handles it at the end of the line (e.g. "text**").
                            text_content = m.group(2).strip().lstrip('*_ ').rstrip('*_ ')
                            if 0 <= local_idx < len(active):
                                batch_translated[local_idx] = text_content

                    # Pass 2 fallback: filters LLM preamble/postamble noise, accepts any response with >= expected content lines, trims extras.
                    if len(batch_translated) < len(active):
                        _preamble_re = re.compile(
                            r'^(?:here\s+(?:are|is)\b|sure[,\s]|certainly[,:]|'
                            r'translations?\s*(?:results?)?:|output:|results?:|notes?:)',
                            re.IGNORECASE
                        )
                        clean_lines = []
                        for _p2_line in reply_lines:
                            _s = _p2_line.strip()
                            if not _s:
                                continue
                            if _preamble_re.match(_s):
                                continue
                            _cleaned = re.sub(
                                r'^(?:[-\*\s\u2022]*)?(?:\[|\()?(\d+)(?:\]|\))?[\s\.\)\[\]:,-]*\s*',
                                '', _s
                            )
                            # lstrip handles markdown bold stuck right after the number (e.g. "**1.** text").
                            _cleaned = _cleaned.lstrip('*_ ').rstrip('*_ ').strip()
                            if _cleaned:
                                clean_lines.append(_cleaned)
                        # Accept if we got at least as many content lines as needed.
                        if len(clean_lines) >= len(active):
                            clean_lines = clean_lines[:len(active)]  # trim trailing extras
                            for local_i, trans_text in enumerate(clean_lines):
                                if local_i not in batch_translated:
                                    batch_translated[local_i] = trans_text

                    # Merge translated lines + English pass-throughs back into results
                    for pos, (orig_i, orig_line) in enumerate(active):
                        results[batch_start + orig_i] = batch_translated.get(pos, orig_line)
                    for i, line in enumerate(batch):
                        if english_mask[i]:
                            results[batch_start + i] = line

                    batch_success = True
                    break   # success — exit retry loop

                except Exception as e:
                    err_str = str(e)[:200]
                    if attempt < PER_MODEL_RETRIES - 1:
                        log(f"{log_prefix} Batch {batch_num} attempt {attempt + 1} error: {err_str} — retrying...", "yellow")
                    else:
                        log(f"{log_prefix} Batch {batch_num} '{current_model}' exhausted: {err_str}", "yellow")

            if batch_success:
                break   # no need to try fallback model

        if not batch_success:
            log(f"{log_prefix} Batch {batch_num} failed on all models — keeping originals", "bold red")
            for local_i, orig in enumerate(batch):
                results[batch_start + local_i] = orig

    return [r if r is not None else texts[i] for i, r in enumerate(results)]


def translate_with_local_llm(texts, meta_title=None, meta_artist=None):
    """Translate lyric lines using a locally-running Ollama LLM (method "5")
    via the OpenAI-compatible endpoint at LOCAL_LLM_BASE_URL. Runs a tone
    pre-read pass first so every batch's context header reflects the song's
    overall emotional character, not just its own small window of lines.
    """
    if not texts:
        return []

    base_url          = CONF.get("LOCAL_LLM_BASE_URL",   "http://localhost:11434/v1").rstrip("/")
    model             = CONF.get("LOCAL_LLM_MODEL",      "gemma4:12b")
    _think            = CONF.get("LOCAL_LLM_THINK",      False)  # False = suppress reasoning pass
    _batch_size       = CONF.get("LOCAL_LLM_BATCH_SIZE", 8)      # lines per translation batch
    _disable_thinking = not _think   # invert: THINK=False → disable_thinking=True

    # Pre-read: get a one-sentence tone summary for the whole song so batch translations share consistent mood context.
    log(
        f"[Local LLM] Assessing song tone "
        f"(thinking={'ON — may take ~2 min' if _think else 'OFF'}, batch_size={_batch_size})...",
        "dim cyan",
    )
    song_tone = _assess_song_tone(
        texts,
        base_url=base_url,
        extra_headers={},
        model=model,
        fuse_system_prompt=True,             # required for Gemma models
        meta_title=meta_title,
        meta_artist=meta_artist,
        timeout=300,
        use_streaming=True,                  # keeps read-timeout alive during any think pass
        keep_alive=600,                      # model stays in VRAM for subsequent batch calls
        disable_thinking=_disable_thinking,  # honours LOCAL_LLM_THINK config key
    )
    if song_tone:
        log(f"[Local LLM] Detected tone: {song_tone[:120]}", "dim cyan")

    return _translate_with_llm(
        texts,
        base_url=base_url,
        extra_headers={},                    # Ollama needs no auth headers
        model_cascade=[model],
        fuse_system_prompt=True,             # required for Gemma / Ollama
        system_prompt=_LOCAL_LLM_SYSTEM_PROMPT,   # tone-faithful prompt; no softening
        max_tokens=None,                     # no cap on local inference
        timeout=300,                         # per-token idle budget; streaming keeps this alive
        batch_size=_batch_size,              # honours LOCAL_LLM_BATCH_SIZE config key
                                             # 8 halves round-trips vs old hardcoded 4;
                                             # safe when thinking is OFF (no repetition collapse)
        inter_batch_delay=0.0,
        retry_base_delay=1,                  # localhost never rate-limits; 1 s/2 s is plenty
        meta_title=meta_title,
        meta_artist=meta_artist,
        song_tone=song_tone,                 # full-song tone anchors every batch
        log_prefix="[Local LLM]",
        use_streaming=True,                  # Required for Gemma 4 / thinking models:
                                             # (1) prevents read-timeout during silent think pass
                                             # (2) temperature must be 1 when thinking is ON
        keep_alive=600,                      # keep model warm between batches (no reload penalty)
        disable_thinking=_disable_thinking,  # honours LOCAL_LLM_THINK config key
    )


# Compiled once at module level for _apply_lrc_offset(); matches both line-level [mm:ss.xx] and word-level <mm:ss.xx> tags.
_LRC_TS_PATTERN = re.compile(
    r'(\[)(\d+):(\d+(?:\.\d+)?)(\])|(<)(\d+):(\d+(?:\.\d+)?)(>)'
)

# ---------- LRC Timestamp Offset ----------
def _apply_lrc_offset(lrc_lines, offset_ms):
    """Shift every LRC/Enhanced-LRC timestamp by *offset_ms* ms (+later/-earlier,
    clamped to 0). Handles both line-level [mm:ss.xx] and word-level <mm:ss.xx>
    tags; metadata tags like [ar:...] are never matched since their second
    field starts with a letter, not a digit.
    """
    if not offset_ms:
        return lrc_lines
    offset_s = offset_ms / 1000.0

    def _shift_any(m):
        if m.group(1):   # square-bracket match groups 1-4
            open_b, mins_s, secs_s, close_b = m.group(1), m.group(2), m.group(3), m.group(4)
        else:             # angle-bracket match groups 5-8
            open_b, mins_s, secs_s, close_b = m.group(5), m.group(6), m.group(7), m.group(8)
        total = int(mins_s) * 60 + float(secs_s) + offset_s
        total = max(0.0, total)
        mins  = int(total // 60)
        secs  = total % 60
        # Guard against float-rounding carry (e.g. 59.9995 → 60.00 after {:05.2f})
        if round(secs, 2) >= 60.0:
            mins += 1
            secs -= 60.0
        secs = max(0.0, secs)
        return f"{open_b}{mins:02d}:{secs:05.2f}{close_b}"

    return [_LRC_TS_PATTERN.sub(_shift_any, line) for line in lrc_lines]


# ---------------- Data Gathering & Tagging ----------------
def translate_lrc_file(lrc_path, meta_title=None, meta_artist=None):
    try:
        with open(lrc_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        translated_lines = []
        lrc_data = []

        for line in lines:
            sync_match = re.match(r'^((?:\[\d+:\d+(?:\.\d+)?\])+)(.*)', line)
            meta_match = re.match(r'^\[[a-zA-Z]+:.*\]', line)

            if sync_match and sync_match.group(2).strip():
                raw_content = sync_match.group(2).strip()
                # Safely strip Enhanced LRC word-level tags for translation & string comparisons
                clean_content = re.sub(r'<\d+:\d+(?:\.\d+)?>', '', raw_content)
                lrc_data.append((True, sync_match.group(1), raw_content, clean_content, line))
            elif not meta_match and line.strip() and not re.match(r'^(?:\[\d+:\d+(?:\.\d+)?\])+\s*$', line):
                lrc_data.append((True, "", line.strip(), line.strip(), line))
            else:
                lrc_data.append((False, None, None, None, line))

        texts_to_translate = [item[3] for item in lrc_data if item[0]]
        translated_texts = []

        if texts_to_translate and GLOBAL_TRANS_METHOD != "0":
            if GLOBAL_TRANS_METHOD == "5":
                # Runs outside translation_lock — manages its own timeouts and
                # would otherwise block behind a method-1/3 worker's 1.5s/line sleep.
                try:
                    translated_texts = translate_with_local_llm(
                        texts_to_translate,
                        meta_title=meta_title,
                        meta_artist=meta_artist
                    )
                    if translated_texts == texts_to_translate:
                        log("[Local LLM] All batches fell back to originals — is Ollama running? (ollama serve)", "bold yellow")
                except Exception as e:
                    log(f"[Local LLM] Unexpected error during translation: {e}", "bold red")
            else:
                time.sleep(1)  # Rate-limit buffer; kept outside the lock so other workers aren't blocked

                if GLOBAL_TRANS_METHOD == "1":
                    # Lock acquired/released per line so the rate-limit sleep doesn't block other workers (old design froze all workers for the full sleep duration).
                    for orig in texts_to_translate:
                        success = False
                        src_lang = 'ja' if (GLOBAL_AUDIO_LANG == 'ja' or is_mostly_romaji(orig)) else 'auto'
                        try:
                            with translation_lock:
                                for attempt in range(3):
                                    trans = None
                                    try:
                                        trans = GoogleTranslator(source=src_lang, target='en').translate(orig)
                                    except Exception:
                                        pass
                                    if trans:
                                        translated_texts.append(trans)
                                        success = True
                                        break
                                    # Google returned empty or raised — try MyMemory as fallback.
                                    try:
                                        mym_src = 'japanese' if src_lang == 'ja' else 'autodetect'
                                        trans = MyMemoryTranslator(source=mym_src, target='english').translate(orig)
                                        if trans:
                                            translated_texts.append(trans)
                                            success = True
                                            break
                                    except Exception:
                                        pass   # MyMemory error — fall through to orig below
                                if not success:
                                    translated_texts.append(orig)
                        except Exception as _trans_exc:
                            if CONF.get("DEBUG_MODE"):
                                log(f"Translation method 1 error: {_trans_exc}", "red")
                            if not success:
                                translated_texts.append(orig)
                        # One rate-limit pause per line regardless of retry count; outside the lock so other workers can proceed.
                        time.sleep(1.5)

                elif GLOBAL_TRANS_METHOD == "2":
                    # Single batch call — no per-line sleep — lock held only for
                    # the duration of the one network request.
                    with translation_lock:
                        try:
                            is_romaji_batch = any(is_mostly_romaji(t) for t in texts_to_translate)
                            # MyMemoryTranslator requires 'autodetect' (not 'auto') and full
                            # language names like 'japanese', not BCP-47 codes like 'ja'.
                            mym_src = 'japanese' if (GLOBAL_AUDIO_LANG == 'ja' or is_romaji_batch) else 'autodetect'
                            raw_batch = MyMemoryTranslator(source=mym_src, target='english').translate_batch(texts_to_translate)
                            # translate_batch may return None or a list with None slots on partial
                            # API failure — normalise to originals so callers never see None values.
                            if raw_batch:
                                translated_texts = [
                                    t if t else orig
                                    for t, orig in zip(raw_batch, texts_to_translate)
                                ]
                            else:
                                translated_texts = list(texts_to_translate)
                        except Exception as _trans_exc:
                            if CONF.get("DEBUG_MODE"):
                                log(f"Translation method 2 error: {_trans_exc}", "red")

                elif GLOBAL_TRANS_METHOD == "3":
                    # Per-line lock release + sleep, same reasoning as method 1.
                    for txt in texts_to_translate:
                        src_lang = 'ja' if (GLOBAL_AUDIO_LANG == 'ja' or is_mostly_romaji(txt)) else 'auto'
                        try:
                            with translation_lock:
                                result = GoogleTranslator(source=src_lang, target='en').translate(txt)
                                # translate() may return None on a successful but empty API response
                                translated_texts.append(result if result else txt)
                        except Exception:
                            translated_texts.append(txt)
                        # Rate-limit sleep outside the lock so other workers aren't blocked.
                        time.sleep(1.5)

        t_idx = 0
        for is_lyric, timestamps, original, clean_content, raw_line in lrc_data:
            if is_lyric:
                eng = None
                # Always increment t_idx to stay in sync with texts_to_translate, even on partial translation failures, so we never pull the wrong slot.
                if GLOBAL_TRANS_METHOD != "0":
                    if t_idx < len(translated_texts):
                        eng = translated_texts[t_idx] or None
                    t_idx += 1

                display_original = original
                time_prefix = timestamps if timestamps else ""

                if eng:
                    orig_clean = re.sub(r'[^\w\s]', '', clean_content).lower().strip()
                    eng_clean = re.sub(r'[^\w\s]', '', str(eng)).lower().strip()

                    if orig_clean != eng_clean and eng_clean:
                        eng_final = str(eng).replace('\n', ' ').replace('\r', ' ').strip()

                        translated_lines.append(f"{time_prefix}{display_original}\n")
                        translated_lines.append(f"{time_prefix}（{eng_final}）\n")
                    else:
                        translated_lines.append(f"{time_prefix}{display_original}\n")
                else:
                    translated_lines.append(f"{time_prefix}{display_original}\n")
            else:
                translated_lines.append(raw_line)

        # Apply timestamp offset if configured.
        offset_ms = CONF.get("LRC_OFFSET_MS", 0)
        output_lines = _apply_lrc_offset(translated_lines, offset_ms) if offset_ms else translated_lines

        # Atomic write (temp file + rename) — avoids leaving a zero-byte/partial LRC if the process crashes mid-write.
        lrc_dir = os.path.dirname(lrc_path)
        fd, tmp_path = tempfile.mkstemp(dir=lrc_dir, suffix=".lrc.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.writelines(output_lines)
            os.replace(tmp_path, lrc_path)   # atomic on POSIX; near-atomic on Windows
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    except Exception as e:
        # Always log translation failures — in non-debug mode a short warning
        # is still useful so the user knows the LRC may be incomplete or untranslated.
        log(f"LRC translation/write error (LRC left unchanged): {e}", "yellow"
            if not CONF.get("DEBUG_MODE") else "red")

WHISPER_MODEL = None
ai_lock = threading.Lock()

# ---------------- BATCH-SCOPED COVER ART CACHE ----------------
# When a whole album folder is dropped at once, every track resolves to the
# same cover URL and would otherwise re-download identical image bytes (up to
# ~12x for a full album). Cache by resolved cover URL, thread-safe across the
# worker pool. Capped + LRU-evicted so a long-running --watch daemon can't
# grow this unboundedly over days of operation.
_art_cache_lock = threading.Lock()
_ALBUM_ART_CACHE: "OrderedDict[str, tuple]" = OrderedDict()   # cover_url -> (img_bytes, mime_type)
_ART_CACHE_MAX_ENTRIES = 200

# Companion cache for the MusicBrainz release -> Cover Art Archive lookup itself
# (the CAA JSON/HEAD round-trip that *resolves* cover_url in the first place).
# Every track on the same release shares one release_mbid, so without this the
# JSON+HEAD calls repeat once per track even though _ALBUM_ART_CACHE above
# already dedupes the final image download. "" is cached too (a confirmed
# no-art release), so a release with no cover doesn't get re-probed either.
_caa_cache_lock = threading.Lock()
_CAA_LOOKUP_CACHE: "OrderedDict[str, str]" = OrderedDict()   # release_mbid -> cover_url
_CAA_CACHE_MAX_ENTRIES = 500

# Lock serialising .m3u8 playlist (re)writes — several workers can finish
# tracks from the same album within milliseconds of each other.
_m3u_lock = threading.Lock()

def isolate_vocals_with_demucs(file_path, vid, worker_idx):
    out_dir = os.path.join(LOGS_DIR, f"{vid}_demucs")
    os.makedirs(out_dir, exist_ok=True)

    # Reuse cached stems from disk (retry case) if non-empty and made by the currently configured model; model-specific sub-path avoids stale reuse after a model switch.
    _current_demucs_model = CONF.get("DEMUCS_MODEL", "htdemucs_ft")
    _model_out_dir = os.path.join(out_dir, _current_demucs_model)
    existing_vocals = glob(os.path.join(_model_out_dir, "**", "vocals.*"), recursive=True)
    if existing_vocals:
        _cached = existing_vocals[0]
        if os.path.getsize(_cached) > 0:
            update_ui(worker_idx, msg="Demucs: Reusing cached stems...")
            return _cached, out_dir
        else:
            # Empty file from a previous crashed run — remove and redo separation.
            try:
                os.remove(_cached)
            except Exception:
                pass

    update_ui(worker_idx, msg="Demucs: Isolating Vocals...")

    cmd = [
        sys.executable, "-m", "demucs.separate",
        "-n", CONF.get("DEMUCS_MODEL", "htdemucs_ft"),
        "--two-stems=vocals",
        "-d", "cuda" if torch.cuda.is_available() else "cpu",  # explicit device — Demucs auto-detect diverges from PyTorch on WSL2 / custom CUDA paths
        "-o", out_dir,
        file_path,
    ]

    try:
        if CONF.get("VRAM_SAFE_MODE", True):
            update_ui(worker_idx, msg="Demucs: Waiting for GPU...")
            with gpu_lock:
                update_ui(worker_idx, msg="Demucs: Isolating Vocals...")
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        else:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        vocals_path = glob(os.path.join(out_dir, "**", "vocals.*"), recursive=True)
        if vocals_path:
            return vocals_path[0], out_dir
    except Exception as e:
        if CONF.get("DEBUG_MODE"):
            log(f"Demucs Err: {e}", "red")

    return None, out_dir

def format_timestamp(seconds):
    mins = int(seconds // 60)
    secs = seconds % 60
    if round(secs, 2) >= 60.0:
        mins += 1
        secs -= 60.0
    secs = max(0.0, secs)
    return f"[{mins:02d}:{secs:05.2f}]"


def _format_word_timestamp(seconds):
    """Return an Enhanced-LRC word-level tag <mm:ss.xx> for *seconds*.
    Same math as format_timestamp() but with angle brackets (Enhanced-LRC/A2
    spec); includes the same rounding-carry guard against invalid <01:60.00>.
    """
    mins = int(seconds // 60)
    secs = seconds % 60
    if round(secs, 2) >= 60.0:
        mins += 1
        secs -= 60.0
    secs = max(0.0, secs)
    return f"<{mins:02d}:{secs:05.2f}>"

def _librosa_load_safe(path, sr=22050, mono=True, offset=0.0, duration=None):
    """Drop-in replacement for librosa.load that avoids the PySoundFile/audioread
    warning chain on Windows (soundfile can't decode MP3/AAC/OGG there natively).
    Pipes audio through ffmpeg to a temp WAV file first; falls back to plain
    librosa.load if ffmpeg is unavailable or fails.
    """
    import io, soundfile as _sf

    ffmpeg_exe = "ffmpeg"  # already on PATH via imageio-ffmpeg injection

    # ffmpeg decode → signed 16-bit PCM WAV on stdout. -ss must precede -i for fast input seek (after -i forces a slow full decode-and-discard).
    cmd = [
        ffmpeg_exe,
        "-y",                          # overwrite without prompting
        "-hide_banner", "-loglevel", "error",
    ]
    if offset and offset > 0.0:
        cmd += ["-ss", str(offset)]    # input seek — must precede -i
    cmd += ["-i", path]
    if duration is not None:
        cmd += ["-t", str(duration)]
    if mono:
        cmd += ["-ac", "1"]
    if sr:
        cmd += ["-ar", str(sr)]
    cmd += ["-f", "wav", "-"]          # write WAV to stdout

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        buf = io.BytesIO(result.stdout)
        y, file_sr = _sf.read(buf, dtype="float32", always_2d=False)
        if y.ndim == 2 and mono:
            y = y.mean(axis=1)
        return y, file_sr
    except Exception:
        # ffmpeg unavailable or failed — fall back to librosa's own loader.
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return librosa.load(path, sr=sr, mono=mono, offset=offset,
                                duration=duration)


def analyze_audio_features(audio_path):
    """Analyze BPM and musical key from an audio file.
    BPM via librosa.beat.beat_track() on a 22kHz mono downmix; key via the
    Krumhansl-Schmuckler algorithm (chroma energy vs. K-S profiles, 24 keys).
    Prefers the Demucs instrumental stem when available (locks BPM better
    than vocals or full mix); falls back to the original file otherwise.
    Returns {"bpm": int, "key": str} on success or {} on failure. Key format
    follows KEY_NOTATION: "text" (F# minor), "camelot" (11A), "openkey" (4m).
    """
    try:
        # 22kHz mono is enough for beat/chroma analysis and much faster than full-quality decode; ffmpeg loader avoids soundfile's Windows MP3/AAC/OGG issues.
        y, sr = _librosa_load_safe(audio_path, sr=22050, mono=True)

        # ── BPM ──────────────────────────────────────────────────────────────
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        # librosa ≥ 0.10 may return a 1-element array; ensure we get a plain int.
        bpm = int(round(float(np.atleast_1d(tempo)[0])))

        # ── KEY (Krumhansl-Schmuckler) ── classic K-S profiles (Krumhansl 1990): perceived stability of each chromatic degree vs. the tonic.
        ks_major = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                              2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
        ks_minor = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                              2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

        # Constant-Q chroma gives better pitch accuracy on musical audio than
        # STFT chroma; sum across time to get the overall pitch-class histogram.
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        chroma_mean = chroma.mean(axis=1)   # shape: (12,) — one value per semitone

        # Correlate observed chroma against 24 rotated key profiles; np.roll aligns each template's tonic to chromatic index i (C=0, C#=1...).
        chromatic_notes = ["C", "C#", "D", "Eb", "E", "F",
                           "F#", "G", "Ab", "A", "Bb", "B"]
        best_score = -np.inf
        best_key   = "C major"

        for i, note in enumerate(chromatic_notes):
            major_profile = np.roll(ks_major, i)
            minor_profile = np.roll(ks_minor, i)

            score_major = np.corrcoef(chroma_mean, major_profile)[0, 1]
            score_minor = np.corrcoef(chroma_mean, minor_profile)[0, 1]

            # Guard against NaN from zero-variance inputs (silence, DC offset).
            if np.isfinite(score_major) and score_major > best_score:
                best_score = score_major
                best_key   = f"{note} major"
            if np.isfinite(score_minor) and score_minor > best_score:
                best_score = score_minor
                best_key   = f"{note} minor"

        # Convert to the notation requested in config.
        notation = CONF.get("KEY_NOTATION", "text")
        if notation == "camelot":
            key_str = _CAMELOT_MAP.get(best_key, best_key)
        elif notation == "openkey":
            key_str = _OPENKEY_MAP.get(best_key, best_key)
        else:
            key_str = best_key   # plain text: "F# minor"

        return {"bpm": bpm, "key": key_str}

    except Exception as e:
        if CONF.get("DEBUG_MODE"):
            log(f"Audio feature analysis failed: {e}", "red")
        return {}


# ── Feature 2: Fake-FLAC / lossy-upconvert detection ──
_LOSSLESS_EXTENSIONS = frozenset({'flac', 'wav', 'aiff', 'aif', 'alac'})

def detect_lossy_upconvert(file_path):
    """Check whether a 'lossless' file is actually a lossy upconvert.
    Loads a 30s centre-sample, computes the STFT power spectrum, and checks
    if >= UPCONVERT_ENERGY_THRESHOLD of energy sits below 16kHz (a spectral
    ceiling consistent with lossy encoding). Returns
    {"suspect": bool, "energy_below_16k": float, "threshold_used": float}
    on success or None on failure. Only called for _LOSSLESS_EXTENSIONS files.
    """
    ext = os.path.splitext(file_path)[1].lstrip('.').lower()
    if ext not in _LOSSLESS_EXTENSIONS:
        return None

    threshold = float(CONF.get("UPCONVERT_ENERGY_THRESHOLD", 0.95))

    try:
        # Load 30 s from the middle of the track — intros are often silence-padded
        # and could skew the spectral measurement.
        probe_duration = get_audio_duration(file_path)
        offset = max(0.0, (probe_duration - 30.0) / 2) if probe_duration > 30 else 0.0

        # Use the ffmpeg-based safe loader — avoids the PySoundFile/audioread
        # warning chain on Windows for lossy source files (MP3, AAC, OGG).
        y, sr = _librosa_load_safe(file_path, sr=None, mono=True, offset=offset, duration=30.0)
        if len(y) == 0 or len(y) < sr:   # guard: less than 1 second of audio (librosa always returns ndarray, never None)
            return None

        # Full-bandwidth power spectrum via STFT.
        S = np.abs(librosa.stft(y)) ** 2
        freqs = librosa.fft_frequencies(sr=sr)

        # Total energy across the analysed window.
        power_per_bin = S.mean(axis=1)   # average power per frequency bin
        total_power   = power_per_bin.sum()
        if total_power <= 0:
            return None

        # Energy contained in bins below 16 kHz.
        low_mask  = freqs < 16000
        low_power = power_per_bin[low_mask].sum()
        ratio     = float(low_power / total_power)

        return {
            "suspect":           ratio >= threshold,
            "energy_below_16k":  ratio,
            "threshold_used":    threshold,
        }
    except Exception as e:
        if CONF.get("DEBUG_MODE"):
            log(f"Transcode detection error: {e}", "dim")
        return None


# ── Feature 3: audio quality scoring (duplicate quality-upgrade path) ──
# Format rank: higher = better; used for comparison when bitrate is equal/unavailable (FLAC has no bitrate tag).
_FORMAT_RANK = {'flac': 5, 'wav': 5, 'aiff': 5, 'aif': 5, 'alac': 5,
                'm4a': 3, 'aac': 3, 'ogg': 3, 'opus': 3,
                'mp3': 2, 'wma': 2,
                'mp4': 1, 'webm': 1, 'mkv': 1}

def get_audio_quality_score(file_path):
    """Return a (format_rank, bitrate_kbps) tuple for quality comparison (higher
    is better). Lossless formats get max rank (5) and bitrate 9999 so they
    always outrank lossy formats. Returns (0, 0) on failure.
    """
    try:
        ext = os.path.splitext(file_path)[1].lstrip('.').lower()
        rank = _FORMAT_RANK.get(ext, 0)

        if rank == 5:
            # Lossless — bitrate is meaningless; assign a sentinel value
            # that is higher than any real lossy bitrate.
            return (rank, 9999)

        probe = mutagen.File(file_path)
        bitrate = 0
        if probe and probe.info:
            bitrate = int(getattr(probe.info, 'bitrate', 0) or 0) // 1000   # bps → kbps
        return (rank, bitrate)
    except Exception:
        return (0, 0)

def _prefix_len(a, b):
    """Length of the common prefix of two strings (character-by-character).
    Module-level so it is created once and reusable across call sites.
    """
    n = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        n += 1
    return n

def _forced_align_lyrics(hint_text, segments):
    """Align plain embedded lyrics (*hint_text*) to Whisper's word-level
    timestamps, so the ORIGINAL lyrics text is emitted with accurate timing
    instead of Whisper's re-transcription.
    Flattens all word timestamps into one list, then for each hint line
    slides a forward-only window to find the best-matching word (longest
    common prefix) and builds standard LRC [mm:ss.xx] lines from it.
    Falls back to segment-level timestamps if word timestamps are missing;
    returns None if there are no segments or hint lines.
    """
    if not segments or not hint_text:
        return None

    # Strip residual LRC/Enhanced-LRC markers from the hint (in case the
    # embedded tag somehow contained partial timestamps already).
    clean_hint = re.sub(r'\[\d+:\d+(?:\.\d+)?\]|<\d+:\d+(?:\.\d+)?>', '', hint_text)
    hint_lines = [ln.strip() for ln in clean_hint.splitlines() if ln.strip()]
    if not hint_lines:
        return None

    # ── Build flat word list from Whisper output ──────────────────────────────
    # Each entry: (start_seconds: float, normalised_word: str)
    flat_words = []
    for seg in segments:
        if getattr(seg, 'words', None):
            for w in seg.words:
                nw = _norm(w.word)
                if nw:
                    flat_words.append((w.start, nw))
        else:
            # No word-level data — distribute segment evenly across its tokens.
            tokens = [t for t in seg.text.split() if _norm(t)]
            if tokens:
                dur = (seg.end - seg.start) / len(tokens)
                for i, tok in enumerate(tokens):
                    flat_words.append((seg.start + i * dur, _norm(tok)))

    if not flat_words:
        return None

    # ── Match each lyrics line to the nearest Whisper word ───────────────────
    n_words = len(flat_words)
    n_lines = len(hint_lines)

    # Search window scales with word density (spans long instrumental gaps correctly) — old fixed window of 30 was too small and bunched unmatched lines at the song start.
    words_per_line = max(1, n_words // n_lines)
    SEARCH_WINDOW  = min(max(50, words_per_line * 3), 150)

    song_start = flat_words[0][0]
    song_end   = flat_words[-1][0]
    song_span  = max(song_end - song_start, 1.0)

    lrc_lines   = []
    word_cursor = 0        # forward-only pointer — lyrics are monotonically advancing
    last_ts     = song_start

    for line_idx, line in enumerate(hint_lines):
        line_words = [_norm(w) for w in line.split() if _norm(w)]
        if not line_words:
            continue

        # Slide a window forward from the current cursor.
        search_end = min(word_cursor + SEARCH_WINDOW, n_words)
        best_idx   = -1    # -1 = no real match found yet
        best_score = 0     # require ≥1 matched char in the first word

        for i in range(word_cursor, search_end):
            # ── Multi-word weighted scoring ── score position i on up to 4 consecutive words vs. the lyric line's first 4; earlier words weighted 4x more for tie-breaking.
            score = 0
            for w_off, lw in enumerate(line_words[:4]):
                w_idx = i + w_off
                if w_idx >= n_words:
                    break
                cand_w = flat_words[w_idx][1]
                pref = _prefix_len(lw, cand_w)
                if cand_w == lw:
                    pref += 10          # exact-match bonus
                weight = max(1, 4 - w_off)  # 4, 3, 2, 1 for successive words
                score += pref * weight
            # ──────────────────────────────────────────────────────────────

            if score > best_score:
                best_score = score
                best_idx   = i

        if best_idx >= 0:
            # Good match — use its timestamp and advance cursor past it.
            ts          = flat_words[best_idx][0]
            last_ts     = ts
            word_cursor = best_idx + 1
        else:
            # No match in window — interpolate proportionally across the song span (and advance cursor proportionally) so unmatched lines spread evenly rather than bunching.
            ratio       = line_idx / max(n_lines - 1, 1)
            ts          = song_start + ratio * song_span
            ts          = max(ts, last_ts)      # never go backwards
            last_ts     = ts
            new_cursor  = min(int(ratio * n_words) + 1, n_words - 1)
            word_cursor = max(word_cursor + 1, new_cursor)

        lrc_lines.append(f"{format_timestamp(ts)}{line}\n")
        # (cursor is advanced inside the if/else above)

    return lrc_lines if lrc_lines else None


# Consecutive identical-segment count that trips the hallucination-loop guard; defined at module level to avoid re-creation per call.
_CONSEC_REPEAT_LIMIT = 6   # raised from 3 — songs repeat choruses legitimately 4-5×;
                            # the old limit of 3 silently dropped the 4th+ occurrence.

def _norm_seg(s):
    """Normalise a Whisper segment string for hallucination-loop comparison.
    Collapses whitespace and lowercases so trivially-different repetitions match.
    Module-level so it is created once rather than on every transcribe_audio call.
    """
    return re.sub(r'\s+', ' ', s.lower()).strip()

def _norm(text):
    """Normalise a word for lyric-alignment matching: strip non-word characters and lowercase.
    Module-level so it is created once rather than on every _forced_align_lyrics call.
    """
    return re.sub(r'[^\w]', '', text.lower())

def transcribe_audio(file_path, vid, worker_idx, meta_dict=None, lyrics_hint=None):
    global WHISPER_MODEL
    with ai_lock:
        if WHISPER_MODEL is None:
            from faster_whisper import WhisperModel
            # Auto-detect device; fall back to CPU when CUDA is unavailable.
            device       = "cuda" if torch.cuda.is_available() else "cpu"
            _model_size  = CONF.get("WHISPER_MODEL_SIZE", "large-v3")
            _compute_cfg = CONF.get("WHISPER_COMPUTE_TYPE", "")
            # Respect an explicit config value; otherwise pick the best
            # quantisation level for the available hardware automatically.
            compute_type = (
                _compute_cfg if _compute_cfg
                else ("int8_float16" if device == "cuda" else "int8")
            )
            with cli_lock:
                _live_snap = active_live_ui
                if _live_snap and _live_snap.is_started:
                    _live_snap.stop()
                console.print(
                    f"\n[bold yellow]📡 Initializing Faster-Whisper "
                    f"[bold]{_model_size}[/bold] "
                    f"([dim]{compute_type}[/dim]) on {device.upper()}...[/bold yellow]"
                )
                WHISPER_MODEL = WhisperModel(_model_size, device=device, compute_type=compute_type)
                console.print("[bold green]✅ AI Engine Integrated[/bold green]\n")
                if _live_snap and not _live_snap.is_started:
                    _live_snap.start()

    # ── Build initial_prompt ──
    # initial_prompt primes vocabulary/character-set as "already-transcribed" context, without implying it's literally in the audio.
    # Strategy: base "Song lyrics in <Language>." → override with title+artist → append genre if short → append a native-script hint for CJK.
    _lang_label = {
        "ja": "Japanese", "zh": "Chinese",
        "ko": "Korean",   "en": "English",
        "es": "Spanish",  "fr": "French",
    }.get(GLOBAL_AUDIO_LANG)
    initial_prompt = f"Song lyrics in {_lang_label}." if _lang_label else "Song lyrics."

    if meta_dict:
        t = (meta_dict.get("title")  or "").strip()
        a = (meta_dict.get("artist") or "").strip()
        g = (meta_dict.get("genre")  or "").strip()

        if t and a and a not in ("Unknown", ""):
            initial_prompt = f"Song lyrics for \"{t}\" by {a}."
        elif t:
            initial_prompt = f"Song lyrics for \"{t}\"."

        # Genre tag: only append when short and clean (avoids "Unknown / Various")
        if g and len(g) < 40 and g.lower() not in ("unknown", "various", "other"):
            initial_prompt += f" Genre: {g}."

    # CJK script anchors ("歌詞。"=Lyrics JA/ZH, "가사."=Lyrics KO) bias the tokeniser toward the right character set without adding false pre-transcribed context.
    if GLOBAL_AUDIO_LANG == "ja":
        initial_prompt += " 歌詞。"
    elif GLOBAL_AUDIO_LANG == "zh":
        initial_prompt += " 歌词。"
    elif GLOBAL_AUDIO_LANG == "ko":
        initial_prompt += " 가사."

    # When caller supplies lyrics_hint (pre-existing lyrics the user wants time-aligned), append only the first line (<=80 chars) as a vocabulary primer.
    # CRITICAL: never replace initial_prompt with the full lyrics text — Whisper would treat it as already-spoken and skip ahead, dropping 15-90s of real vocals. Strip residual LRC markers first.
    if lyrics_hint and isinstance(lyrics_hint, str):
        _hint_clean = re.sub(r'\[\d+:\d+(?:\.\d+)?\]|<\d+:\d+(?:\.\d+)?>', '', lyrics_hint)
        # Split on real newlines so we get the first proper lyric line, not
        # the first 80 chars of a long space-collapsed blob.
        _hint_lines = [ln.strip() for ln in _hint_clean.splitlines() if ln.strip()]
        if _hint_lines:
            _first_line = _hint_lines[0][:80]
            # Append; never replace — the metadata context must stay intact.
            initial_prompt = f"{initial_prompt} {_first_line}"
            log("🎤 Using first lyric line as Whisper vocabulary primer (intro preserved)", "cyan")

    vocals_file = None
    demucs_dir  = None
    no_vocals_file = None

    try:
        vocals_file, demucs_dir = isolate_vocals_with_demucs(file_path, vid, worker_idx)
        target_audio = vocals_file if vocals_file else file_path

        # Prefer the no_vocals (instrumental) stem for BPM/key — percussion/bass lock beat tracking better than the vocal stem or full mix.
        if vocals_file:
            _nv_candidates = glob(os.path.join(demucs_dir, "**", "no_vocals.*"), recursive=True)
            no_vocals_file = _nv_candidates[0] if _nv_candidates else None
    except Exception as _demucs_err:
        # Demucs is best-effort — fall back to the raw file rather than aborting
        # the whole transcription.  Log in debug mode so the issue is visible.
        if CONF.get("DEBUG_MODE"):
            log(f"Demucs setup error (falling back to raw audio): {_demucs_err}", "yellow")
        target_audio   = file_path
        vocals_file    = None
        demucs_dir     = None
        no_vocals_file = None

    # Pre-compile phonetic correction patterns once per call (not inside the per-segment loop).
    _phonetic_patterns = [
        (bad, re.compile(re.escape(bad), re.IGNORECASE), good)
        for bad, good in CONF.get("PHONETIC_CORRECTIONS", {}).items()
    ]

    try:
        lrc_lines = []

        # Serialize Whisper inference under gpu_lock (faster-whisper isn't documented thread-safe); 10-min timeout prevents a crashed worker from freezing every other thread.
        update_ui(worker_idx, msg="AI: Waiting for GPU...")
        if not gpu_lock.acquire(timeout=600):
            raise TimeoutError("gpu_lock wait exceeded 10 min — a sibling worker may have crashed while holding it")

        try:
            update_ui(worker_idx, msg="AI Inference Running...")
            # Shared Whisper params, defined once so every pass stays in sync (pass-specific overrides merge on top); all tuning knobs read from config.json.
            _whisper_base = dict(
                language             = GLOBAL_AUDIO_LANG if GLOBAL_AUDIO_LANG != "auto" else None,
                beam_size            = CONF.get("WHISPER_BEAM_SIZE",         5),
                patience             = CONF.get("WHISPER_PATIENCE",          1.5),
                best_of              = CONF.get("WHISPER_BEST_OF",           1),
                temperature          = CONF.get("WHISPER_TEMPERATURE_STEPS", [0.0, 0.2, 0.4, 0.6]),
                log_prob_threshold   = CONF.get("WHISPER_LOGPROB_THRESHOLD", -1.5),
                initial_prompt       = initial_prompt,
                condition_on_previous_text = CONF.get("WHISPER_CONDITION_ON_PREV", False),
                word_timestamps      = CONF.get("WORD_LEVEL_LRC", True),
            )

            # Pass 1 — VAD-filtered transcription tuned for dense/orchestral mixes and accented singing.
            # VAD is only reliable on a clean vocal stem; skip it entirely on a full mix (Demucs failed) or it silently drops the intro.
            _vad_on_stem = vocals_file is not None
            # Build VAD kwargs conditionally — some faster-whisper versions raise TypeError if vad_parameters=None is passed alongside vad_filter=False.
            _vad_kwargs = (
                dict(
                    vad_filter=True,
                    vad_parameters=dict(
                        min_silence_duration_ms=CONF.get("WHISPER_VAD_MIN_SILENCE_MS", 500),
                        threshold=CONF.get("WHISPER_VAD_THRESHOLD", 0.15),
                        speech_pad_ms=CONF.get("WHISPER_VAD_SPEECH_PAD_MS", 600),
                        min_speech_duration_ms=CONF.get("WHISPER_VAD_MIN_SPEECH_MS", 100),
                        max_speech_duration_s=CONF.get("WHISPER_VAD_MAX_SPEECH_S", 60),
                    ),
                )
                if _vad_on_stem
                else dict(vad_filter=False)
            )
            segments_gen, info = WHISPER_MODEL.transcribe(
                target_audio,
                **_whisper_base,
                **_vad_kwargs,
                # Config-driven; default raised 0.45→0.65→0.80 since background music bleed pushes no_speech_prob to 0.65-0.79 on genuinely audible vocals.
                no_speech_threshold=CONF.get("WHISPER_NO_SPEECH_THRESHOLD", 0.80),
                # Default raised 1.8→2.4→2.6 — legitimately repetitive choruses were being flagged as hallucination loops and dropped.
                compression_ratio_threshold=CONF.get("WHISPER_COMPRESSION_RATIO", 2.6),
            )

            if GLOBAL_AUDIO_LANG == "auto":
                update_ui(worker_idx, msg=f"AI: Detected {info.language} ({info.language_probability:.2f})")

            # Feature 3: info.language/info.language_probability are computed on every
            # pass regardless of auto-detect vs. an explicit --lang — persist them onto
            # meta_dict (same pattern as the BPM/key update below) so apply_tags_and_move
            # can write a LANGUAGE/TLAN tag. Previously shown in the UI for a moment, then lost.
            if meta_dict is not None:
                meta_dict["language"] = info.language
                meta_dict["language_confidence"] = round(float(info.language_probability), 3)

            segments = list(segments_gen)

            # Measure coverage: last segment timestamp vs total track duration.
            # info.duration may be 0.0 for FLAC — fall back to mutagen then librosa.
            audio_duration = float(getattr(info, 'duration', None) or 0.0) or get_audio_duration(target_audio)
            # Use last-segment end time for coverage; sum of durations misses
            # tracks that go silent partway through.
            last_timestamp  = segments[-1].end   if segments else 0.0
            first_timestamp = segments[0].start  if segments else audio_duration
            coverage = (last_timestamp / audio_duration) if audio_duration > 0 else 1.0

            # Flag sparse when: end coverage <80% (vocals stop early / VAD missed tail), or first segment starts >15s in (intro skipped by VAD — classic J-Pop/OST bug; threshold lowered from 30s).
            _intro_skipped = (
                first_timestamp > 15.0
                and audio_duration > 60
                and first_timestamp / audio_duration > 0.10  # was 0.15 — too strict
            )
            # Coverage retry applies to ALL track lengths — a short track with only partial output is just as broken as a long one and needs the same retry.
            sparse_result = (coverage < 0.80) or _intro_skipped

            # Pass 2/3 overrides — relaxed thresholds, no VAD.
            _whisper_relaxed = dict(
                vad_filter=False,
                # Further relaxed than Pass 1 — last-resort pass tolerating more uncertainty so quiet vocals aren't discarded; config.json driven.
                no_speech_threshold=CONF.get("WHISPER_NO_SPEECH_THRESHOLD_RELAXED", 0.90),
                # Higher compression ceiling than Pass 1 — extremely repetitive tracks (chants, K-pop refrains) can legitimately hit 2.7-2.9.
                compression_ratio_threshold=CONF.get("WHISPER_COMPRESSION_RATIO_RELAXED", 3.0),
            )

            if not segments or sparse_result:
                if not segments:
                    reason = "VAD muted track entirely"
                elif _intro_skipped:
                    reason = f"intro skipped (vocals begin at {first_timestamp:.0f}s of {audio_duration:.0f}s)"
                else:
                    reason = f"sparse coverage ({coverage:.0%} of {audio_duration:.0f}s)"
                # Label the pass so the dashboard makes clear this is a normal retry,
                # not a failure — the old "retrying raw..." message looked alarming.
                update_ui(worker_idx, msg=f"AI Pass 1: {reason} — trying Pass 2 (no VAD)...")

                # Pass 2 — no VAD at all, but keep the other fixes so we don't
                # hit the same no_speech / compression walls on the raw pass.
                segments_gen, _ = WHISPER_MODEL.transcribe(
                    target_audio,
                    **_whisper_base,
                    **_whisper_relaxed,
                )
                segments = list(segments_gen)

                # Pass 3 — retry on the original audio if Demucs' vocal stem was poor (bleed-through), or if the intro is still skipped after Pass 2.
                if vocals_file:
                    p2_last  = segments[-1].end   if segments else 0.0
                    p2_first = segments[0].start  if segments else audio_duration
                    p2_coverage = (p2_last / audio_duration) if audio_duration > 0 else 1.0
                    # Trigger Pass 3 when coverage is low OR when the intro is still
                    # skipped (p2_first still > 15 s even without VAD on the stem).
                    _p2_intro_still_skipped = _intro_skipped and p2_first > 15.0
                    if p2_coverage < 0.80 or _p2_intro_still_skipped:
                        _p3_reason = (
                            f"intro still at {p2_first:.0f}s" if _p2_intro_still_skipped
                            else f"{p2_coverage:.0%} reach"
                        )
                        update_ui(worker_idx, msg=f"AI Pass 2: {_p3_reason} — Pass 3 retrying on raw audio (Demucs bypass)...")
                        segments_gen, _ = WHISPER_MODEL.transcribe(
                            file_path,
                            **_whisper_base,
                            **_whisper_relaxed,
                        )
                        segments_p3 = list(segments_gen)
                        p3_last  = segments_p3[-1].end   if segments_p3 else 0.0
                        p3_first = segments_p3[0].start  if segments_p3 else audio_duration
                    # Prefer Pass 3 if it reaches further into the track OR captures the intro earlier — the old check only guarded tail coverage.
                        if p3_last > p2_last or (p3_first < p2_first - 5.0):
                            segments = segments_p3

            # Emit final coverage verdict after all passes complete.
            _final_last = segments[-1].end if segments else 0.0
            _final_cov  = (_final_last / audio_duration) if audio_duration > 0 else 1.0
            if audio_duration > 60 and _final_cov < 0.80:
                update_ui(worker_idx, msg=f"AI: Sparse track ({_final_cov:.0%} vocal reach — likely instrumental sections or complex mix)")

            # ── Mid-track gap detection ── the 3-pass system guards intro/tail but misses mid-song instrumental bridges; log gaps >30s (tracks >90s) so the user knows a section was skipped.
            if audio_duration > 90 and len(segments) >= 2:
                _GAP_THRESHOLD = 30.0  # seconds; gaps below this are normal breaths/instrumentals
                _gap_found = False
                for _g_i in range(len(segments) - 1):
                    _gap = segments[_g_i + 1].start - segments[_g_i].end
                    if _gap > _GAP_THRESHOLD:
                        _gap_start = segments[_g_i].end
                        _gap_end   = segments[_g_i + 1].start
                        log(
                            f"⚠️  Mid-track gap detected: {_gap_start:.0f}s – {_gap_end:.0f}s "
                            f"({_gap:.0f}s silence/instrumental — lyrics may be missing here)",
                            "yellow",
                        )
                        _gap_found = True
                if _gap_found:
                    update_ui(worker_idx, msg="AI: Mid-track gap(s) found — see log for details")
            # ─────────────────────────────────────────────────────────────────────

            # ── Hallucination-loop guard ── Whisper can loop on a short phrase over unclear audio (common in intros). Two layers: (1) drop segments the model itself flags as uncertain speech; (2) drop segments repeated consecutively past a limit — a consecutive counter (not a window) avoids wrongly dropping legitimate repeated chorus lines.
            _last_norm:  str  = ""
            _consec_cnt: int  = 0
            # ─────────────────────────────────────────────────────────────────────

            for segment in segments:
                text = segment.text.strip()
                if not text:
                    continue

                # Layer 1 — per-segment no-speech confidence, raised 0.55→0.80→0.95 (config-driven) since music bleed pushes it to 0.80-0.94 on genuine vocals.
                if getattr(segment, 'no_speech_prob', 0.0) > CONF.get("WHISPER_NO_SPEECH_PROB_FILTER", 0.95):
                    continue

                # Layer 2 — consecutive-repeat / hallucination-loop check.
                _norm_text = _norm_seg(text)
                if _norm_text == _last_norm:
                    _consec_cnt += 1
                else:
                    _last_norm  = _norm_text
                    _consec_cnt = 1
                if _consec_cnt > _CONSEC_REPEAT_LIMIT:
                    continue

                text_lower = text.lower()
                changed = False
                for bad_phrase, pattern, correct_phrase in _phonetic_patterns:
                    # lower() the config key before comparing against text_lower (the regex itself already uses IGNORECASE).
                    if bad_phrase.lower() in text_lower:
                        new_text = pattern.sub(correct_phrase, text)
                        if new_text != text:
                            text = new_text
                            # Refresh text_lower after each substitution so chained patterns see the updated text, not the original.
                            text_lower = text.lower()
                            changed = True

                start_str = format_timestamp(segment.start)

                # Word-level LRC only emitted when no phonetic correction fired — corrections are phrase-level and can't be mapped per-word.
                if CONF.get("WORD_LEVEL_LRC", True) and getattr(segment, 'words', None) and not changed:
                    line_content = start_str
                    for w in segment.words:
                        line_content += f"{_format_word_timestamp(w.start)}{w.word}"
                    lrc_lines.append(f"{line_content}\n")
                else:
                    lrc_lines.append(f"{start_str}{text}\n")

            # ── Forced alignment (sync-with-AI, "S" option) ── lyrics_hint holds the original text; align it to Whisper's word timestamps via _forced_align_lyrics() instead of using Whisper's re-transcription. Falls back to raw Whisper output if alignment fails.
            if lyrics_hint and isinstance(lyrics_hint, str) and lrc_lines:
                update_ui(worker_idx, msg="AI: Aligning embedded lyrics to timestamps...")
                aligned = _forced_align_lyrics(lyrics_hint, segments)
                if aligned:
                    lrc_lines = aligned
                    log("🎤 Forced alignment complete — original lyrics mapped to Whisper timestamps", "bold green")
                else:
                    log("⚠️  Forced alignment produced no output — keeping raw Whisper transcription", "yellow")
            # ──────────────────────────────────────────────────────────────────

        finally:
            gpu_lock.release()

        # Analyze BPM/key while Demucs stems are still on disk — prefer the instrumental stem, fall back to the original file if Demucs didn't run.
        update_ui(worker_idx, msg="Analyzing BPM & Key...")
        _analysis_src = no_vocals_file if no_vocals_file else file_path
        _features = analyze_audio_features(_analysis_src)
        if _features and meta_dict is not None:
            meta_dict.update(_features)

        if demucs_dir and os.path.exists(demucs_dir):
            shutil.rmtree(demucs_dir, ignore_errors=True)

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        if not lrc_lines:
            update_ui(worker_idx, msg="[yellow]No speech detected (Instrumental?)[/yellow]")
            time.sleep(1.5)
            return None

        out_path = os.path.join(LOGS_DIR, f"{vid}_lyrics.lrc")
        # Atomic write — same pattern as translate_lrc_file.
        # A bare open("w") leaves a partial file on any crash mid-write.
        _lrc_dir = os.path.dirname(out_path)
        _fd, _tmp = tempfile.mkstemp(dir=_lrc_dir, suffix=".lrc.tmp")
        try:
            with os.fdopen(_fd, "w", encoding="utf-8") as f:
                f.writelines(lrc_lines)
            os.replace(_tmp, out_path)
        except Exception:
            try:
                os.unlink(_tmp)
            except OSError:
                pass
            raise

        update_ui(worker_idx, msg="Formatting & Translating...")
        _meta_t = (meta_dict or {}).get("title")
        _meta_a = (meta_dict or {}).get("artist")
        translate_lrc_file(out_path, meta_title=_meta_t, meta_artist=_meta_a)

        return out_path

    except Exception as e:
        if demucs_dir and os.path.exists(demucs_dir):
            shutil.rmtree(demucs_dir, ignore_errors=True)

        err_str = str(e)[:200]
        update_ui(worker_idx, msg=f"[bold red]AI Err: {err_str}[/bold red]")
        if CONF.get("DEBUG_MODE"):
            log(f"AI Processing Err:\n{traceback.format_exc()}", "red")
        time.sleep(3)
        return None

def _fetch_deezer_genre_by_isrc(isrc):
    """Fetch album-level genre from Deezer via a track ISRC (more reliable
    than MusicBrainz's crowd-sourced tags). Two calls: GET /track/isrc:{isrc}
    to resolve the album ID (the track endpoint has no genre data), then
    GET /album/{album_id} for the genre list. Returns the first genre name,
    or None on failure/no match.
    """
    if not isrc:
        return None
    _headers = {"User-Agent": f"MonsterArchiver/{VERSION}"}
    try:
        t = requests.get(
            f"https://api.deezer.com/track/isrc:{isrc}",
            headers=_headers,
            timeout=4,
        ).json()
        if t.get("error"):
            return None
        album_id = t.get("album", {}).get("id")
        if not album_id:
            return None
        alb = requests.get(
            f"https://api.deezer.com/album/{album_id}",
            headers=_headers,
            timeout=4,
        ).json()
        genres = alb.get("genres", {}).get("data", [])
        if genres:
            return genres[0].get("name") or None
    except Exception:
        pass
    return None


def fetch_smart_metadata(original_title, uploader, worker_idx):
    """Query MusicBrainz, iTunes, and Deezer for track metadata. MusicBrainz
    and iTunes run concurrently (independent calls, halves fetch time);
    Deezer is a serial fallback only called when both return nothing. Uses
    a while-loop for manual re-search so the call stack stays flat.
    """
    query_title    = original_title
    query_uploader = uploader

    while True:   # Loop for manual re-search — no recursion
        clean_title = re.sub(r'\(.*?\)|\[.*?\]|\b(?:official|video|audio|lyrics|mv)\b', '', query_title, flags=re.IGNORECASE).strip()
        clean_title = re.sub(r'^\d+\s*[-\.]\s*', '', clean_title).strip()
        clean_title = re.sub(r'\s+(?:feat|ft)\.?\s+.*$', '', clean_title, flags=re.IGNORECASE).strip()
        # Guard against cleaning stripping the entire title (e.g. "01 - [Audio]")
        # — an empty query wastes all three API calls and returns garbage matches.
        if not clean_title:
            clean_title = query_title

        ignore_uploaders = ["topic", "vevo", "official", "monster siren records", "monster siren", "hypergryph", "arknights", "msr"]
        _uploader_is_usable = query_uploader and query_uploader.lower() != "unknown" and not any(x in query_uploader.lower() for x in ignore_uploaders)
        clean_query = f"{clean_title} {query_uploader}" if _uploader_is_usable else clean_title

        results = []

        # ── Parallel fetch: MusicBrainz + iTunes — independent calls, run concurrently to halve wall-clock time. ──
        def _fetch_musicbrainz():
            mb_hits = []
            try:
                if _uploader_is_usable:
                    mb_query = f'recording:"{clean_title}" AND artist:"{query_uploader}"'
                else:
                    mb_query = f'recording:"{clean_title}"'

                mb_data = musicbrainzngs.search_recordings(query=mb_query, limit=2)

                for rec in mb_data.get('recording-list', []):
                    composer = "Unknown"
                    lyricist = "Unknown"
                    # MusicBrainz tags are crowd-sourced/sparse — treat as a fallback; Deezer ISRC lookup below may upgrade to a curated genre.
                    genre = "Unknown"
                    isrc = ""   # Feature 1: industry-standard recording ID — written to file tags below, not just used to query Deezer.
                    tags = rec.get('tag-list', [])
                    if tags:
                        genre = tags[0].get('name', 'Unknown').title()

                    rec_id = rec.get('id', '')
                    if rec_id:
                        try:
                            rec_detail = musicbrainzngs.get_recording_by_id(
                                rec_id, includes=['work-rels', 'isrcs']
                            )
                            for work_rel in rec_detail['recording'].get('work-relation-list', []):
                                if work_rel.get('type') == 'performance':
                                    work_id = work_rel.get('work', {}).get('id')
                                    if work_id:
                                        work_detail = musicbrainzngs.get_work_by_id(work_id, includes=['artist-rels'])
                                        for ar in work_detail['work'].get('artist-relation-list', []):
                                            role = ar.get('type', '').lower()
                                            name = ar.get('artist', {}).get('name', '')
                                            if not name:
                                                continue
                                            if role == 'composer' and composer == "Unknown":
                                                composer = name
                                            elif role in ('lyricist', 'writer') and lyricist == "Unknown":
                                                lyricist = name

                            # Upgrade genre: Deezer album-level genre via ISRC is more
                            # reliable than MB user tags.  Fall back to MB tags on failure.
                            # Same isrc-list also gives us the ISRC itself (Feature 1) —
                            # previously fetched purely for this genre lookup, then dropped.
                            _isrc_list = rec_detail['recording'].get('isrc-list', [])
                            if _isrc_list:
                                isrc = _isrc_list[0]
                                _dz_genre = _fetch_deezer_genre_by_isrc(isrc)
                                if _dz_genre:
                                    genre = _dz_genre
                        except Exception:
                            pass

                    release_list = rec.get('release-list', [])
                    release = release_list[0] if release_list else {}
                    release_mbid = release.get('id')

                    cover_url = ""
                    if release_mbid:
                        with _caa_cache_lock:
                            _cached_cover = _CAA_LOOKUP_CACHE.get(release_mbid)
                            if _cached_cover is not None:
                                _CAA_LOOKUP_CACHE.move_to_end(release_mbid)   # LRU touch

                        if _cached_cover is not None:
                            # Feature 5: another track in this batch already resolved this exact
                            # release's cover art — reuse it instead of re-hitting CAA.
                            cover_url = _cached_cover
                            if CONF.get("DEBUG_MODE"):
                                log(f"CAA lookup: reused cached release {release_mbid}", "dim cyan")
                        else:
                            # Query the CAA JSON index for the direct full-res front-image URL (avoids the /front redirect hop and thumbnail risk).
                            _caa_api = f"https://coverartarchive.org/release/{release_mbid}"
                            try:
                                _caa_r = requests.get(
                                    _caa_api,
                                    timeout=8,
                                    headers={
                                        "Accept":     "application/json",
                                        "User-Agent": f"MonsterArchiver/{VERSION}",
                                    },
                                )
                                if _caa_r.status_code == 200:
                                    _caa_json = _caa_r.json()
                                    # Prefer approved front images; fall back to first image.
                                    _fronts = [img for img in _caa_json.get("images", []) if img.get("front")]
                                    if not _fronts:
                                        _fronts = _caa_json.get("images", [])
                                    if _fronts:
                                        # `image` is the full-resolution original upload on archive.org.
                                        cover_url = _fronts[0].get("image", "")
                            except Exception as _caa_err:
                                if CONF.get("DEBUG_MODE"):
                                    log(f"MusicBrainz CAA JSON fetch failed for {release_mbid}: {_caa_err}", "dim")
                            # JSON failed or returned no images — fall back to the /front redirect URL.
                            if not cover_url:
                                _caa_front = f"https://coverartarchive.org/release/{release_mbid}/front"
                                try:
                                    if requests.head(_caa_front, allow_redirects=True, timeout=5).status_code == 200:
                                        cover_url = _caa_front
                                except Exception as _caa_fallback_err:
                                    if CONF.get("DEBUG_MODE"):
                                        log(f"MusicBrainz CAA HEAD fallback failed for {release_mbid}: {_caa_fallback_err}", "dim")

                            # Cache the outcome — including "" for a confirmed no-art release — so
                            # the rest of this run never re-queries the same release_mbid.
                            with _caa_cache_lock:
                                _CAA_LOOKUP_CACHE[release_mbid] = cover_url
                                _CAA_LOOKUP_CACHE.move_to_end(release_mbid)
                                while len(_CAA_LOOKUP_CACHE) > _CAA_CACHE_MAX_ENTRIES:
                                    _CAA_LOOKUP_CACHE.popitem(last=False)

                    artist_list = []
                    for ac in rec.get('artist-credit', []):
                        if isinstance(ac, dict) and 'artist' in ac:
                            artist_list.append(ac['artist']['name'])

                    if not artist_list:
                        artist_list = [rec.get('artist-credit-phrase', 'Unknown')]

                    medium_list = release.get('medium-list', [])
                    track_num = '1'
                    disc_num = '1'
                    # Iterate mediums to find which disc this recording is on — track-list is populated only for the containing medium.
                    for medium in medium_list:
                        tl = medium.get('track-list', [])
                        if tl:
                            disc_num  = str(medium.get('position', '1'))
                            track_num = str(tl[0].get('number', '1'))
                            break

                    _date = release.get('date', '')
                    # Release type (Album/Single/EP/etc.) from release-group; not always present, fall back gracefully.
                    _rg      = release.get('release-group', {})
                    _rg_type = (_rg.get('primary-type') or _rg.get('type') or 'Unknown').strip()
                    mb_hits.append({
                        "source": "MusicBrainz",
                        "title": rec.get('title', 'Unknown'),
                        "artist": rec.get('artist-credit-phrase', 'Unknown'),
                        "artist_list": artist_list,
                        "album": release.get('title', 'Unknown Single'),
                        "year": _date[:4] if _date else 'Unknown Year',
                        "track": track_num,
                        "disc": disc_num,
                        "genre": genre,
                        "composer": composer,
                        "lyricist": lyricist,
                        "cover": cover_url,
                        "release_type": _rg_type,
                        # Feature 1: ISRC — reuses the isrc-list fetch above (was queried only for
                        # the Deezer genre lookup and then discarded).
                        "isrc": isrc,
                        # Feature 2: MusicBrainz IDs — rec_id/release_mbid were already fetched to
                        # hit the Cover Art Archive; writing them out lets Picard/MusicBee re-sync
                        # this file later without re-searching from scratch.
                        "mb_track_id": rec_id or "",
                        "mb_album_id": release_mbid or "",
                        # MusicBrainz doesn't report content advisory — left for iTunes/Deezer to fill in.
                        "explicit": "",
                    })
            except Exception as _mb_err:
                if CONF.get("DEBUG_MODE"):
                    log(f"MusicBrainz fetch error: {_mb_err}", "dim red")
            return mb_hits

        def _fetch_itunes():
            it_hits = []
            try:
                r = requests.get(
                    f"https://itunes.apple.com/search?term={urllib.parse.quote(clean_query)}&entity=song&limit=3",
                    headers={'User-Agent': 'Mozilla/5.0'}, timeout=5
                ).json()
                if r.get("resultCount", 0) > 0:
                    for t in r["results"]:
                        _rel_date = t.get("releaseDate", "")
                        # Substitute 100000x100000bb.jpg in the iTunes artwork URL pattern to get the CDN's max-resolution master.
                        _raw_art = t.get("artworkUrl100", "")
                        _hq_art  = re.sub(r'\d+x\d+bb\.(jpg|png)', r'100000x100000bb.jpg', _raw_art) if _raw_art else ""
                        it_hits.append({
                            "source": "iTunes",
                            "title": t.get("trackName", "Unknown"),
                            "artist": t.get("artistName", "Unknown"),
                            "artist_list": split_artists(t.get("artistName", "Unknown")),
                            "album": t.get("collectionName", "Unknown"),
                            "year": _rel_date[:4] if _rel_date else 'Unknown Year',
                            "track": str(t.get("trackNumber", "1")),
                            "disc": str(t.get("discNumber", "1")),
                            "genre": t.get("primaryGenreName", "Unknown"),
                            "composer": t.get("composerName") or "Unknown",
                            "lyricist": "Unknown",
                            "cover": _hq_art,
                            "release_type": t.get("wrapperType", "track").replace("track", "Album").title(),
                            # iTunes Search API doesn't expose ISRC/MBIDs — only MusicBrainz does.
                            "isrc": "",
                            "mb_track_id": "",
                            "mb_album_id": "",
                            # Feature 4: every result already carries this — previously ignored entirely.
                            "explicit": t.get("trackExplicitness", ""),
                        })
            except Exception as _it_err:
                if CONF.get("DEBUG_MODE"):
                    log(f"iTunes fetch error: {_it_err}", "dim red")
            return it_hits

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as _pool:
            _mb_future = _pool.submit(_fetch_musicbrainz)
            _it_future = _pool.submit(_fetch_itunes)
            mb_results_list = _mb_future.result()
            it_results_list = _it_future.result()

        results.extend(mb_results_list)
        results.extend(it_results_list)
        # ────────────────────────────────────────────────────────────────────

        mb_results = [r for r in results if r["source"] == "MusicBrainz"]
        it_results = [r for r in results if r["source"] == "iTunes"]
        if mb_results and it_results:
            for mb in mb_results:
                if not mb.get("cover"): mb["cover"] = it_results[0]["cover"]

        if not results:
            try:
                dz_url = f"https://api.deezer.com/search?q={urllib.parse.quote(clean_query)}&limit=3"
                dz_r = requests.get(dz_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5).json()
                if dz_r.get("data"):
                    for t in dz_r["data"]:
                        dz_genre = "Unknown"
                        alb_r = {}   # populated below only when the album detail call succeeds
                        dz_album_id = t.get("album", {}).get("id")
                        if dz_album_id:
                            try:
                                alb_r = requests.get(
                                    f"https://api.deezer.com/album/{dz_album_id}",
                                    headers={'User-Agent': 'Mozilla/5.0'}, timeout=4
                                ).json()
                                genres = alb_r.get("genres", {}).get("data", [])
                                if genres:
                                    dz_genre = genres[0].get("name", "Unknown")
                            except Exception:
                                pass

                        results.append({
                            "source": "Deezer",
                            "title": t.get("title", "Unknown"),
                            "artist": t.get("artist", {}).get("name", "Unknown"),
                            "artist_list": split_artists(t.get("artist", {}).get("name", "Unknown")),
                            "album": t.get("album", {}).get("title", "Unknown"),
                            # Prefer release_date on the album detail; fall back to the track field.
                            "year": (alb_r.get("release_date", "") or t.get("release_date", ""))[:4] or "Unknown Year",
                            "track": str(t.get("track_position", "1")),
                            "disc": str(t.get("disk_number", "1")),
                            "genre": dz_genre,
                            "composer": "Unknown",
                            "lyricist": "Unknown",
                            "cover": t.get("album", {}).get("cover_xl", ""),
                            "release_type": alb_r.get("record_type", "Unknown").title() if alb_r else "Unknown",
                            # Deezer's track object already includes both fields — no MusicBrainz round-trip needed here.
                            "isrc": t.get("isrc", "") or "",
                            "mb_track_id": "",
                            "mb_album_id": "",
                            "explicit": "explicit" if t.get("explicit_lyrics") else "notExplicit",
                        })
            except Exception: pass

        # Snapshot genuine result count before appending Auto-Single — old code added it first, breaking the len(results)==1 auto-select branch.
        _real_result_count = len(results)

        if results:
            base_match = results[0]
            single_album_name = f"{base_match['title']} - Single"
            already_has_single = any(r.get("album", "").lower() == single_album_name.lower() for r in results)

            # Feature 5: this Auto-Single candidate only ever reaches the user when there's
            # more than one real match to pick between — see the `_real_result_count == 1`
            # branch below, which auto-selects results[0] and returns before this entry is
            # ever looked at. Skipping the lookup when there's exactly one real match removes
            # a guaranteed-wasted iTunes round-trip on the common case of an unambiguous match.
            if _real_result_count > 1 and not already_has_single:
                single_cover = base_match["cover"]
                try:
                    single_query = f"{base_match['title']} {base_match['artist']} Single"
                    itunes_single = requests.get(
                        f"https://itunes.apple.com/search?term={urllib.parse.quote(single_query)}&entity=song&limit=1",
                        headers={'User-Agent': 'Mozilla/5.0'}, timeout=5
                    ).json()
                    if itunes_single.get("resultCount", 0) > 0:
                        _raw_single_art = itunes_single["results"][0].get("artworkUrl100", "")
                        single_cover = re.sub(r'\d+x\d+bb\.(jpg|png)', r'100000x100000bb.jpg', _raw_single_art) if _raw_single_art else single_cover
                except Exception: pass

                results.append({
                    "source": "Auto-Single",
                    "title": base_match["title"],
                    "artist": base_match["artist"],
                    "artist_list": base_match.get("artist_list", split_artists(base_match["artist"])),
                    "album": single_album_name,
                    "year": base_match["year"],
                    "track": "1",
                    "disc": base_match.get("disc", "1"),
                    "genre": base_match.get("genre", "Unknown"),
                    "composer": base_match.get("composer", "Unknown"),
                    "lyricist": base_match.get("lyricist", "Unknown"),
                    "cover": single_cover,
                    "release_type": "Single",
                    # Same underlying recording as base_match — carry its identifiers forward.
                    "isrc": base_match.get("isrc", ""),
                    "mb_track_id": base_match.get("mb_track_id", ""),
                    "mb_album_id": base_match.get("mb_album_id", ""),
                    "explicit": base_match.get("explicit", ""),
                })

        selected_meta = {
            "title": query_title, "artist": query_uploader,
            "artist_list": split_artists(query_uploader),
            "album": "Unknown Album", "year": "Unknown Year", "track": "1",
            "disc": "1",
            "genre": "Unknown", "composer": "Unknown", "lyricist": "Unknown", "cover": "",
            "release_type": "Unknown",
            "isrc": "", "mb_track_id": "", "mb_album_id": "", "explicit": "",
        }

        if _real_result_count == 1:
            selected_meta.update(results[0])
            return selected_meta

        elif len(results) > 1:
            update_ui(worker_idx, msg="[bold yellow]WAITING FOR INPUT[/bold yellow]")
            table = Table(title=f"\U0001f3b5 Multiple Matches Found For: [cyan]{query_title}[/cyan]", box=box.ROUNDED)
            table.add_column("Key", style="bold red", justify="center")
            table.add_column("Source", style="magenta")
            table.add_column("Track Title", style="green")
            table.add_column("Artist", style="yellow")
            table.add_column("Album")
            table.add_column("Type", style="cyan")
            for i, r in enumerate(results):
                _rtype = r.get("release_type", "—") or "—"
                table.add_row(str(i + 1), r["source"], r["title"], r["artist"], r["album"], _rtype)

            choice = pause_ui_and_ask(
                "[bold cyan]Enter Key, 0 to use raw data, or 'M' to manual search[/bold cyan]",
                choices=[str(i) for i in range(len(results)+1)] + ["M", "m"],
                default="1",
                table_to_print=table,
            )
            if choice.lower() == 'm':
                manual_query = pause_ui_and_ask("[bold yellow]Enter Custom Search Query: [/bold yellow]", choices=None, default="")
                if not manual_query:
                    # User submitted an empty query — don't re-run the same API
                    # calls; return defaults to avoid an infinite search loop.
                    return selected_meta
                query_title, query_uploader = manual_query, "Unknown"
                continue   # re-search with new query
            if choice != "0":
                selected_meta.update(results[int(choice)-1])
            return selected_meta

        else:   # 0 results
            update_ui(worker_idx, msg="[bold yellow]WAITING FOR INPUT[/bold yellow]")
            choice = pause_ui_and_ask(
                f"[bold red]No metadata found for '{query_title}'.[/bold red]\n[bold cyan]Press 'M' to manually search or '0' to skip:[/bold cyan]",
                choices=["0", "M", "m"],
                default="0",   # Watch-mode safe: "M" auto-resolves to a bogus manual search the daemon can't handle
            )
            if choice.lower() == 'm':
                manual_query = pause_ui_and_ask("[bold yellow]Enter Custom Search Query: [/bold yellow]", choices=None, default="")
                if manual_query:
                    query_title, query_uploader = manual_query, "Unknown"
                    continue   # re-search with new query
            return selected_meta   # "0" or empty manual → return defaults


def get_lrc_coverage(lrc_text):
    """Return the timestamp of the last synced line in an LRC string, in seconds.
    Returns 0.0 if no timestamps are found.
    Scans every timestamp on every line — LRC allows multiple timestamps on one line
    (e.g. [00:10.00][01:05.00]lyrics) so using re.findall instead of re.search
    prevents the function from returning the first cue instead of the last."""
    last_ts = 0.0
    for line in lrc_text.splitlines():   # splitlines() handles \r\n, \n, and \r
        for m in re.finditer(r'\[(\d+):(\d+(?:\.\d+)?)\]', line):
            ts = int(m.group(1)) * 60 + float(m.group(2))
            if ts > last_ts:
                last_ts = ts
    return last_ts

def _lrc_to_sylt(lrc_text):
    """Convert an LRC/Enhanced-LRC string into a list of (text, timestamp_ms)
    tuples for mutagen.id3.SYLT(text=...). Word-level tags in Enhanced LRC are
    stripped (line-level timestamp kept); bilingual （translation） lines merge
    into the same entry; metadata tags ([ti:...] etc.) and blank timestamp
    lines are skipped. Returns [] if no synced timestamps are found.
    """
    sylt_entries  = []
    pending_text  = None   # original lyric line waiting for a possible translation
    pending_ts_ms = None

    for line in lrc_text.splitlines():
        # Skip LRC metadata tags  [ar:…]  [ti:…]  [by:…]  etc.
        if re.match(r'^\[[a-zA-Z]+:', line):
            if pending_text is not None:
                sylt_entries.append((pending_text, pending_ts_ms))
                pending_text  = None
                pending_ts_ms = None
            continue

        m = re.match(r'^((?:\[\d+:\d+(?:\.\d+)?\])+)(.*)', line)
        if not m:
            # Plain text or blank — flush any buffered line
            if pending_text is not None:
                sylt_entries.append((pending_text, pending_ts_ms))
                pending_text  = None
                pending_ts_ms = None
            continue

        ts_match = re.search(r'\[(\d+):(\d+(?:\.\d+)?)\]', m.group(1))
        if not ts_match:
            continue
        ts_ms = int((int(ts_match.group(1)) * 60 + float(ts_match.group(2))) * 1000)

        # Strip Enhanced-LRC word-level tags  <mm:ss.xx>  to get clean text
        raw_content = m.group(2).strip()
        clean_text  = re.sub(r'<\d+:\d+(?:\.\d+)?>', '', raw_content).strip()

        if not clean_text:
            # Bare timestamp with no lyrics — flush pending, skip
            if pending_text is not None:
                sylt_entries.append((pending_text, pending_ts_ms))
                pending_text  = None
                pending_ts_ms = None
            continue

        # Bilingual translation line  （…）  — merge into the preceding entry
        if clean_text.startswith('（') and clean_text.endswith('）'):
            if pending_text is not None:
                sylt_entries.append((f"{pending_text}\n{clean_text}", pending_ts_ms))
                pending_text  = None
                pending_ts_ms = None
            else:
                # Orphaned translation (no preceding original) — emit as-is
                sylt_entries.append((clean_text, ts_ms))
            continue

        # Normal lyric line — flush any buffered previous line, then buffer this one
        if pending_text is not None:
            sylt_entries.append((pending_text, pending_ts_ms))
        pending_text  = clean_text
        pending_ts_ms = ts_ms

    # Flush the final buffered line
    if pending_text is not None:
        sylt_entries.append((pending_text, pending_ts_ms))

    return sylt_entries

def _score_lyrics_option(opt, meta_artist, audio_duration):
    """Return a higher-is-better integer score for a lyrics candidate.
    Rubric (additive): +40 synced LRC, +20 artist matches meta_artist,
    +15 duration within 5s (+5 within 15s), +5 has >=10 lines, +3 "Embedded
    Tags" source. Weighted so a synced/artist-matched/duration-aligned result
    always beats an unsynced one from a different artist.
    """
    score = 0

    if opt.get("synced"):
        score += 40

    opt_artist = (opt.get("artist") or "").lower().strip()
    ref_artist = (meta_artist or "").lower().strip()
    if opt_artist and ref_artist and opt_artist != "unknown":
        if opt_artist in ref_artist or ref_artist in opt_artist:
            score += 20

    dur = opt.get("dur")
    if dur and dur != "Unknown" and dur != "N/A" and audio_duration and audio_duration > 0:
        try:
            diff = abs(float(dur) - float(audio_duration))
            if diff <= 5:
                score += 15
            elif diff <= 15:
                score += 5
        except (TypeError, ValueError):
            pass

    lyric_text = opt.get("text") or ""
    line_count = sum(1 for ln in lyric_text.splitlines() if ln.strip())
    if line_count >= 10:
        score += 5

    if opt.get("source") == "Embedded Tags":
        score += 3

    return score


def fetch_ultimate_lyrics(meta_title, meta_artist, vid, worker_idx, audio_path=None):
    options = []

    clean_artist = meta_artist
    ignore_artists = ["monster siren records", "monster siren", "hypergryph", "arknights", "msr"]
    if clean_artist and any(x in clean_artist.lower() for x in ignore_artists):
        clean_artist = ""

    # ── Step 0: Embedded lyrics (checked before any network call) ── surfaced as option 1; user can accept, skip to a DB hit, or press A to force AI transcription.
    if audio_path:
        _emb = _extract_embedded_lyrics(audio_path)
        if _emb:
            options.append({
                "source": "Embedded Tags",
                "synced": _emb["synced"],
                "text":   _emb["text"],
                "title":  meta_title,
                "artist": meta_artist,
                "dur":    "N/A",
            })
            log(
                f"🎵 Embedded {'synced ' if _emb['synced'] else 'plain '}lyrics detected "
                "in file — shown as option 1 in the selection menu",
                "cyan",
            )
    # ─────────────────────────────────────────────────────────────────────────

    # ── Step 1: LRCLIB (two-phase: search → per-ID fetch) ── /api/search often omits lyric text, so we fetch each candidate's /api/get/<id> to guarantee the full body.
    try:
        if clean_artist:
            search_query = urllib.parse.quote(f"{meta_title} {clean_artist}")
        else:
            search_query = urllib.parse.quote(meta_title)

        url = f"https://lrclib.net/api/search?q={search_query}"
        # Split (connect=10s, read=30s) timeout via requests — the old single-timeout urllib approach false-timed-out on slow response bodies.
        resp = requests.get(
            url,
            headers={'User-Agent': f'MonsterArchiver/{VERSION}'},
            timeout=(10, 30),
        )
        resp.raise_for_status()
        data = resp.json()

        if data:
            for track in data[:6]:  # expanded from 4 — catches plain-lyrics-only matches ranked lower
                # FIX #2: lrclib uses 'trackName', not 'name'.
                track_title  = track.get('trackName',  track.get('name',       'Unknown'))
                track_artist = track.get('artistName', track.get('artist',     'Unknown'))
                track_dur    = track.get('duration', 0)
                track_id     = track.get('id')

                # Try inline lyrics first (valid for some API responses).
                synced = bool(track.get('syncedLyrics'))
                txt    = track.get('syncedLyrics') or track.get('plainLyrics')

                # FIX #1: inline payload was empty → fall back to /api/get/<id>.
                if not txt and track_id:
                    try:
                        id_url  = f"https://lrclib.net/api/get/{track_id}"
                        id_resp = requests.get(
                            id_url,
                            headers={'User-Agent': f'MonsterArchiver/{VERSION}'},
                            timeout=(10, 30),
                        )
                        id_resp.raise_for_status()
                        id_data = id_resp.json()
                        synced  = bool(id_data.get('syncedLyrics'))
                        txt     = id_data.get('syncedLyrics') or id_data.get('plainLyrics')
                    except Exception as _id_err:
                        # FIX #3: log ID-fetch failures instead of swallowing silently.
                        if CONF.get("DEBUG_MODE"):
                            log(f"LRCLIB /api/get/{track_id} error: {_id_err}", "dim")

                if txt:
                    options.append({
                        "source": "LRCLIB",
                        "synced": synced,
                        "text":   txt,
                        "title":  track_title,
                        "artist": track_artist,
                        "dur":    track_dur,
                    })

    except Exception as _lrc_err:
        # Always surface LRCLIB failures (dim normally, red in debug) so the user knows a source was unavailable, not just in debug mode.
        log(
            f"LRCLIB search error: {_lrc_err}",
            "red" if CONF.get("DEBUG_MODE") else "dim",
        )
    # ─────────────────────────────────────────────────────────────────────────

    sl_query = f"{meta_title} {clean_artist}" if clean_artist else meta_title

    # ── Lyrics supplement chain ── runs unconditionally, even after LRCLIB hits, since providers cover different catalogues; the scoring pass below picks the best. Each tried independently so one failure doesn't block the rest.
    # Priority: Musixmatch (best synced coverage, tried first) → Genius (plain-lyrics fallback) → Megalobiz (broad, catches misses) → NetEase (J-Pop/C-Pop/anime gold standard).
    # Silences syncedlyrics' internal error logging (harmless noise; we handle real exceptions) except in DEBUG_MODE, where full stack traces stay visible.
    _sl_log = logging.getLogger("syncedlyrics")

    for _provider, _label in [
        ("Musixmatch", "Musixmatch"),
        ("Genius",     "Genius"),
        ("Megalobiz",  "Megalobiz"),
        ("NetEase",    "NetEase"),
    ]:
        try:
            _sl_prev_level = _sl_log.level
            if not CONF.get("DEBUG_MODE"):
                _sl_log.setLevel(logging.CRITICAL + 1)
            try:
                _txt = syncedlyrics.search(sl_query, providers=[_provider])
            finally:
                _sl_log.setLevel(_sl_prev_level)

            if _txt:
                options.append({
                    "source": _label,
                    "synced": bool(re.search(r'\[\d+:\d+', _txt)),
                    "text":   _txt,
                    "title":  meta_title,
                    "artist": meta_artist,
                    "dur":    "Unknown",
                })
        except Exception as _prov_err:
            # Always log provider failures at dim level so the user can see
            # which sources silently failed — not just in debug mode.
            log(
                f"{_label} lyrics error: {_prov_err}",
                "red" if CONF.get("DEBUG_MODE") else "dim",
            )
    # ──────────────────────────────────────────────────────────────────────────

    if not options:
        return None

    # ── Feature 5: smart auto-selection ── sort by score descending so option "1" is always the best match, not just whatever came first (watch mode may auto-select "1").
    _audio_dur_for_scoring = get_audio_duration(audio_path) if audio_path else 0.0
    options.sort(
        key=lambda o: _score_lyrics_option(o, meta_artist, _audio_dur_for_scoring),
        reverse=True,
    )
    if CONF.get("DEBUG_MODE") and len(options) > 1:
        for _rank, _o in enumerate(options):
            log(
                f"[Lyrics] Rank {_rank+1}: {_o['source']} / {_o['artist']} "
                f"synced={_o['synced']} dur={_o.get('dur')} "
                f"score={_score_lyrics_option(_o, meta_artist, _audio_dur_for_scoring)}",
                "dim",
            )
    # ──────────────────────────────────────────────────────────────────────

    selected_text   = None
    selected_synced = False   # tracks the chosen option's synced flag for the coverage check below

    if len(options) == 1:
        update_ui(worker_idx, msg="[bold yellow]WAITING FOR INPUT[/bold yellow]")

        table = Table(title=f"🎤 Lyrics Found: [cyan]{meta_title} - {meta_artist}[/cyan]", box=box.ROUNDED)
        table.add_column("Key", style="bold red", justify="center")
        table.add_column("Source", style="magenta")
        table.add_column("Type", style="bold green")
        table.add_column("Track Title", style="cyan")
        table.add_column("Artist", style="yellow")
        table.add_column("Dur (s)", justify="right")
        sync_style = "[bold green]Synced[/bold green]" if options[0]["synced"] else "[bold yellow]Plain[/bold yellow]"
        table.add_row("1", options[0]["source"], sync_style, options[0]["title"], options[0]["artist"], str(options[0]["dur"]))

        # Offer (S) sync whenever the single option is plain, regardless of source — previously gated only on source == "Embedded Tags".
        choice = pause_ui_and_ask(
            "[bold cyan]Select (1) found lyrics, (A) for AI transcription, or (0) to skip[/bold cyan]",
            choices=["0", "1", "A", "a"],
            default="1",
            table_to_print=table,
            force_interactive=True,   # ask even in watch mode — DB lyrics may be poor
        ) if options[0]["synced"] else pause_ui_and_ask(
            "[bold cyan]Select (1) use as plain, (S) sync timestamps with AI, (A) fresh AI transcription, or (0) skip[/bold cyan]",
            choices=["0", "1", "A", "a", "S", "s"],
            default="1",
            table_to_print=table,
            force_interactive=True,
        )
        if choice == "1":
            selected_text   = options[0]["text"]
            selected_synced = options[0]["synced"]
        elif choice.lower() == "s":
            # Stash plain lyrics as a Whisper alignment hint so transcribe_audio force-aligns them to word timestamps instead of re-transcribing.
            with _lyrics_hint_lock:
                _lyrics_hint_store[vid] = options[0]["text"]
            log(f"🎤 Sync-with-AI requested — {options[0]['source']} lyrics queued as Whisper context", "cyan")
            return None
        elif choice.lower() == "a":
            return None  # Signal process_local_file to fall through to AI transcription
        else:  # choice == "0" — user explicitly skipped; must NOT trigger AI transcription
            return _LYRICS_SKIP
    else:
        update_ui(worker_idx, msg="[bold yellow]WAITING FOR INPUT[/bold yellow]")

        table = Table(title=f"🎤 Multiple Lyrics Found For: [cyan]{meta_title} - {meta_artist}[/cyan]", box=box.ROUNDED)
        table.add_column("Key", style="bold red", justify="center")
        table.add_column("Source", style="magenta")
        table.add_column("Type", style="bold green")
        table.add_column("Track Title", style="cyan")
        table.add_column("Artist", style="yellow")
        table.add_column("Dur (s)", justify="right")

        for i, opt in enumerate(options):
            sync_style = "[bold green]Synced[/bold green]" if opt["synced"] else "[bold yellow]Plain[/bold yellow]"
            table.add_row(str(i+1), opt["source"], sync_style, opt["title"], opt["artist"], str(opt["dur"]))

        # Collect all plain-lyrics option numbers — S is offered if ANY exist, not just when option 1 happens to be plain.
        _plain_idxs = [i + 1 for i, o in enumerate(options) if not o["synced"]]
        _has_plain = bool(_plain_idxs)

        if _has_plain:
            if len(_plain_idxs) == 1:
                _s_hint = f"(S) sync option {_plain_idxs[0]} with AI"
            else:
                _s_hint = f"(S) sync a plain option ({'/'.join(str(i) for i in _plain_idxs)}) with AI"
            _multi_prompt  = f"[bold cyan]Select (1-{len(options)}), {_s_hint}, (A) fresh AI, or (0) skip[/bold cyan]"
            _multi_choices = [str(i) for i in range(len(options) + 1)] + ["A", "a", "S", "s"]
        else:
            _multi_prompt  = f"[bold cyan]Select Lyrics (1-{len(options)}), (A) for AI transcription, or (0) to skip[/bold cyan]"
            _multi_choices = [str(i) for i in range(len(options) + 1)] + ["A", "a"]

        choice = pause_ui_and_ask(
            _multi_prompt,
            choices=_multi_choices,
            default="1",
            table_to_print=table,
            force_interactive=True,   # ask even in watch mode — DB lyrics may be poor
        )

        if choice.lower() == "s":
            # Determine which plain option to sync as Whisper alignment context.
            if len(_plain_idxs) == 1:
                # Only one plain option — use it directly.
                _sync_idx = _plain_idxs[0]
            else:
                # Multiple plain options — ask the user which one to sync.
                _plain_str = "/".join(str(i) for i in _plain_idxs)
                _sync_choice = pause_ui_and_ask(
                    f"[bold cyan]Sync which option? ({_plain_str})[/bold cyan]",
                    choices=[str(i) for i in _plain_idxs],
                    default=str(_plain_idxs[0]),
                    force_interactive=True,
                )
                try:
                    _sync_idx = int(_sync_choice)
                    if _sync_idx not in _plain_idxs:
                        raise ValueError
                except (ValueError, TypeError):
                    log("⚠️  Invalid sync choice — falling through to AI transcription", "yellow")
                    return None

            _sync_opt = options[_sync_idx - 1]
            with _lyrics_hint_lock:
                _lyrics_hint_store[vid] = _sync_opt["text"]
            log(
                f"🎤 Sync-with-AI requested — option {_sync_idx} ({_sync_opt['source']}) "
                "lyrics queued as Whisper context",
                "cyan",
            )
            return None
        elif choice.lower() == "a":
            return None  # Fall through to AI transcription
        elif choice != "0":
            _sel_idx        = int(choice) - 1
            selected_text   = options[_sel_idx]["text"]
            selected_synced = options[_sel_idx]["synced"]
        else:  # choice == "0" — user explicitly skipped; must NOT trigger AI transcription
            return _LYRICS_SKIP

    if selected_text:
        # Reject synced lyrics covering <50% of the track, falling through to AI. Use the option's own `synced` flag, not get_lrc_coverage()>0 — plain Genius text can contain bracket markers that look like timestamps.
        if selected_synced and audio_path:
            audio_duration = get_audio_duration(audio_path)
            if audio_duration > 60:
                lrc_end = get_lrc_coverage(selected_text)
                if lrc_end > 0:   # sanity guard — only meaningful for true LRC content
                    ratio = lrc_end / audio_duration
                    if ratio < 0.50:
                        update_ui(worker_idx, msg=f"[yellow]Lyrics only cover {ratio:.0%} of track — falling through to AI[/yellow]")
                        return None

        out_path = os.path.join(LOGS_DIR, f"{vid}_lyrics.lrc")
        # Atomic write (same pattern as translate_lrc_file) — avoids a partial/empty .lrc being read back as valid.
        _lrc_dir = os.path.dirname(out_path)
        _fd, _tmp = tempfile.mkstemp(dir=_lrc_dir, suffix=".lrc.tmp")
        try:
            with os.fdopen(_fd, "w", encoding="utf-8") as f:
                f.write(selected_text)
            os.replace(_tmp, out_path)
        except Exception:
            try:
                os.unlink(_tmp)
            except OSError:
                pass
            raise
        update_ui(worker_idx, msg="Translating")
        translate_lrc_file(out_path, meta_title=meta_title, meta_artist=meta_artist)
        return out_path

    return None

# ---------- Embedded Art Fallback ----------
def _extract_embedded_art(file_path):
    """Extract the first embedded cover image from an audio file.
    Returns (image_bytes, mime_type) or (None, None) if no art is found."""
    try:
        ext = os.path.splitext(file_path)[1].lstrip('.').lower()
        if ext == 'flac':
            audio = FLAC(file_path)
            if audio.pictures:
                p = audio.pictures[0]
                return p.data, (p.mime or "image/jpeg")
        elif ext == 'mp3':
            audio = MP3(file_path, ID3=ID3)
            if audio.tags:
                apics = audio.tags.getall('APIC')
                if apics:
                    return apics[0].data, (apics[0].mime or "image/jpeg")
        elif ext in ('m4a', 'aac'):
            audio = MP4(file_path)
            if audio.tags and 'covr' in audio.tags:
                covers = audio.tags['covr']
                if covers:
                    c = covers[0]
                    mime = 'image/png' if c.imageformat == MP4Cover.FORMAT_PNG else 'image/jpeg'
                    return bytes(c), mime
        else:
            # Generic mutagen fallback (ogg, opus, etc.)
            audio = mutagen.File(file_path)
            if audio and hasattr(audio, 'pictures') and audio.pictures:
                p = audio.pictures[0]
                return p.data, (getattr(p, 'mime', None) or "image/jpeg")
    except Exception:
        pass
    return None, None


# ---------- Embedded Lyrics Extractor ----------
def _extract_embedded_lyrics(file_path):
    """Detect and extract lyrics already embedded in a file's tags.
    Priority: MP3 → SYLT (converted to LRC) then USLT; FLAC → LYRICS/
    UNSYNCEDLYRICS; M4A → ©lyr; other → generic mutagen 'lyrics' key.
    Returns {"text": str, "synced": bool} or None if not found; synced=True
    text is LRC-formatted ([mm:ss.xx]Line), synced=False is plain text.
    """
    if not file_path or not os.path.exists(file_path):
        return None

    ext = os.path.splitext(file_path)[1].lstrip('.').lower()

    try:
        if ext == 'mp3':
            audio = MP3(file_path, ID3=ID3)
            if audio.tags:
                # ── SYLT ── entries are (text, timestamp_ms); convert to LRC so translate_lrc_file's existing pipeline can use it directly.
                sylt_frames = audio.tags.getall('SYLT')
                if sylt_frames and sylt_frames[0].text:
                    lrc_lines = []
                    for text, ts_ms in sylt_frames[0].text:
                        if text and text.strip():
                            mins = ts_ms // 60000
                            secs = (ts_ms % 60000) / 1000
                            # Guard against float-rounding carry (same fix as format_timestamp):
                            # ts_ms % 60000 can be 59999, giving secs=59.999 → formats as "60.00".
                            if round(secs, 2) >= 60.0:
                                mins += 1
                                secs -= 60.0
                            secs = max(0.0, secs)
                            lrc_lines.append(f"[{mins:02d}:{secs:05.2f}]{text.strip()}")
                    if lrc_lines:
                        return {"text": "\n".join(lrc_lines), "synced": True}

                # ── USLT (Unsynchronized Lyrics) ──────────────────────────────
                uslt_frames = audio.tags.getall('USLT')
                if uslt_frames:
                    text = uslt_frames[0].text
                    if text and text.strip():
                        return {"text": text.strip(), "synced": False}

        elif ext == 'flac':
            audio = FLAC(file_path)
            # LYRICS is the Plex/Jellyfin/foobar2000 standard; some taggers
            # write UNSYNCEDLYRICS instead — check both.
            for key in ('LYRICS', 'lyrics', 'UNSYNCEDLYRICS', 'unsyncedlyrics'):
                val = audio.get(key)
                if val and val[0].strip():
                    text = val[0].strip()
                    # Distinguish LRC from plain text by presence of timestamps
                    synced = bool(re.search(r'\[\d+:\d+', text))
                    return {"text": text, "synced": synced}

        elif ext in ('m4a', 'aac', 'mp4'):
            audio = MP4(file_path)
            if audio.tags:
                # ©lyr is the canonical iTunes/M4A lyrics atom.
                # Mutagen may expose it as '\xa9lyr' (raw byte) or '©lyr'.
                lyr_key = '\xa9lyr'   # U+00A9 — canonical iTunes/M4A lyrics atom
                if lyr_key in audio.tags:
                    val = audio.tags[lyr_key]
                    if val:
                        text = str(val[0]).strip()
                        if text:
                            synced = bool(re.search(r'\[\d+:\d+', text))
                            return {"text": text, "synced": synced}

        else:
            # Generic mutagen fallback: OGG, Opus, WMA, etc.
            audio = mutagen.File(file_path)
            if audio:
                for key in ('lyrics', 'LYRICS', 'unsyncedlyrics', 'UNSYNCEDLYRICS'):
                    val = audio.get(key)
                    if val:
                        text = (val[0] if isinstance(val, list) else str(val)).strip()
                        if text:
                            synced = bool(re.search(r'\[\d+:\d+', text))
                            return {"text": text, "synced": synced}

    except Exception as e:
        if CONF.get("DEBUG_MODE"):
            log(f"Embedded lyrics extraction error: {e}", "dim")

    return None
# -----------------------------------------------


def _strip_lrc_timestamps(text):
    """Remove LRC [mm:ss.xx] and Enhanced-LRC <mm:ss.xx> markers from *text*,
    leaving clean lyrics for plain-text tag atoms (USLT, ©lyr). Also strips
    leading/trailing whitespace.
    """
    text = re.sub(r'(?m)^(?:\[\d+:\d+(?:\.\d+)?\])+', '', text)
    text = re.sub(r'<\d+:\d+(?:\.\d+)?>', '', text)
    return '\n'.join(ln.strip() for ln in text.splitlines()).strip()


def _unique_path(path):
    """Return `path` unchanged if free, otherwise the same path with a
    `_1`, `_2`, ... counter inserted before the extension — never overwrites
    an existing file. Falls back to a millisecond-timestamp suffix past 9999
    collisions. Shared by _compute_destination()'s collision guard and the
    album-merge pass's file moves.
    """
    if not os.path.exists(path):
        return path
    base, dot_ext = os.path.splitext(path)
    counter = 1
    candidate = f"{base}_{counter}{dot_ext}"
    while os.path.exists(candidate) and counter < 9999:
        counter += 1
        candidate = f"{base}_{counter}{dot_ext}"
    if os.path.exists(candidate):
        candidate = f"{base}_{int(time.time() * 1000)}{dot_ext}"
    return candidate


def _compute_destination(file_path, meta):
    """Work out the library folder + final filename for a track's metadata.

    Side effects limited to read-only os.path.exists() collision checks —
    no makedirs, no copy. Shared by apply_tags_and_move() (the real archive
    pipeline) and the --dry-run preview path, so a preview's reported
    destination is guaranteed to match what a real run would produce.

    Returns (folder_structure, new_filepath, track_formatted).
    """
    ext = os.path.splitext(file_path)[1].lstrip('.').lower()

    safe_artist = sanitize_filename(meta["artist"], 40)
    safe_album  = sanitize_filename(meta["album"], 50)
    safe_title  = sanitize_filename(meta["title"], 60)
    safe_year   = sanitize_filename(str(meta.get("year", "Unknown Year")), 20)
    folder_structure = os.path.join(MUSIC_DIR, safe_artist, f"{safe_year} - {safe_album}")

    try:
        track_formatted = f"{int(meta['track']):02d}"
    except Exception:
        track_formatted = str(meta.get('track', '1'))

    new_filename = f"{track_formatted} - {safe_title}.{ext}"
    new_filepath = os.path.join(folder_structure, new_filename)

    # Collision guard — append a counter instead of overwriting an existing file of the same name.
    new_filepath = _unique_path(new_filepath)

    return folder_structure, new_filepath, track_formatted


def apply_tags_and_move(file_path, meta, lyrics_path=None):
    ext = os.path.splitext(file_path)[1].lstrip('.').lower()
    folder_structure, new_filepath, track_formatted = _compute_destination(file_path, meta)

    # Composer/lyricist fallback to artist name — metadata sources (MusicBrainz/iTunes/Deezer) often return "Unknown" for phonk/Telegram-sourced tracks.
    effective_composer = (meta.get("composer") or "").strip()
    if not effective_composer or effective_composer == "Unknown":
        effective_composer = (meta.get("artist") or "").strip()

    effective_lyricist = (meta.get("lyricist") or "").strip()
    if not effective_lyricist or effective_lyricist == "Unknown":
        effective_lyricist = (meta.get("artist") or "").strip()

    # Feature 4: normalise the advisory flag once up front — reused across all three format blocks below.
    _explicit_val = _explicit_flag(meta)
    # Feature 3: 3-letter language code for LANGUAGE/TLAN, once up front too.
    _lang_code = _ISO_639_1_TO_2T.get(meta.get("language", ""), meta.get("language", "")) if meta.get("language") else ""

    os.makedirs(folder_structure, exist_ok=True)

    shutil.copy2(file_path, new_filepath)

    try:
        audio = mutagen.File(new_filepath, easy=True)
        if audio is not None:
            audio.delete()         # wipe any stale tags from a previous run before writing fresh ones
            audio["title"] = meta["title"]
            audio["artist"] = meta["artist_list"]
            audio["album"] = meta["album"]
            if meta.get("year") and str(meta["year"]) not in ("Unknown Year", "Unknown", ""):
                audio["date"] = str(meta["year"])
            audio["tracknumber"] = track_formatted    # zero-padded ("03") — matches filename and sorts correctly
            if meta.get("genre") and meta["genre"] != "Unknown":
                audio["genre"] = meta["genre"]
            # Write composer + lyricist via easy tags for universal format support;
            # format-specific sections below re-apply them via native tag types.
            if effective_composer:
                try:
                    audio["composer"] = [effective_composer]
                except Exception:
                    pass
            if effective_lyricist:
                try:
                    audio["lyricist"] = [effective_lyricist]
                except Exception:
                    pass
            audio.save()
    except Exception as e:
        if CONF.get("DEBUG_MODE"):
            log(f"Easy-tag write failed: {e}", "red")

    img_data = None
    mime_type = "image/jpeg"

    if meta.get("cover"):
        _cover_url = meta["cover"]

        # Feature 5: batch-scoped cache — an album drop resolves every track to the
        # same cover URL, so check for already-downloaded bytes before hitting the network.
        with _art_cache_lock:
            _cache_hit = _ALBUM_ART_CACHE.get(_cover_url)
            if _cache_hit is not None:
                _ALBUM_ART_CACHE.move_to_end(_cover_url)   # LRU touch

        if _cache_hit is not None:
            img_data, mime_type = _cache_hit
            if CONF.get("DEBUG_MODE"):
                log(f"Cover: reused cached art for this batch ({mime_type})", "dim cyan")
        else:
            # Retry up to 3x with exponential backoff — large 4K cover art (10+MB) could exceed the old single-attempt timeout.
            for _art_attempt in range(3):
                try:
                    img_req = requests.get(
                        _cover_url,
                        headers={'User-Agent': 'Mozilla/5.0'},
                        timeout=30,    # raised from 10 → 30 s for 4K images
                    )
                    if img_req.status_code == 200:
                        img_data = img_req.content
                        # Read MIME from the actual server response, not guessed from the URL string (which may contain misleading text like "png").
                        ct = img_req.headers.get("Content-Type", "image/jpeg").split(";")[0].strip().lower()
                        mime_type = ct if ct in ("image/jpeg", "image/png", "image/webp", "image/gif") else "image/jpeg"
                        break   # success — exit retry loop
                    # Non-200 from the server — no point retrying the same URL
                    break
                except requests.exceptions.Timeout:
                    if _art_attempt < 2:
                        time.sleep(2 ** _art_attempt)   # back-off: 1 s, 2 s
                except Exception:
                    if _art_attempt < 2:
                        time.sleep(2 ** _art_attempt)
                    else:
                        pass

            if img_data:
                with _art_cache_lock:
                    _ALBUM_ART_CACHE[_cover_url] = (img_data, mime_type)
                    _ALBUM_ART_CACHE.move_to_end(_cover_url)
                    while len(_ALBUM_ART_CACHE) > _ART_CACHE_MAX_ENTRIES:
                        _ALBUM_ART_CACHE.popitem(last=False)   # evict oldest

    # Fall back to art already embedded in the source file when no
    # online cover was found — preserves artwork from well-tagged rips.
    if not img_data:
        img_data, mime_type = _extract_embedded_art(file_path)
        if img_data and CONF.get("DEBUG_MODE"):
            log(f"Cover: using embedded art from source file ({mime_type})", "cyan")

    try:
        if ext == "flac":
            f_audio = FLAC(new_filepath)
            if img_data:
                # The copied source file may already have picture blocks; add_picture() only appends, so clear first or the old low-res art wins as the first block in most players.
                f_audio.clear_pictures()
                pic = Picture()
                pic.type = 3
                pic.mime = mime_type
                pic.data = img_data
                f_audio.add_picture(pic)
            if lyrics_path:
                with open(lyrics_path, "r", encoding="utf-8") as f:
                    txt = f.read()
                    f_audio["LYRICS"] = txt  # standard Vorbis tag — recognised by Plex, Jellyfin, foobar2000
            if meta.get("genre") and meta["genre"] != "Unknown":
                f_audio["GENRE"] = meta["genre"]
            if effective_composer:
                f_audio["COMPOSER"] = effective_composer
            if effective_lyricist:
                f_audio["LYRICIST"] = effective_lyricist
            if meta.get("bpm"):
                f_audio["BPM"] = str(meta["bpm"])
            if meta.get("key"):
                f_audio["INITIALKEY"] = str(meta["key"])
            # Disc number for multi-disc albums
            if meta.get("disc") and str(meta["disc"]) not in ("", "1"):
                f_audio["DISCNUMBER"] = str(meta["disc"])
            f_audio["ARTIST"] = meta["artist_list"]
    # ALBUMARTIST keeps multi-artist albums grouped across all players; always a single normalised string, even when ARTIST is a list.
            f_audio["ALBUMARTIST"] = meta.get("artist") or (meta["artist_list"][0] if meta["artist_list"] else "Unknown")
            f_audio.save()

        elif ext == "mp3":
            m_audio = MP3(new_filepath, ID3=ID3)
            if m_audio.tags is None:
                m_audio.add_tags()
            if img_data:
                # APIC frames are keyed by desc, not type — tags.add() alone just inserts a 2nd frame, leaving the old one (which most players show first) intact. delall() clears every APIC frame regardless of desc.
                m_audio.tags.delall('APIC')
                m_audio.tags.add(APIC(encoding=3, mime=mime_type, type=3, desc='Cover', data=img_data))
            if lyrics_path:
                with open(lyrics_path, "r", encoding="utf-8") as f:
                    txt = f.read()
                # USLT — plain-text fallback for players without SYLT support; strip LRC timestamp markers first.
                _uslt_text = _strip_lrc_timestamps(txt)
                m_audio.tags.add(USLT(encoding=3, lang='eng', desc='', text=_uslt_text))
                # SYLT — synced scrolling lyrics (foobar2000, Poweramp, Rockbox); only added when the source actually has timestamps.
                sylt_entries = _lrc_to_sylt(txt)
                if sylt_entries:
                    m_audio.tags.add(SYLT(
                        encoding=3,
                        lang='eng',
                        format=2,   # 2 = absolute milliseconds
                        type=1,     # 1 = lyrics/text
                        desc='',
                        text=sylt_entries,
                    ))
            if meta.get("genre") and meta["genre"] != "Unknown":
                m_audio.tags.add(TCON(encoding=3, text=meta["genre"]))
            if effective_composer:
                m_audio.tags.add(TCOM(encoding=3, text=effective_composer))
            if effective_lyricist:
                m_audio.tags.add(TEXT(encoding=3, text=[effective_lyricist]))
            if meta.get("bpm"):
                m_audio.tags.add(TBPM(encoding=3, text=str(meta["bpm"])))
            if meta.get("key"):
                m_audio.tags.add(TKEY(encoding=3, text=meta["key"]))
            # Disc number for multi-disc albums (TPOS = Part of Set)
            if meta.get("disc") and str(meta["disc"]) not in ("", "1"):
                m_audio.tags.add(TPOS(encoding=3, text=str(meta["disc"])))
            m_audio["TPE1"] = TPE1(encoding=3, text=meta["artist_list"])
            # TPE2 = Album Artist — single normalised string, not a list, matching Plex/Rockbox/foobar2000 expectations.
            m_audio.tags.add(TPE2(encoding=3, text=[meta.get("artist") or (meta["artist_list"][0] if meta["artist_list"] else "Unknown")]))
            m_audio.save()

        # M4A/AAC: add cover art, lyrics, and lyricist freeform atom on top of the easy-tag pass.
        elif ext in ("m4a", "aac"):
            m4_audio = MP4(new_filepath)
            if m4_audio.tags is None:
                m4_audio.add_tags()
            if img_data:
                if "png" in mime_type:
                    cover_format = MP4Cover.FORMAT_PNG
                else:
                    # MP4Cover has no WebP constant — JPEG atom is the safest
                    # fallback for both image/jpeg and image/webp payloads.
                    cover_format = MP4Cover.FORMAT_JPEG
                m4_audio["covr"] = [MP4Cover(img_data, imageformat=cover_format)]
            # M4A ©lyr atom keeps line-level [mm:ss.xx] timestamps (players like AIMP detect and scroll them); word-level <mm:ss.xx> tags are stripped as unsupported visual noise.
            if lyrics_path:
                with open(lyrics_path, "r", encoding="utf-8") as f:
                    _m4a_lrc_raw = f.read()
                # Strip only word-level Enhanced-LRC tags; keep line-level [mm:ss.xx] timestamps.
                _m4a_lyr = re.sub(r'<\d+:\d+(?:\.\d+)?>', '', _m4a_lrc_raw)
                m4_audio["©lyr"] = [_m4a_lyr]
            if effective_lyricist:
                m4_audio["----:com.apple.iTunes:LYRICIST"] = [
                    MP4FreeForm(effective_lyricist.encode("utf-8"), dataformat=AtomDataType.UTF8)
                ]
            if effective_composer:
                # ©wrt is the canonical iTunes/M4A composer atom recognised by all players.
                m4_audio["\xa9wrt"] = [effective_composer]
            # aART = Album Artist — keeps multi-artist albums grouped correctly in
            # Apple Music, Infuse, Jellyfin, and other M4A-aware players.
            m4_audio["aART"] = [meta.get("artist") or (meta["artist_list"][0] if meta["artist_list"] else "Unknown")]
            if meta.get("bpm"):
                # tmpo is a native M4A integer atom (16-bit unsigned); all players
                # including Plex, Jellyfin, and iTunes recognise it natively.
                m4_audio["tmpo"] = [int(meta["bpm"])]
            if meta.get("key"):
                m4_audio["----:com.apple.iTunes:initialkey"] = [
                    MP4FreeForm(meta["key"].encode("utf-8"), dataformat=AtomDataType.UTF8)
                ]
            # Disc number (M4A 'disk' atom is a list of (disc, total) tuples)
            if meta.get("disc") and str(meta["disc"]) not in ("", "1"):
                try:
                    m4_audio["disk"] = [(int(meta["disc"]), 0)]
                except Exception:
                    pass
            m4_audio.save()

    except Exception as e:
        # new_filepath is always assigned before this try block; no conditional needed.
        log(f"Advanced tagging failed for {new_filepath}: {e}", "bold red")
        # Remove the copied-but-untagged file so the library never has a file that looks done but lacks metadata/art; marked FAILED for --retry-failed.
        try:
            os.remove(new_filepath)
        except OSError:
            pass
        raise   # let process_local_file catch this, log FAILED, and allow --retry-failed

    return new_filepath


_PLAYLIST_EXTENSIONS = (".flac", ".mp3", ".m4a", ".aac")
# Matches the "NN - Title.ext" filenames _compute_destination() produces, so tracks sort correctly.
_TRACK_NUM_RE = re.compile(r'^(\d+)\s*-\s*')


def _write_album_playlist(folder_structure):
    """(Re)generate an .m3u8 playlist listing every audio file currently in
    folder_structure, ordered by track number. Called after each successful
    apply_tags_and_move() so the playlist stays in sync as an album fills in
    — possibly across multiple runs/sessions. Regenerated from the folder's
    actual contents each time (rather than accumulated in memory) so it's
    correct even if files were added, removed, or renamed by hand between
    runs. Serialised via _m3u_lock since several workers can finish tracks
    from the same album within milliseconds of each other.
    """
    with _m3u_lock:
        try:
            entries = []
            for fname in os.listdir(folder_structure):
                if not fname.lower().endswith(_PLAYLIST_EXTENSIONS):
                    continue
                fpath = os.path.join(folder_structure, fname)
                if not os.path.isfile(fpath):
                    continue
                m = _TRACK_NUM_RE.match(fname)
                track_no = int(m.group(1)) if m else 9999   # malformed names sort last, alphabetically
                duration = -1   # -1 = unknown, per the M3U spec
                title = os.path.splitext(fname)[0]
                try:
                    _a = mutagen.File(fpath, easy=True)
                    if _a is not None and _a.info is not None and getattr(_a.info, "length", None):
                        duration = int(_a.info.length)
                    if _a is not None and _a.get("title"):
                        title = _a["title"][0]
                except Exception:
                    pass
                entries.append((track_no, fname, duration, title))

            if not entries:
                return   # nothing to list — leave any existing playlist alone rather than write an empty one

            entries.sort(key=lambda e: (e[0], e[1]))

            playlist_name = sanitize_filename(os.path.basename(folder_structure), 100) + ".m3u8"
            playlist_path = os.path.join(folder_structure, playlist_name)

            lines = ["#EXTM3U"]
            for _track_no, fname, duration, title in entries:
                lines.append(f"#EXTINF:{duration},{title}")
                lines.append(fname)   # relative path — playlist lives in the same folder as the tracks

            with open(playlist_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception as _m3u_err:
            if CONF.get("DEBUG_MODE"):
                log(f"Playlist write failed for {folder_structure}: {_m3u_err}", "dim red")


def _set_album_artist_tag(filepath, album_artist):
    """Write a single normalised ALBUMARTIST value into filepath without
    touching any other tag (title, per-track ARTIST/feature credits, etc.
    are left exactly as they are). Used by the album-merge pass so every
    track in a consolidated album agrees on one album artist, which is what
    lets players (and this script, on the next import) group them as a
    single album instead of splitting on a per-track "feat." credit.
    """
    ext = os.path.splitext(filepath)[1].lstrip('.').lower()
    try:
        if ext == "flac":
            f_audio = FLAC(filepath)
            f_audio["ALBUMARTIST"] = album_artist
            f_audio.save()
        elif ext == "mp3":
            m_audio = MP3(filepath, ID3=ID3)
            if m_audio.tags is None:
                m_audio.add_tags()
            m_audio.tags.add(TPE2(encoding=3, text=[album_artist]))
            m_audio.save()
        elif ext in ("m4a", "aac"):
            m4_audio = MP4(filepath)
            if m4_audio.tags is None:
                m4_audio.add_tags()
            m4_audio["aART"] = [album_artist]
            m4_audio.save()
    except Exception as e:
        if CONF.get("DEBUG_MODE"):
            log(f"ALBUMARTIST normalisation failed for {filepath}: {e}", "dim red")


# ---------- Post-Session Statistics ----------
class BatchStats:
    """Thread-safe accumulator for per-batch processing statistics."""

    def __init__(self):
        self._lock   = threading.Lock()
        self.processed     = 0
        self.skipped       = 0
        self.failed        = 0
        self.lyrics_synced = 0   # found via LRCLIB / SyncedLyrics DB
        self.lyrics_ai     = 0   # transcribed with Whisper
        self.lyrics_none   = 0   # no lyrics available
        self.bpm_values    = []

    def record_processed(self):
        with self._lock:
            self.processed += 1

    def record_skipped(self):
        with self._lock:
            self.skipped += 1

    def record_failed(self):
        with self._lock:
            self.failed += 1

    def record_lyrics(self, source):
        """source: 'synced' | 'ai' | 'none'"""
        with self._lock:
            if source == "synced":
                self.lyrics_synced += 1
            elif source == "ai":
                self.lyrics_ai += 1
            else:
                self.lyrics_none += 1

    def record_bpm(self, bpm):
        with self._lock:
            try:
                if bpm is not None and int(bpm) > 0:   # explicit check: rejects None, 0, and False
                    self.bpm_values.append(int(bpm))
            except (ValueError, TypeError):
                pass   # non-numeric bpm value — silently skip rather than crash the stats thread

    def _avg_bpm_nolock(self):
        """Compute average BPM without acquiring the lock.
        Callers MUST already hold self._lock — this helper exists so both
        avg_bpm() and print_summary() share one formula without a deadlock."""
        return int(round(sum(self.bpm_values) / len(self.bpm_values))) if self.bpm_values else None

    def avg_bpm(self):
        with self._lock:
            return self._avg_bpm_nolock()

    def print_summary(self):
        with self._lock:
            avg        = self._avg_bpm_nolock()    # reuses shared formula; avoids duplicate and deadlock
            total      = self.processed + self.skipped + self.failed
            processed  = self.processed
            skipped    = self.skipped
            failed     = self.failed
            synced     = self.lyrics_synced
            ai_count   = self.lyrics_ai
            none_count = self.lyrics_none
        bpm_str  = f" | Avg BPM: [bold white]{avg}[/bold white]" if avg else ""
        console.print(Panel(
            f"[bold green]✅ {processed} processed[/bold green]  "
            f"[bold yellow]⏭  {skipped} skipped[/bold yellow]  "
            f"[bold red]❌ {failed} failed[/bold red]  "
            f"(total: {total})\n"
            f"Lyrics: [cyan]{synced} synced[/cyan]  "
            f"[magenta]{ai_count} AI-transcribed[/magenta]  "
            f"[dim]{none_count} none[/dim]"
            f"{bpm_str}",
            title="[bold white]📊 Batch Summary[/bold white]",
            border_style="green",
        ))


# ---------- Watch-Folder Daemon ---------- when True, interactive prompts auto-resolve to default "1" for unattended operation.
WATCH_MODE = False

# Sentinel for "user pressed 0 to skip lyrics" — distinguishes explicit skip from None (nothing found/use AI) so Whisper isn't triggered against the user's choice. object() avoids any string-collision risk.
_LYRICS_SKIP = object()

# Per-vid store for lyrics the user wants time-aligned ("S" option): fetch_ultimate_lyrics deposits the text, process_local_file pops it as lyrics_hint. Keyed by vid to avoid collisions across workers.
_lyrics_hint_store: dict = {}
_lyrics_hint_lock  = threading.Lock()


def process_local_file(file_path, worker_idx, force=False, stats=None):
    vid = "LOC_" + hashlib.md5(file_path.encode('utf-8')).hexdigest()[:16]
    title = os.path.splitext(os.path.basename(file_path))[0]
    uploader = "Unknown"

    try:
        audio_meta = mutagen.File(file_path, easy=True)
        if audio_meta:
            if 'title' in audio_meta:
                title = audio_meta['title'][0]
            if 'artist' in audio_meta:
                uploader = audio_meta['artist'][0]
    except Exception: pass

    if is_processed(vid) and not force:
        update_ui(worker_idx, vid, "Skipped")
        if stats:
            stats.record_skipped()
        time.sleep(1)
        update_ui(worker_idx, "--", "Idle")
        return

    try:
        update_ui(worker_idx, vid, "Fingerprinting")
        fp_meta, raw_fingerprint = get_metadata_via_fingerprint(file_path)
        if fp_meta:
            title = fp_meta.get('title') or title
            uploader = fp_meta.get('artist') or uploader

        # ── Feature 2: Fake-FLAC detection ── runs before the expensive Demucs+Whisper pipeline to avoid wasting GPU time; only applies to "lossless" extensions.
        _ext_lower = os.path.splitext(file_path)[1].lstrip('.').lower()
        if _ext_lower in _LOSSLESS_EXTENSIONS:
            update_ui(worker_idx, msg="Checking lossless integrity...")
            _upconv = detect_lossy_upconvert(file_path)
            if _upconv and _upconv["suspect"]:
                _ratio_pct = f"{_upconv['energy_below_16k']:.1%}"
                _warn_msg  = (
                    f"⚠️  POSSIBLE LOSSY UPCONVERT: '{os.path.basename(file_path)}' "
                    f"claims to be lossless ({_ext_lower.upper()}) but "
                    f"{_ratio_pct} of spectral energy is below 16 kHz — "
                    f"consistent with a 128–192 kbps MP3/AAC master. "
                    f"This file may be a renamed lossy encode."
                )
                log(_warn_msg, "bold red")
                if CONF.get("REJECT_LOSSY_UPCONVERT", False):
                    update_ui(worker_idx, msg="[bold red]Rejected — suspected upconvert[/bold red]")
                    db_log_status(vid, title, "FAILED: suspected lossy upconvert", file_path)
                    if stats:
                        stats.record_failed()
                    time.sleep(2)
                    update_ui(worker_idx, "--", "Idle")
                    return
                # REJECT_LOSSY_UPCONVERT is False → warn and continue.
                log("   Set REJECT_LOSSY_UPCONVERT=true in config.json to hard-reject these files.", "yellow")
        # ─────────────────────────────────────────────────────────────────

        # ── Feature 3: Acoustic duplicate quality-upgrade ── if already in the library, compare quality and offer to replace if the incoming file is clearly better.
        if raw_fingerprint:
            dup = check_acoustic_duplicate(raw_fingerprint)
            if dup and not force and not GLOBAL_DRY_RUN:
                _existing_path = dup.get("path") or ""
                _incoming_score  = get_audio_quality_score(file_path)
                _existing_score  = get_audio_quality_score(_existing_path) if _existing_path and os.path.exists(_existing_path) else (0, 0)

                _incoming_better = _incoming_score > _existing_score   # tuple comparison: format first, then bitrate

                if _incoming_better:
                    # The incoming file is higher quality.  Ask (or in watch mode, auto-accept).
                    _in_fmt   = os.path.splitext(file_path)[1].lstrip('.').upper()
                    _ex_fmt   = os.path.splitext(_existing_path)[1].lstrip('.').upper() if _existing_path else "?"
                    _in_kbps  = _incoming_score[1] if _incoming_score[1] != 9999 else "lossless"
                    _ex_kbps  = _existing_score[1] if _existing_score[1] != 9999 else "lossless"
                    log(
                        f"🔄 Quality upgrade detected: incoming {_in_fmt} ({_in_kbps} kbps) "
                        f"outranks existing {_ex_fmt} ({_ex_kbps} kbps) "
                        f"— '{dup['title']}' by {dup['artist']}",
                        "bold cyan",
                    )
                    _upgrade_choice = pause_ui_and_ask(
                        f"[bold cyan]Replace existing {_ex_fmt} with higher-quality {_in_fmt}? "
                        f"(Y=replace, N=skip, F=force full re-process)[/bold cyan]",
                        choices=["Y", "y", "N", "n", "F", "f"],
                        default="Y",
                    )
                    if _upgrade_choice.lower() == "n":
                        log(f"⏭  Quality upgrade declined — keeping existing {_ex_fmt}.", "yellow")
                        update_ui(worker_idx, vid, "[bold yellow]Upgrade declined — skipped[/bold yellow]")
                        if stats:
                            stats.record_skipped()
                        db_log_status(vid, title, "DUPLICATE")
                        time.sleep(1)
                        update_ui(worker_idx, "--", "Idle")
                        return
                    elif _upgrade_choice.lower() == "f":
                        # Treat as a full force-reprocess — fall through to the main pipeline.
                        log("🔥 Force re-process requested for quality upgrade.", "bold orange1")
                    else:   # "Y" — replace the file
                        if _existing_path and os.path.exists(_existing_path):
                            try:
                                os.remove(_existing_path)
                                log(f"🗑  Removed old {_ex_fmt}: {_existing_path}", "dim")
                            except Exception as _rm_err:
                                log(f"⚠️  Could not remove old file: {_rm_err}", "yellow")
                    # Fall through — pipeline re-tags/re-archives the incoming file; fingerprint updated to point to the new path at the end.
                        log("✅ Replacing with higher-quality version — re-archiving...", "bold green")
                        # Do NOT return — let the main pipeline continue below.
                else:
                    # Same or lower quality — standard duplicate skip.
                    log(
                        f"⚠️  Acoustic duplicate: '{dup['title']}' by {dup['artist']} "
                        f"already exists in library → {dup['path']}",
                        "bold yellow",
                    )
                    update_ui(worker_idx, vid, "[bold yellow]Duplicate — skipped[/bold yellow]")
                    if stats:
                        stats.record_skipped()
                    db_log_status(vid, title, "DUPLICATE")
                    time.sleep(1)
                    update_ui(worker_idx, "--", "Idle")
                    return
            elif dup and not force and GLOBAL_DRY_RUN:
                # Dry-run: report the same decision the real pipeline would make,
                # without prompting, deleting, or replacing anything.
                _existing_path  = dup.get("path") or ""
                _incoming_score = get_audio_quality_score(file_path)
                _existing_score = get_audio_quality_score(_existing_path) if _existing_path and os.path.exists(_existing_path) else (0, 0)
                if _incoming_score > _existing_score:
                    log(
                        f"📝 [DRY RUN] Quality-upgrade candidate — would offer to replace existing "
                        f"'{dup['title']}' by {dup['artist']} ({dup['path']})",
                        "bold cyan",
                    )
                    # Falls through to the metadata/destination preview below — nothing is deleted or moved.
                else:
                    log(
                        f"📝 [DRY RUN] Acoustic duplicate — would be skipped: '{dup['title']}' by {dup['artist']} "
                        f"already exists → {dup['path']}",
                        "dim cyan",
                    )
                    if stats:
                        stats.record_skipped()
                    update_ui(worker_idx, "--", "Idle")
                    return
        # ─────────────────────────────────────────────────────────────────

        update_ui(worker_idx, msg="Querying Meta")
        meta_dict = fetch_smart_metadata(title, uploader, worker_idx)

        if GLOBAL_DRY_RUN:
            # Dry-run preview: skip lyrics fetching/AI transcription and the real
            # tag-write/copy/move/DB-write entirely — just report where this file
            # would land and with what metadata, then move on to the next file.
            update_ui(worker_idx, msg="Analyzing (dry-run)...")
            if "bpm" not in meta_dict:
                _features = analyze_audio_features(file_path)
                if _features:
                    meta_dict.update(_features)
            _folder, _new_filepath, _track_fmt = _compute_destination(file_path, meta_dict)
            _rel_dest = os.path.relpath(_new_filepath, MUSIC_DIR)
            log(f"📝 [DRY RUN] Would archive → {_rel_dest}", "bold cyan")
            if CONF.get("DEBUG_MODE"):
                log(
                    f"    title='{meta_dict.get('title')}' artist='{meta_dict.get('artist')}' "
                    f"album='{meta_dict.get('album')}' year='{meta_dict.get('year')}' "
                    f"genre='{meta_dict.get('genre')}' cover={'yes' if meta_dict.get('cover') else 'no'}",
                    "dim",
                )
            if stats:
                stats.record_processed()
            update_ui(worker_idx, msg="[bold cyan]Dry-run preview complete[/bold cyan]")
            time.sleep(1)
            update_ui(worker_idx, "--", "Idle")
            return

        lyrics_source = "none"
        lrc = None
        if GLOBAL_ENABLE_LYRICS:
            update_ui(worker_idx, msg="Fetching Lyrics")
            lrc = fetch_ultimate_lyrics(meta_dict["title"], meta_dict["artist"], vid, worker_idx, audio_path=file_path)

            # Pop any sync-with-AI hint deposited by fetch_ultimate_lyrics when the
            # user pressed "S" on plain embedded lyrics.  None when not applicable.
            with _lyrics_hint_lock:
                _embedded_hint = _lyrics_hint_store.pop(vid, None)

            if lrc is _LYRICS_SKIP:
                lrc = None  # User explicitly skipped — do not fall through to AI transcription
            elif lrc:
                lyrics_source = "synced"
            else:
                lrc = transcribe_audio(file_path, vid, worker_idx, meta_dict=meta_dict, lyrics_hint=_embedded_hint)
                if lrc:
                    lyrics_source = "ai"
        else:
            update_ui(worker_idx, msg="Lyrics Disabled")

        if stats:
            stats.record_lyrics(lyrics_source)

        # BPM/key normally come from transcribe_audio's Demucs stem — if transcription was skipped (DB lyrics found), analyze the raw file instead so every track still gets these tags.
        if "bpm" not in meta_dict:
            update_ui(worker_idx, msg="Analyzing BPM & Key...")
            _features = analyze_audio_features(file_path)
            if _features:
                meta_dict.update(_features)

        if stats:
            stats.record_bpm(meta_dict.get("bpm"))

        update_ui(worker_idx, msg="Tagging & Sorting")
        new_filepath = apply_tags_and_move(file_path, meta_dict, lrc)

        # Feature 6: keep an .m3u8 playlist in sync with this album's folder contents.
        _write_album_playlist(os.path.dirname(new_filepath))

        # Store fingerprint so future imports of the same audio are caught as duplicates.
        if raw_fingerprint:
            store_fingerprint(
                raw_fingerprint,
                meta_dict.get("title", title),
                meta_dict.get("artist", uploader),
                new_filepath or file_path,
            )

        # Clean up the temporary .lrc file written to LOGS_DIR — it has already
        # been embedded into the audio file and would accumulate indefinitely.
        if lrc and os.path.exists(lrc):
            try:
                os.remove(lrc)
            except Exception:
                pass

        db_log_status(vid, meta_dict["title"], "SUCCESS", new_filepath)
        if stats:
            stats.record_processed()
        update_ui(worker_idx, msg="[bold green]Complete[/bold green]")
        time.sleep(2)
        update_ui(worker_idx, "--", "Idle")

    except Exception as e:
        err_msg = str(e)[:200]   # 200 chars gives enough context for DB + dashboard without flooding
        update_ui(worker_idx, msg=f"[bold red]Error: {err_msg}[/bold red]")
        db_log_status(vid, title, f"FAILED: {err_msg}", file_path)
        if stats:
            stats.record_failed()
        # Clean up any temp LRC that was written before the failure — without this
        # the file would linger in LOGS_DIR indefinitely after a tagging crash.
        try:
            _lrc_candidate = os.path.join(LOGS_DIR, f"{vid}_lyrics.lrc")
            if os.path.exists(_lrc_candidate):
                os.remove(_lrc_candidate)
        except Exception:
            pass
        time.sleep(2)
        update_ui(worker_idx, "--", "Idle")

def run_library_scan(fix=False):
    """Walk MUSIC_DIR and report files with missing/poor metadata: missing
    genre, no cover art, no lyrics tag, missing year, or BPM 0. Outputs a
    Rich table and CSV to LOGS_DIR. When fix=True, flagged files are
    re-queued through fetch_smart_metadata + apply_tags_and_move using the
    same worker/stats infrastructure as a normal batch.
    """
    audio_extensions = ('.mp3', '.flac', '.m4a', '.wav', '.ogg', '.opus', '.mkv', '.webm', '.mp4')
    console.print(f"\n[bold cyan]🔍 Library Health Scan — {MUSIC_DIR}[/bold cyan]\n")

    issues = []   # list of (path, list_of_issue_strings)

    all_files = []
    _logs_abs = os.path.abspath(LOGS_DIR) + os.sep
    for root, _, files in os.walk(MUSIC_DIR):
        # Skip the .logs subdirectory itself (os.sep-anchored so a sibling like ".logs_backup" isn't accidentally matched).
        _root_abs = os.path.abspath(root)
        if _root_abs == os.path.abspath(LOGS_DIR) or _root_abs.startswith(_logs_abs):
            continue
        for fname in files:
            # Skip macOS resource-fork sidecars (._filename) — same extension as real audio but no audio data, breaks mutagen.
            if fname.startswith('._'):
                continue
            if fname.lower().endswith(audio_extensions):
                all_files.append(os.path.join(root, fname))

    if not all_files:
        console.print("[bold yellow]No audio files found in the library.[/bold yellow]")
        return

    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        transient=True,
        console=console,
    ) as progress:
        task = progress.add_task("Scanning...", total=len(all_files))

        for fpath in all_files:
            progress.advance(task)
            file_issues = []
            try:
                audio = mutagen.File(fpath, easy=True)
                if audio is None:
                    file_issues.append("unreadable (mutagen returned None)")
                    issues.append((fpath, file_issues))
                    continue

                # ── Genre ────────────────────────────────────────────────
                genre_val = (audio.get("genre") or audio.get("GENRE") or [""])[0]
                if not genre_val or str(genre_val).strip().lower() in ("unknown", ""):
                    file_issues.append("genre = Unknown / missing")

                # ── Year / Date ──────────────────────────────────────────
                year_val = (audio.get("date") or audio.get("year") or [""])[0]
                if not year_val or str(year_val).strip().lower() in ("unknown year", "unknown", ""):
                    file_issues.append("year = Unknown / missing")

                # ── BPM ──────────────────────────────────────────────────
                bpm_raw = (audio.get("bpm") or audio.get("BPM") or ["0"])[0]
                try:
                    bpm_val = int(str(bpm_raw).split('.')[0])
                except (ValueError, TypeError):
                    bpm_val = 0
                if bpm_val == 0:
                    file_issues.append("BPM = 0 / missing")

            except Exception:
                file_issues.append("unreadable (exception)")
                issues.append((fpath, file_issues))
                continue

            # ── Cover art (requires format-specific check) ───────────────
            try:
                art_bytes, _ = _extract_embedded_art(fpath)
                if not art_bytes:
                    file_issues.append("no cover art")
            except Exception:
                file_issues.append("cover art check failed")

            # ── Lyrics ───────────────────────────────────────────────────
            try:
                emb_lyr = _extract_embedded_lyrics(fpath)
                if not emb_lyr:
                    file_issues.append("no lyrics tag")
            except Exception:
                file_issues.append("lyrics check failed")

            if file_issues:
                issues.append((fpath, file_issues))

    # ── Summary table ──────────────────────────────────────────────────────
    total      = len(all_files)
    flagged    = len(issues)
    clean      = total - flagged

    console.print(f"\n[bold white]📊 Scan complete:[/bold white] "
                  f"[bold green]{clean} clean[/bold green]  "
                  f"[bold yellow]{flagged} flagged[/bold yellow]  "
                  f"(total: {total})\n")

    if issues:
        tbl = Table(
            title="[bold yellow]⚠  Flagged Library Files[/bold yellow]",
            box=box.ROUNDED,
            show_lines=True,
        )
        tbl.add_column("File", style="cyan", max_width=60, overflow="fold")
        tbl.add_column("Issues", style="yellow")

        for fpath, fissues in issues[:200]:   # cap at 200 rows for readability
            rel_path = os.path.relpath(fpath, MUSIC_DIR)
            tbl.add_row(rel_path, " | ".join(fissues))

        if len(issues) > 200:
            tbl.add_row(f"… and {len(issues) - 200} more", "[dim](see CSV for full list)[/dim]")

        console.print(tbl)

        # Write CSV report.
        csv_path = os.path.join(LOGS_DIR, "scan_report.csv")
        try:
            with open(csv_path, "w", newline="", encoding="utf-8") as _cf:
                writer = csv.writer(_cf)
                writer.writerow(["path", "issues"])
                for fpath, fissues in issues:
                    writer.writerow([fpath, " | ".join(fissues)])
            console.print(f"[dim]Full report saved → {csv_path}[/dim]\n")
        except Exception as _csv_err:
            console.print(f"[dim yellow]CSV write failed: {_csv_err}[/dim yellow]")

    if fix and issues:
        console.print(f"\n[bold magenta]🔧 --fix requested: re-queuing {flagged} flagged file(s)...[/bold magenta]\n")
        fix_paths  = [fp for fp, _ in issues]
        fix_stats  = BatchStats()
        global active_live_ui

        try:
            with Live(generate_dashboard_table(), refresh_per_second=4) as live:
                with cli_lock:
                    active_live_ui = live
                try:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=CONF["MAX_WORKERS"]) as executor:
                        futures = [
                            executor.submit(
                                process_local_file,
                                fp,
                                i % CONF["MAX_WORKERS"],
                                force=True,
                                stats=fix_stats,
                            )
                            for i, fp in enumerate(fix_paths)
                        ]
                        while any(not f.done() for f in futures):
                            try:
                                req = _input_queue.get_nowait()
                            except queue.Empty:
                                req = None
                            if req is not None:
                                try:
                                    with cli_lock:
                                        _snap = active_live_ui
                                        if _snap and _snap.is_started:
                                            _snap.stop()
                                        print()
                                        if req["table"]:
                                            console.print(req["table"])
                                        req["answer"][0] = Prompt.ask(
                                            req["prompt"],
                                            choices=req["choices"],
                                            default=req["default"],
                                        )
                                        if _snap and not _snap.is_started:
                                            _snap.start()
                                finally:
                                    req["event"].set()
                            with cli_lock:
                                if live.is_started:
                                    try:
                                        live.update(generate_dashboard_table())
                                    except Exception:
                                        pass
                            time.sleep(0.1)
                finally:
                    with cli_lock:
                        active_live_ui = None
        except (KeyboardInterrupt, EOFError):
            console.print("\n[bold red]Fix interrupted.[/bold red]")

        fix_stats.print_summary()
        console.print("\n[bold green]Fix pass complete.[/bold green]\n")
    elif not issues:
        console.print("[bold green]✅ Library is clean — no issues found.[/bold green]\n")


# ---------- Album-Merge (split-folder consolidation) ----------
# Root cause of the split: _compute_destination() builds the top-level folder
# from meta["artist"], which is the *display* artist string for one specific
# track. A track with a featured artist ("Juice WRLD, Trippie Redd") therefore
# lands in a different top folder than the rest of the same album ("Juice
# WRLD"), even though every track belongs to one release. Small differences in
# the fetched album title (a stray "(Explicit)"/"(Deluxe)" tag from one source)
# can split things the same way even under the same artist folder. This pass
# finds folders that are almost certainly the same release and merges them.

# Cosmetic edition/version qualifiers stripped when comparing album titles —
# these commonly differ between sources/tracks for what is still one release.
_ALBUM_TITLE_SUFFIX_RE = re.compile(
    r'\s*[\(\[](?:deluxe(?:\s+edition)?|explicit|clean|bonus\s+track(?:s)?(?:\s+version)?|'
    r're-?master(?:ed)?(?:\s+\d{4})?|expanded(?:\s+edition)?|special\s+edition|'
    r'anniversary\s+edition|single|ep)[\)\]]\s*$',
    re.IGNORECASE,
)
# Matches the "YYYY - Album Title" (or "Unknown Year - Album Title") folders _compute_destination() creates.
_ALBUM_FOLDER_RE = re.compile(r'^(\d{4}|Unknown Year)\s*-\s*(.+)$')
_MERGE_AUDIO_EXTENSIONS = ('.mp3', '.flac', '.m4a', '.wav', '.ogg', '.opus')


def _normalize_album_key(album_title):
    """Collapse cosmetic album-title variants (Deluxe/Explicit/Remastered
    qualifiers, punctuation, casing, whitespace) into one comparable key, so
    the same release fetched slightly differently per-track still groups
    together."""
    key = album_title.lower()
    while True:
        new_key = _ALBUM_TITLE_SUFFIX_RE.sub('', key).strip()
        if new_key == key:
            break
        key = new_key
    key = re.sub(r'[^\w\s]', '', key)   # drop punctuation
    key = re.sub(r'\s+', ' ', key).strip()
    return key


def _scan_album_folders():
    """Walk MUSIC_DIR's Artist/Year - Album structure and return every
    populated album folder as a dict, for grouping by run_album_merge()."""
    records = []
    if not os.path.isdir(MUSIC_DIR):
        return records
    _logs_abs = os.path.abspath(LOGS_DIR) + os.sep

    for artist_name in sorted(os.listdir(MUSIC_DIR)):
        artist_path = os.path.join(MUSIC_DIR, artist_name)
        if not os.path.isdir(artist_path):
            continue
        _artist_abs = os.path.abspath(artist_path)
        if _artist_abs == os.path.abspath(LOGS_DIR) or _artist_abs.startswith(_logs_abs):
            continue

        for album_folder_name in sorted(os.listdir(artist_path)):
            album_path = os.path.join(artist_path, album_folder_name)
            if not os.path.isdir(album_path):
                continue
            m = _ALBUM_FOLDER_RE.match(album_folder_name)
            if not m:
                continue   # doesn't match this script's naming convention — leave it alone
            year, raw_album = m.group(1), m.group(2)
            files = sorted(
                f for f in os.listdir(album_path)
                if os.path.isfile(os.path.join(album_path, f)) and f.lower().endswith(_MERGE_AUDIO_EXTENSIONS)
            )
            if not files:
                continue
            records.append({
                "artist_folder": artist_name,
                "path": album_path,
                "year": year,
                "raw_album": raw_album,
                "files": files,
            })
    return records


def run_album_merge(dry_run=False):
    """Scan MUSIC_DIR for album folders that almost certainly belong to the
    same release, and merge them into one folder — keeping the copy with the
    most tracks as canonical, moving every other track in (never overwriting
    an existing file), and normalising ALBUMARTIST across the merged set so
    players group them correctly going forward. Pair with --dry-run to see
    exactly what would be merged without touching any files.
    """
    console.print(f"\n[bold cyan]🔗 Album Merge Scan — {MUSIC_DIR}[/bold cyan]\n")
    records = _scan_album_folders()
    if not records:
        console.print("[bold yellow]No album folders found in the library.[/bold yellow]\n")
        return

    groups = {}
    for rec in records:
        key = (rec["year"], _normalize_album_key(rec["raw_album"]))
        groups.setdefault(key, []).append(rec)

    # Only groups spanning more than one distinct on-disk folder are merge candidates.
    candidates = [recs for recs in groups.values() if len({r["path"] for r in recs}) > 1]

    if not candidates:
        console.print("[bold green]✅ No split albums found — every album already lives in a single folder.[/bold green]\n")
        return

    console.print(f"[bold yellow]Found {len(candidates)} album(s) split across multiple folders:[/bold yellow]\n")

    auto_yes = False
    merged_count = 0
    skipped_count = 0

    for group in candidates:
        group.sort(key=lambda r: len(r["files"]), reverse=True)
        canonical, others = group[0], group[1:]

        tbl = Table(box=box.ROUNDED, show_header=True,
                    title=f"[bold]{canonical['year']} — {canonical['raw_album']}[/bold]")
        tbl.add_column("Folder", style="cyan", overflow="fold")
        tbl.add_column("Tracks", justify="right", style="magenta")
        tbl.add_column("Role")
        tbl.add_row(os.path.relpath(canonical["path"], MUSIC_DIR), str(len(canonical["files"])),
                    "[bold green]KEEP (most tracks)[/bold green]")
        for o in others:
            tbl.add_row(os.path.relpath(o["path"], MUSIC_DIR), str(len(o["files"])), "[yellow]merge in →[/yellow]")
        console.print(tbl)

        if dry_run:
            console.print("[dim cyan]  (dry run — no files moved)[/dim cyan]\n")
            continue

        if not auto_yes:
            choice = Prompt.ask(
                "[bold cyan]Merge these into the folder with the most tracks? "
                "(Y=merge, N=skip, A=yes to all remaining, Q=quit)[/bold cyan]",
                choices=["Y", "y", "N", "n", "A", "a", "Q", "q"],
                default="Y",
            )
            if choice.lower() == "q":
                break
            if choice.lower() == "a":
                auto_yes = True
            elif choice.lower() == "n":
                skipped_count += 1
                console.print()
                continue

        # ── Perform the merge ──
        moved_any = False
        for o in others:
            for fname in o["files"]:
                src = os.path.join(o["path"], fname)
                dst = _unique_path(os.path.join(canonical["path"], fname))   # never overwrite a track
                try:
                    shutil.move(src, dst)
                    moved_any = True
                except Exception as _mv_err:
                    log(f"⚠️  Could not move '{src}' → '{dst}': {_mv_err}", "yellow")

            # Clean up the now-(mostly-)empty source folder. Only remove the exact
            # playlist file this script itself would have written — never touch
            # anything else left behind, and only rmdir() when truly empty, so a
            # folder holding unrelated files is safely left in place instead of
            # having those files deleted.
            _stale_playlist = os.path.join(o["path"], sanitize_filename(os.path.basename(o["path"]), 100) + ".m3u8")
            if os.path.isfile(_stale_playlist):
                try:
                    os.remove(_stale_playlist)
                except OSError:
                    pass
            try:
                os.rmdir(o["path"])
                # Also drop the artist folder if it's now empty (e.g. a folder that
                # only existed because of one featured-artist track).
                _parent = os.path.dirname(o["path"])
                if os.path.abspath(_parent) != os.path.abspath(MUSIC_DIR) and os.path.isdir(_parent) and not os.listdir(_parent):
                    os.rmdir(_parent)
            except OSError as _rm_err:
                if CONF.get("DEBUG_MODE"):
                    log(f"Folder not removed (still has other files): {o['path']} ({_rm_err})", "dim")

        if moved_any:
            # Normalise ALBUMARTIST across every track now in the canonical folder —
            # per-track ARTIST (feature credits) is left untouched.
            try:
                for fname in os.listdir(canonical["path"]):
                    fpath = os.path.join(canonical["path"], fname)
                    if os.path.isfile(fpath) and fname.lower().endswith(_PLAYLIST_EXTENSIONS):
                        _set_album_artist_tag(fpath, canonical["artist_folder"])
            except Exception:
                pass

            _write_album_playlist(canonical["path"])
            merged_count += 1
            console.print(f"[bold green]✅ Merged into {os.path.relpath(canonical['path'], MUSIC_DIR)}[/bold green]\n")

    if dry_run:
        console.print(f"[bold cyan]Dry run complete — {len(candidates)} album(s) would be merged.[/bold cyan]\n")
    else:
        console.print(f"[bold white]📊 Album merge complete:[/bold white] "
                      f"[bold green]{merged_count} merged[/bold green]  "
                      f"[bold yellow]{skipped_count} skipped[/bold yellow]\n")


def display_banner():
    clear_screen()

    # ── Feature reference table ───────────────────────────────────────────────
    feat = Table.grid(padding=(0, 2))
    feat.add_column(style="bold cyan",    no_wrap=True, min_width=22)
    feat.add_column(style="white")

    feat.add_row("▸ Interactive",
                 "Drop file paths into the prompt — prefix with [bold]force [/bold] to reprocess")
    feat.add_row("▸ --force",
                 "Skip SUCCESS / DUPLICATE checks — re-run the full pipeline on every file")
    feat.add_row("▸ --watch [PATH]",
                 "Daemon mode: auto-archives audio files dropped into the watched folder  "
                 "[dim](also typeable at the prompt)[/dim]")
    feat.add_row("   Default path",
                 f"[dim]{_DEFAULT_WATCH_PATH}[/dim]")
    feat.add_row("▸ --retry-failed",
                 "Re-queue every FAILED entry recorded in the SQLite database (implies --force)")
    feat.add_row("▸ --scan",
                 "Library health scan: reports files with missing genre, art, lyrics, year, or BPM "
                 "[dim](outputs Rich table + scan_report.csv)[/dim]")
    feat.add_row("▸ --fix",
                 "Used with --scan: re-process every flagged file to attempt metadata re-fetch")
    feat.add_row("▸ --engine N",
                 "Translation engine — 0=off  1=Google-Auto  2=MyMemory  "
                 "3=Google-Line  5=Local-LLM")
    feat.add_row("▸ --lang LANG",
                 "Source language override — auto | ja | ko | en | es | fr")

    console.print(Panel(
        feat,
        title=f"[bold white]Monster Archiver — NEXUS Edition v{VERSION}[/bold white]",
        subtitle="[dim]Ctrl+C to quit[/dim]",
        border_style="blue",
        expand=True,
        padding=(1, 2),
    ))
    print()

def _run_retry_failed_session():
    """Reprocess every FAILED file from the SQLite DB, with force=True so
    duplicate/already-processed checks are bypassed. Missing files are
    reported and skipped. Shares _run_interactive_session's event-loop
    structure so interactive prompts still work.
    """
    global active_live_ui

    console.print("[bold cyan]🔁 Retry-Failed Mode: scanning database…[/bold cyan]\n")
    failed = get_failed_entries()

    if not failed:
        console.print("[bold yellow]No FAILED entries found in the database.[/bold yellow]")
        return

    tasks = []
    for _vid, _title, _fpath in failed:
        if _fpath and os.path.exists(_fpath):
            tasks.append(_fpath)
            console.print(f"  [cyan]→ Queuing:[/cyan] {os.path.basename(_fpath)}  [dim]({_title})[/dim]")
        else:
            console.print(
                f"  [dim red]✗ Not found:[/dim red] "
                f"{_fpath or '(no path stored)'}  [dim]— {_title}[/dim]"
            )

    if not tasks:
        console.print(
            "\n[bold red]No recoverable files found.[/bold red]  "
            "Files may have moved or been deleted.\n"
            "[dim]Re-drop them into the interactive session to re-index.[/dim]"
        )
        return

    console.print(f"\n[bold green]Retrying {len(tasks)} file(s) with force mode…[/bold green]\n")
    batch_stats = BatchStats()

    try:
        with Live(generate_dashboard_table(), refresh_per_second=4) as live:
            with cli_lock:
                active_live_ui = live
            with concurrent.futures.ThreadPoolExecutor(max_workers=CONF["MAX_WORKERS"]) as executor:
                futures = [
                    executor.submit(
                        process_local_file,
                        fp,
                        i % CONF["MAX_WORKERS"],
                        force=True,
                        stats=batch_stats,
                    )
                    for i, fp in enumerate(tasks)
                ]
                while any(not f.done() for f in futures):
                    try:
                        req = _input_queue.get_nowait()
                    except queue.Empty:
                        req = None

                    if req is not None:
                        try:
                            with cli_lock:
                                _snap = active_live_ui
                                if _snap and _snap.is_started:
                                    _snap.stop()
                                print()
                                if req["table"]:
                                    console.print(req["table"])
                                req["answer"][0] = Prompt.ask(
                                    req["prompt"],
                                    choices=req["choices"],
                                    default=req["default"],
                                )
                                if _snap and not _snap.is_started:
                                    _snap.start()
                        finally:
                            req["event"].set()

                    with cli_lock:
                        if live.is_started:
                            try:
                                live.update(generate_dashboard_table())
                            except Exception:
                                pass
                    time.sleep(0.1)
    except (KeyboardInterrupt, EOFError):
        console.print("\n[bold red]Interrupt detected. Exiting…[/bold red]")
        while not _input_queue.empty():
            try:
                _req = _input_queue.get_nowait()
                _req["answer"][0] = _req.get("default") or "0"
                _req["event"].set()
            except Exception:
                break
        sys.exit(0)
    finally:
        with cli_lock:
            active_live_ui = None

    batch_stats.print_summary()
    console.print("\n[bold green]Retry session complete![/bold green]\n")


def _run_interactive_session(force_override=False):
    """Standard interactive session: user drags & drops files/folders.
    force_override=True (from --force) processes every batch with force=True
    without needing the 'force ' prefix; the prefix still works and stacks.
    """
    global active_live_ui

    try:
        while True:
            print("\n")
            raw = console.input("[bold yellow]➡️ Drag & Drop Audio File, Folder Path, or 'q' to quit: [/bold yellow]").strip()
            if raw.lower() in ("q", "quit", "exit"):
                break

            # ── Watch-mode shortcut ────────────────────────────────────────
            # Accepts: watch | -watch | --watch | watch <path> | --watch <path>
            _watch_m = re.match(r'^(?:--?)?watch(?:\s+(.+))?$', raw, re.IGNORECASE)
            if _watch_m:
                _wp = (_watch_m.group(1) or _DEFAULT_WATCH_PATH).strip().strip('"\'')
                if not os.path.isdir(_wp):
                    log(f"❌ Watch path not found or not a folder: {_wp}", "red")
                else:
                    console.print(f"\n[bold cyan]👁️  Switching to Watch Mode → {_wp}[/bold cyan]\n")
                    _run_watch_mode(_wp)
                    console.print("[bold green]↩  Returned to interactive session.[/bold green]")
                continue

            # force_override=True (--force flag) makes every batch a force-run
            # without the user needing to type the 'force ' prefix each time.
            force_mode = force_override
            if raw.lower().startswith("force "):
                force_mode = True
                raw = raw[6:].strip()
                log("🔥 Force Mode Active", style="bold orange1")

            raw_clean = raw.strip()

            if raw_clean.startswith('& '):
                raw_clean = raw_clean[2:].strip()

            # shlex.split handles quoted/space-separated paths in one shot; fall back to treating the whole string as one path on parse error (unmatched quote).
            try:
                extracted_paths = shlex.split(raw_clean)
            except ValueError:
                extracted_paths = [raw_clean.strip(' "\'')]

            tasks = []
            audio_extensions = ('.mp3', '.flac', '.m4a', '.wav', '.ogg', '.opus', '.mkv', '.webm', '.mp4')

            for raw_path in extracted_paths:
                p = raw_path.strip()
                p = p.strip('\u200e').strip('\u200f')

                if not os.path.exists(p) and '\\ ' in p:
                    p = p.replace('\\ ', ' ')

                if os.path.isdir(p):
                    log(f"📁 Scanning directory: {p}", "cyan")
                    for root, _, files in os.walk(p):
                        for f in files:
                            if f.lower().endswith(audio_extensions):
                                tasks.append(os.path.abspath(os.path.join(root, f)))

                elif os.path.isfile(p) and p.lower().endswith(audio_extensions):
                    tasks.append(os.path.abspath(p))

            if not tasks:
                log("❌ Invalid path or unsupported file type.", "red")
                continue

            # Reset per-batch counters while keeping a fresh stats object
            batch_stats = BatchStats()

            with Live(generate_dashboard_table(), refresh_per_second=4) as live:
                with cli_lock:
                    active_live_ui = live
                try:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=CONF["MAX_WORKERS"]) as executor:
                        futures = []
                        for i, file_path in enumerate(tasks):
                            futures.append(executor.submit(
                                process_local_file,
                                file_path,
                                i % CONF["MAX_WORKERS"],
                                force=force_mode,
                                stats=batch_stats,
                            ))

                    # Main-thread loop: drains pending input requests one at a time (prompts never overlap), refreshes dashboard every other cycle.
                        while any(not f.done() for f in futures):
                            # Drain one queued prompt request per cycle.
                            try:
                                req = _input_queue.get_nowait()
                            except queue.Empty:
                                req = None

                            if req is not None:
                                try:
                                    with cli_lock:
                                        _snap = active_live_ui
                                        if _snap and _snap.is_started:
                                            _snap.stop()
                                        print()
                                        if req["table"]:
                                            console.print(req["table"])
                                        req["answer"][0] = Prompt.ask(
                                            req["prompt"],
                                            choices=req["choices"],
                                            default=req["default"],
                                        )
                                        if _snap and not _snap.is_started:
                                            _snap.start()
                                finally:
                                    req["event"].set()

                            with cli_lock:
                                if live.is_started:
                                    try:
                                        live.update(generate_dashboard_table())
                                    except Exception:
                                        pass
                            time.sleep(0.1)   # 100 ms poll keeps prompt wait short
                finally:
                    with cli_lock:
                        active_live_ui = None

            # Print post-batch statistics summary.
            batch_stats.print_summary()
            console.print("\n[bold green]Tagging & Alignment Complete![/bold green]\n")

    except (KeyboardInterrupt, EOFError):
        console.print("\n[bold red]Interrupt detected. Exiting...[/bold red]")
        # Drain any pending prompt requests so worker threads blocked on
        # done_event.wait() can unblock and terminate cleanly.
        while not _input_queue.empty():
            try:
                _req = _input_queue.get_nowait()
                _req["answer"][0] = _req.get("default") or "0"
                _req["event"].set()
            except Exception:
                break
        sys.exit(0)


def _run_watch_mode(watch_path):
    """Monitor *watch_path* for new audio files and auto-process them.

    Uses watchdog's Observer so no polling is needed.  All interactive prompts
    are suppressed (WATCH_MODE=True) so the pipeline runs fully unattended.
    """
    global active_live_ui, WATCH_MODE
    WATCH_MODE = True

    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        console.print("[bold red]watchdog not installed — run with `pip install watchdog`[/bold red]")
        sys.exit(1)

    audio_extensions = ('.mp3', '.flac', '.m4a', '.wav', '.ogg', '.opus', '.mkv', '.webm', '.mp4')
    _watch_q = queue.Queue()

    class _AudioHandler(FileSystemEventHandler):
        # Dedup guard: abs_path→True while its stability-poll thread runs, preventing duplicate queuing from rapid/overlapping fs events.
        _enqueue_seen: dict = {}
        _enqueue_lock = threading.Lock()

        def _enqueue(self, path):
            if not path.lower().endswith(audio_extensions):
                return
            abs_path = os.path.abspath(path)

            with _AudioHandler._enqueue_lock:
                if abs_path in _AudioHandler._enqueue_seen:
                    return   # already tracking this path — skip duplicate event
                _AudioHandler._enqueue_seen[abs_path] = True

            def _wait_stable_then_queue():
                """Poll file size until it stops changing, then push to the
                processing queue. Handles partial downloads (waits for the
                download manager to finish), duplicate on_created+on_moved
                events (deduped by _enqueue_seen above), and intermediate
                format files (e.g. yt-dlp's temp .webm) that get deleted
                before stabilising — discarded silently via OSError.
                """
                prev_size = -1
                stable_count = 0
                deadline = time.time() + 300   # 5-minute ceiling for very large files
                try:
                    while time.time() < deadline:
                        try:
                            curr_size = os.path.getsize(abs_path)
                        except OSError:
                        # File deleted before stabilising — likely an intermediate format (e.g. yt-dlp's temp .webm/.m4a); discard silently.
                            return
                        if curr_size > 0 and curr_size == prev_size:
                            stable_count += 1
                            if stable_count >= 3:   # 3 × 2 s = 6 s of no growth with non-zero size
                                _watch_q.put(abs_path)
                                return
                        elif curr_size == 0:
                            # File exists but is empty — could be a partial download;
                            # keep waiting but don't count this as stable.
                            stable_count = 0
                        else:
                            stable_count = 0
                            prev_size = curr_size
                        time.sleep(2)
                    # Deadline reached: if the file still exists, queue it anyway.
                    if os.path.exists(abs_path):
                        _watch_q.put(abs_path)
                finally:
                    with _AudioHandler._enqueue_lock:
                        _AudioHandler._enqueue_seen.pop(abs_path, None)

            threading.Thread(target=_wait_stable_then_queue, daemon=True).start()

        def on_created(self, event):
            if not event.is_directory:
                self._enqueue(event.src_path)

        # Also handle moves (e.g. download managers that write to a temp name then rename).
        def on_moved(self, event):
            if not event.is_directory:
                self._enqueue(event.dest_path)

    # try/finally around the observer+executor lifecycle guarantees cleanup even if Observer.start() raises (e.g. inotify limit hit on Linux).
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=CONF["MAX_WORKERS"])
    observer = Observer()
    # batch_stats initialised before the try block so it's always defined even if observer.start() raises; NameError guard below should never fire.
    batch_stats = BatchStats()
    try:
        observer.schedule(_AudioHandler(), watch_path, recursive=True)
        observer.start()

        log(f"👁️  Watch mode active → {watch_path}", "bold cyan")
        log("Drop audio files into the folder — they will be archived automatically. Ctrl+C to stop.", "dim")

        _slot_counter = 0
        active_futures = {}   # future → file_path

        try:
            with Live(generate_dashboard_table(), refresh_per_second=4) as live:
                with cli_lock:
                    active_live_ui = live
                while True:
                    # Drain pending input requests — force_interactive=True prompts (lyrics selection) bypass WATCH_MODE auto-resolve and land here.
                    try:
                        req = _input_queue.get_nowait()
                    except queue.Empty:
                        req = None

                    if req is not None:
                        try:
                            with cli_lock:
                                if live.is_started:
                                    live.stop()
                                print()
                                if req["table"]:
                                    console.print(req["table"])
                                req["answer"][0] = Prompt.ask(
                                    req["prompt"],
                                    choices=req["choices"],
                                    default=req["default"],
                                )
                                if not live.is_started:
                                    live.start()
                        finally:
                            req["event"].set()

                    # Drain new files from the watch queue and submit them.
                    while not _watch_q.empty():
                        try:
                            fp = _watch_q.get_nowait()
                            slot = _slot_counter % CONF["MAX_WORKERS"]
                            _slot_counter += 1
                            log(f"📥 Queuing: {os.path.basename(fp)}", "cyan")
                            fut = executor.submit(process_local_file, fp, slot, stats=batch_stats)
                            active_futures[fut] = fp
                        except queue.Empty:
                            break

                    # Clean up completed futures.
                    for fut in list(active_futures):
                        if fut.done():
                            del active_futures[fut]

                    # Refresh dashboard.
                    with cli_lock:
                        if live.is_started:
                            try:
                                live.update(generate_dashboard_table())
                            except Exception:
                                pass
                    time.sleep(0.25)

        except KeyboardInterrupt:
            pass
    finally:
        # Drain pending prompt requests so worker threads blocked on done_event.wait() can unblock and exit (watch mode was the only entry point missing this).
        while not _input_queue.empty():
            try:
                _req = _input_queue.get_nowait()
                _req["answer"][0] = _req.get("default") or "0"
                _req["event"].set()
            except Exception:
                break
        WATCH_MODE = False  # Restore interactive-prompt behaviour for any caller that returns here
        try:
            observer.stop()
            observer.join(timeout=5)   # give the watchdog thread 5 s to exit cleanly; don't hang forever
        except Exception:
            pass
        executor.shutdown(wait=False)   # don't block on in-flight GPU/network tasks at Ctrl+C
        with cli_lock:
            active_live_ui = None

    # batch_stats is always defined (initialised before the try block) so this
    # NameError guard should never fire — kept only as a belt-and-braces safety net.
    try:
        batch_stats.print_summary()
    except NameError:
        pass
    console.print("\n[bold green]Watch session ended.[/bold green]")


def main():
    parser = argparse.ArgumentParser(
        description="Monster Archiver — Nexus Edition",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  # Normal interactive session
  python Monster_Archiver.py

  # Re-process already-archived files
  python Monster_Archiver.py --force

  # Batch-recover every FAILED entry from the database (scriptable, cron-friendly)
  python Monster_Archiver.py --retry-failed --engine 1 --lang auto

  # Watch folder daemon
  python Monster_Archiver.py --watch ~/Downloads/Music

  # Library health scan (report only)
  python Monster_Archiver.py --scan

  # Library health scan + auto-fix (re-archive every flagged file)
  python Monster_Archiver.py --scan --fix --engine 1 --lang auto

  # Preview a folder drop before committing to it — nothing is written or moved
  python Monster_Archiver.py --dry-run

  # Find + merge albums split across multiple folders (e.g. by a "feat." credit)
  python Monster_Archiver.py --merge-albums

  # Preview which albums would be merged, without touching any files
  python Monster_Archiver.py --merge-albums --dry-run
""",
    )
    parser.add_argument(
        "--watch", metavar="PATH", nargs="?",
        const="",   # bare --watch (no PATH) → args.watch = "" (falsy but not None)
        help=(
            "Daemon mode: monitor PATH for new audio files and archive them automatically.  "
            "Omit PATH to use the default: your ~/Downloads folder"
        ),
    )
    parser.add_argument(
        "--force", action="store_true",
        help=(
            "Force-reprocess files even if they are already marked SUCCESS or DUPLICATE "
            "in the database.  Equivalent to typing 'force ' before every path in the "
            "interactive session."
        ),
    )
    parser.add_argument(
        "--retry-failed", action="store_true", dest="retry_failed",
        help=(
            "Read the SQLite database for all FAILED entries and reprocess them.  "
            "Implies --force.  File paths must have been recorded during a previous run "
            "(any run after this version).  Files that have moved or been deleted are "
            "reported but skipped."
        ),
    )
    parser.add_argument(
        "--scan", action="store_true",
        help=(
            "Library health scan mode: walk MUSIC_DIR and report every file with "
            "missing genre, cover art, lyrics, year, or BPM = 0.  "
            "Outputs a Rich table to the console and saves a CSV to LOGS_DIR. "
            "Pair with --fix to attempt metadata re-fetch for all flagged files."
        ),
    )
    parser.add_argument(
        "--fix", action="store_true",
        help=(
            "Used together with --scan: after reporting flagged files, "
            "re-run the full archive pipeline (force mode) on every flagged file "
            "to attempt metadata re-fetch and tag repair."
        ),
    )
    parser.add_argument(
        "--engine", metavar="N", choices=["0", "1", "2", "3", "5"], default=None,
        help=(
            "Translation engine for this session — skips the interactive prompt.  "
            "0=off  1=Google-auto  2=MyMemory  3=Google-line  5=Local-LLM"
        ),
    )
    parser.add_argument(
        "--lang", metavar="LANG", choices=["auto", "ja", "ko", "en", "es", "fr"], default=None,
        help=(
            "Source audio language — skips the interactive language prompt.  "
            "auto=detect  ja=Japanese  ko=Korean  en=English  es=Spanish  fr=French"
        ),
    )
    parser.add_argument(
        "--no-lyrics", action="store_true", dest="no_lyrics",
        help=(
            "Skip all lyrics fetching and AI transcription for this session.  "
            "Files will be tagged with metadata only — no LRC file, no Whisper run."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help=(
            "Preview mode: run fingerprinting, duplicate/quality checks, and metadata "
            "lookup as normal and report exactly where each file would land — but never "
            "copy, tag, move, or delete anything, and never write to the database.  "
            "Lyrics fetching / AI transcription are skipped since they don't affect the "
            "preview.  Works with a normal session, --retry-failed, --watch, --scan --fix, "
            "and --merge-albums."
        ),
    )
    parser.add_argument(
        "--merge-albums", action="store_true", dest="merge_albums",
        help=(
            "Library maintenance mode: scan MUSIC_DIR for the same album split across "
            "multiple folders — usually caused by a featured artist changing the folder-"
            "naming artist string on just some tracks — and merge them into one folder, "
            "keeping the copy with the most tracks and normalising ALBUMARTIST across the "
            "merged set.  Combine with --dry-run to preview the merges first."
        ),
    )
    args = parser.parse_args()

    display_banner()

    global GLOBAL_TRANS_METHOD
    global GLOBAL_AUDIO_LANG
    global GLOBAL_ENABLE_LYRICS
    global GLOBAL_DRY_RUN
    global active_live_ui

    GLOBAL_DRY_RUN = args.dry_run
    if GLOBAL_DRY_RUN:
        console.print("[bold cyan]🔍 Dry-run mode — previewing only, no files will be written, moved, or deleted.[/bold cyan]\n")

    # --merge-albums never touches metadata fetch, lyrics, or translation — skip these prompts entirely.
    if not args.merge_albums:
        # ── Translation engine ────────────────────────────────────────────────
        if args.engine is not None:
            GLOBAL_TRANS_METHOD = args.engine
            console.print(f"[dim]Translation engine set via CLI: {args.engine}[/dim]")
        else:
            console.print("[bold magenta]🌍 Select Translation Engine For This Session:[/bold magenta]")
            console.print("1. [bold green]Google Translate (Smart Auto)[/bold green] - Auto-detects English, Romaji, and Kanji (Recommended)")
            console.print("2. [bold yellow]MyMemory (Batch API)[/bold yellow] - Good fallback")
            console.print("3. [bold red]Google Translate (Line-by-Line)[/bold red] - Slow")
            _local_model = CONF.get("LOCAL_LLM_MODEL", "gemma4:12b")
            console.print(f"5. [bold cyan]🖥️  Local LLM (Ollama · {_local_model})[/bold cyan] - Runs on your GPU — no rate limits, no token caps, no internet needed")
            console.print("0. [bold white]Disable Lyrics Translation[/bold white]")
            GLOBAL_TRANS_METHOD = Prompt.ask(
                "\n[bold cyan]Choose (0-3, 5)[/bold cyan]",
                choices=["0", "1", "2", "3", "5"],
                default=CONF["DEFAULT_TRANSLATION_ENGINE"],
            )

        # ── Source audio language ─────────────────────────────────────────────
        if args.lang is not None:
            GLOBAL_AUDIO_LANG = args.lang
            console.print(f"[dim]Audio language set via CLI: {args.lang}[/dim]")
        else:
            console.print("\n[bold magenta]🗣️ Select Source Audio Language For AI Adapter:[/bold magenta]")
            console.print("1. [bold cyan]Auto-Detect Language[/bold cyan] - Recommended for mixed libraries (Arknights, etc.)")
            console.print("2. [bold green]Japanese (ja)[/bold green] - Perfect for archiving J-Pop/Anime tracks")
            console.print("3. [bold yellow]English (en)[/bold yellow] - Ideal for Western tracks")
            console.print("4. [bold blue]Spanish (es)[/bold blue]")
            console.print("5. [bold magenta]French (fr)[/bold magenta] - French / Francophone tracks")
            console.print("6. [bold green]Korean (ko)[/bold green] - K-Pop / K-Drama tracks")
            lang_choice = Prompt.ask(
                "\n[bold cyan]Choose Audio Language (1-6)[/bold cyan]",
                choices=["1", "2", "3", "4", "5", "6"],
                default="1",
            )
            GLOBAL_AUDIO_LANG = {"1": "auto", "2": "ja", "3": "en", "4": "es", "5": "fr", "6": "ko"}[lang_choice]

        # ── Lyrics enable / disable ─────────────────────────────────────────────
        if args.no_lyrics:
            GLOBAL_ENABLE_LYRICS = False
            console.print("[dim]Lyrics disabled via --no-lyrics flag.[/dim]")
        else:
            console.print("\n[bold magenta]🎤 Lyrics Options:[/bold magenta]")
            console.print("Y. [bold green]Yes — fetch DB lyrics and run AI transcription as fallback[/bold green] (default)")
            console.print("N. [bold red]No  — skip all lyrics entirely (no DB fetch, no Whisper)[/bold red]")
            _lyrics_choice = Prompt.ask(
                "\n[bold cyan]Enable lyrics fetching for this session? (Y/N)[/bold cyan]",
                choices=["Y", "y", "N", "n"],
                default="Y",
            )
            GLOBAL_ENABLE_LYRICS = _lyrics_choice.upper() == "Y"
            if not GLOBAL_ENABLE_LYRICS:
                console.print("[yellow]⚠  Lyrics disabled — files will be tagged with metadata only.[/yellow]")
    # ─────────────────────────────────────────────────────────────────────────

    # ── Route to the right session type ──────────────────────────────────────
    # ── Guard: --fix without --scan is a no-op ───────────────────────────────
    if args.fix and not args.scan:
        console.print("[bold yellow]⚠  --fix has no effect without --scan.  "
                      "Did you mean: --scan --fix?[/bold yellow]")

    try:
        if args.scan:
            # --scan (+optional --fix): prompts above are asked even in scan-only mode since --fix may need them for re-archiving; scan-only never runs the pipeline.
            run_library_scan(fix=args.fix)
        elif args.merge_albums:
            # --merge-albums: pure folder/tag consolidation — never touches metadata fetch or lyrics.
            run_album_merge(dry_run=GLOBAL_DRY_RUN)
        elif args.retry_failed:
            # --retry-failed: recover every FAILED entry from the DB; implies force.
            _run_retry_failed_session()
        elif args.watch is not None:
            # Watch-folder daemon — args.watch is "" when --watch given with no path.
            watch_path = os.path.abspath(args.watch or _DEFAULT_WATCH_PATH)
            if not os.path.isdir(watch_path):
                console.print(f"[bold red]Watch path does not exist or is not a directory: {watch_path}[/bold red]")
                sys.exit(1)
            _run_watch_mode(watch_path)
        else:
            # Normal interactive session; --force propagates into every batch.
            _run_interactive_session(force_override=args.force)
    except (KeyboardInterrupt, EOFError):
        console.print("\n[bold red]Interrupt detected. Exiting...[/bold red]")
        sys.exit(0)


if __name__ == "__main__":
    main()