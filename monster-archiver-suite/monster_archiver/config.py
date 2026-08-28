"""Config file location, defaults, and load/validate — pure functions over a
plain dict. Nothing here imports monster_archiver.state; the live CONF
instance is built once at startup (monster_archiver.cli) via
CONF = validate_config(load_config()) and stored in state.CONF, since
everything downstream reads the mutable dict from state, not from here.
"""
import json
import os
import sys
import tempfile
import urllib.request

VERSION = "18.3"   # Single source of truth — bump here only

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

DEFAULT_CONFIG = {
    "MAX_WORKERS": 4,
    "ACOUSTID_API_KEY": "",  # Set your AcoustID key here or in config.json
    # Free token from huggingface.co/settings/tokens, after accepting the terms
    # at huggingface.co/pyannote/speaker-diarization-3.1 and
    # huggingface.co/pyannote/segmentation-3.0. Only needed for video caption
    # speaker diarization (video_captions.py) — everything else ignores it.
    "HUGGINGFACE_TOKEN": "",
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

    # ── Naming / folder templates (monster_archiver/naming.py) ──
    # Tokens: {artist} {albumartist} {album} {title} {year} {track} {disc} {genre} {isrc}
    # "/" in either template creates nested folders. Defaults match the
    # library layout the app has always produced, so leaving these untouched
    # changes nothing. Editing them only affects files archived from now on —
    # existing library folders are never renamed retroactively.
    "NAMING_FOLDER_TEMPLATE": "{artist}/{year} - {album}",
    "NAMING_FILENAME_TEMPLATE": "{track} - {title}",
    # When a track credits more than one artist, use the artist with more
    # Deezer fans as the single folder/file {artist} (full multi-artist credit
    # is still written to the ARTIST tag, so players show everyone). Falls
    # back to the first-credited artist if disabled or if the lookup fails.
    "PRIMARY_ARTIST_BY_FAME": True,

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


def load_config():
    if not os.path.exists(CONFIG_FILE):
        # Atomic first-run write (temp + rename), matching validate_config()'s
        # own save pattern — a crash mid-write must not leave a corrupt config.
        _cfg_dir = os.path.dirname(CONFIG_FILE) or "."
        _fd, _tmp_cfg = tempfile.mkstemp(dir=_cfg_dir, suffix=".json.tmp")
        try:
            with os.fdopen(_fd, "w") as f:
                json.dump(DEFAULT_CONFIG, f, indent=4)
            os.replace(_tmp_cfg, CONFIG_FILE)
        except Exception:
            try:
                os.unlink(_tmp_cfg)
            except OSError:
                pass
            raise
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
    Prints human-readable warnings to stderr — stdout must stay clean for
    --webui-transcribe mode, which is invoked as a subprocess and needs pure JSON."""
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
                "but will need to be reapplied on the next launch.",
                file=sys.stderr,
            )

    if issues:
        print("\n[CONFIG VALIDATION]", file=sys.stderr)
        for msg in issues:
            print(f"  ⚠  {msg}", file=sys.stderr)
        print(file=sys.stderr)

    return conf
