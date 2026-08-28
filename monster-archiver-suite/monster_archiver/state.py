"""Shared mutable state for the Monster Archiver CLI.

Every module-level global that used to live directly in rezakir.py lives here
instead, so every other module can do `from monster_archiver import state`
and read/write `state.NAME` without import cycles. This module imports only
stdlib + rich — nothing from the rest of the package — so it can never be
part of a circular import.

CONF itself is populated once at startup (monster_archiver.cli bootstrap) by
calling monster_archiver.config.load_config()/validate_config() and assigning
the result here; nothing in this module knows how to build it.
"""
import threading
import queue
from collections import OrderedDict

from rich.console import Console

# ---------------- configuration ----------------
# Populated at startup by cli.py; other modules just read state.CONF.get(...).
CONF: dict = {}

# ---------------- UI / console ----------------
console = Console()
cli_lock = threading.Lock()
ui_lock = threading.Lock()
translation_lock = threading.Lock()
gpu_lock = threading.Lock()
_db_lock = threading.Lock()

# Worker threads push prompts here and block on a threading.Event; the main
# thread drains one at a time (see ui.pause_ui_and_ask).
_input_queue: "queue.Queue" = queue.Queue()

worker_status: dict = {}
active_live_ui = None

# ---------------- session-scoped flags (set per CLI invocation) ----------------
GLOBAL_TRANS_METHOD = None    # set at startup from CONF["DEFAULT_TRANSLATION_ENGINE"]
GLOBAL_AUDIO_LANG = "auto"
GLOBAL_ENABLE_LYRICS = True   # False skips all DB fetch + AI transcription
GLOBAL_DRY_RUN = False        # --dry-run: preview only, no writes/moves/tags
WATCH_MODE = False            # --watch daemon is active
# --json mode (webui-transcribe, library scan/merge JSON output): never block
# on stdin, same auto-resolve path WATCH_MODE/GLOBAL_DRY_RUN already use.
NON_INTERACTIVE = False

# ---------------- lazy AI model ----------------
WHISPER_MODEL = None
ai_lock = threading.Lock()

# pyannote.audio speaker-diarization pipeline (video_captions.py) — same
# lazy-singleton-under-ai_lock pattern as WHISPER_MODEL above.
DIARIZATION_PIPELINE = None

# ---------------- batch-scoped cover art caches ----------------
# When a whole album folder is dropped at once, every track resolves to the
# same cover URL and would otherwise re-download identical image bytes (up to
# ~12x for a full album). Cache by resolved cover URL, thread-safe across the
# worker pool. Capped + LRU-evicted so a long-running --watch daemon can't
# grow this unboundedly over days of operation.
_art_cache_lock = threading.Lock()
_ALBUM_ART_CACHE: "OrderedDict[str, tuple]" = OrderedDict()   # cover_url -> (img_bytes, mime_type)
_ART_CACHE_MAX_ENTRIES = 200

# Companion cache for the MusicBrainz release -> Cover Art Archive lookup
# itself (the CAA JSON/HEAD round-trip that *resolves* cover_url in the first
# place). Every track on the same release shares one release_mbid, so without
# this the JSON+HEAD calls repeat once per track even though _ALBUM_ART_CACHE
# above already dedupes the final image download. "" is cached too (a
# confirmed no-art release), so a release with no cover doesn't get re-probed.
_caa_cache_lock = threading.Lock()
_CAA_LOOKUP_CACHE: "OrderedDict[str, str]" = OrderedDict()    # release_mbid -> cover_url
_CAA_CACHE_MAX_ENTRIES = 500

# Serialises .m3u8 playlist (re)writes — several workers can finish tracks
# from the same album within milliseconds of each other.
_m3u_lock = threading.Lock()

# Forced-alignment / lyrics-hint handoff between fetch_ultimate_lyrics (which
# stashes a hint when the user picks "sync with AI") and transcribe_audio
# (which consumes it). Defined here up front instead of ~1000 lines after
# first use, as it was in the original monolith.
_LYRICS_SKIP = object()
_lyrics_hint_store: dict = {}
_lyrics_hint_lock = threading.Lock()
