"""Filename/path sanitisation, artist-string splitting, Japanese-script
detection/romanisation, and the library destination-path computation used by
apply_tags_and_move() and the --dry-run preview path.
"""
import os
import re
import threading
import time

import requests

from . import db
from . import paths
from . import state
from . import ui

# Matches _compute_destination()'s historical hardcoded layout — also the
# fallback used if NAMING_FOLDER_TEMPLATE/NAMING_FILENAME_TEMPLATE are ever
# missing from config.json (e.g. an old config.json from before templates
# existed, loaded without going through config.load_config()'s key-merge).
DEFAULT_FOLDER_TEMPLATE = "{artist}/{year} - {album}"
DEFAULT_FILENAME_TEMPLATE = "{track} - {title}"

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

def _fetch_deezer_fan_count(artist_name):
    """Look up artist_name's Deezer fan count via the keyless public search
    API. Returns an int (0+) on a real match, or None if there's no match /
    the request fails — callers must treat None as "unknown", not "zero
    fans", so an API hiccup can't make an obscure-but-real artist lose to a
    successful lookup for someone else by default.
    """
    try:
        r = requests.get(
            "https://api.deezer.com/search/artist",
            params={"q": artist_name, "limit": 1},
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=4,
        )
        data = r.json()
        hits = data.get("data") or []
        if hits:
            return int(hits[0].get("nb_fan", 0) or 0)
    except Exception as e:
        if state.CONF.get("DEBUG_MODE"):
            ui.log(f"Deezer fame lookup failed for '{artist_name}': {e}", "dim red")
    return None

def get_artist_fame(artist_name):
    """Cached fan-count lookup — checks the sqlite cache before ever hitting
    Deezer, and caches misses too (as -1) so a name that doesn't match
    anything isn't re-queried on every track. Returns an int; -1 means
    "looked up, no data" and sorts below every real fan count.
    """
    key = artist_name.strip().lower()
    if not key:
        return -1
    cached = db.get_cached_artist_fame(key)
    if cached is not None:
        return cached
    fans = _fetch_deezer_fan_count(artist_name)
    fans_to_store = fans if fans is not None else -1
    db.store_artist_fame(key, artist_name, fans_to_store)
    return fans_to_store

def pick_primary_artist(artist_list):
    """Pick the single artist to use for the folder/filename {artist} token
    out of a track's full multi-artist credit list. The full list is still
    written to the ARTIST tag elsewhere (tag_writer.py), so players show
    every credited artist during playback — this only decides which one
    artist a multi-artist track files under, so a feature/collab track
    doesn't fragment its "real" artist's library folder into a separate
    one-off folder per unique credit-string.

    With PRIMARY_ARTIST_BY_FAME on (default), picks whichever credited
    artist has more Deezer fans — capped at the first 5 credited artists,
    since a track crediting many acts is usually a compilation/DJ-mix where
    fame-ranking every name adds lookups without a meaningfully better pick.
    Falls back to the first-credited artist when the setting is off, when
    there's a tie, or when every lookup fails (offline, Deezer down, etc.)
    so behavior degrades to the old convention rather than failing loudly.
    """
    names = [a.strip() for a in (artist_list or []) if a and a.strip()]
    if not names:
        return "Unknown"
    if len(names) == 1:
        return names[0]

    if not state.CONF.get("PRIMARY_ARTIST_BY_FAME", True):
        return names[0]

    best_name, best_fans = names[0], -1
    for name in names[:5]:
        fans = get_artist_fame(name)
        if fans > best_fans:
            best_name, best_fans = name, fans

    return best_name if best_fans > -1 else names[0]

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

# ── Destination reservation (TOCTOU guard) ─────────────────────────────────
# _unique_path()'s os.path.exists() check and the eventual shutil.copy2() in
# tag_writer.apply_tags_and_move() happen at different times. With MAX_WORKERS
# threads, two workers whose tracks resolve to the same destination could both
# see "doesn't exist" in the check→copy window and one would silently
# overwrite the other's archived file. Reserved paths are tracked in a
# process-wide set (under a lock) so a path handed out to one worker is
# treated as taken by every other worker until it's released (after the copy
# lands on disk, at which point os.path.exists() takes over).
_dest_reservation_lock = threading.Lock()
_reserved_destinations: set = set()


def release_destination(path):
    """Release a destination path reserved by _unique_path(reserve=True).
    Safe to call for paths that were never reserved."""
    if not path:
        return
    with _dest_reservation_lock:
        _reserved_destinations.discard(path)


def _unique_path(path, reserve=False):
    """Return `path` unchanged if free, otherwise the same path with a
    `_1`, `_2`, ... counter inserted before the extension — never overwrites
    an existing file. Falls back to a millisecond-timestamp suffix past 9999
    collisions. Shared by _compute_destination()'s collision guard and the
    album-merge pass's file moves.

    With reserve=True the returned path is atomically reserved (under
    _dest_reservation_lock) so no concurrent worker can be handed the same
    destination before the caller's copy actually lands on disk. Callers
    using reserve=True must call release_destination() once the file exists
    (or on failure) so the set doesn't grow unboundedly.
    """
    with _dest_reservation_lock:
        def _taken(p):
            return os.path.exists(p) or p in _reserved_destinations

        candidate = path
        if _taken(candidate):
            base, dot_ext = os.path.splitext(path)
            counter = 1
            candidate = f"{base}_{counter}{dot_ext}"
            while _taken(candidate) and counter < 9999:
                counter += 1
                candidate = f"{base}_{counter}{dot_ext}"
            if _taken(candidate):
                candidate = f"{base}_{int(time.time() * 1000)}{dot_ext}"

        if reserve:
            _reserved_destinations.add(candidate)
        return candidate

_TOKEN_RE = re.compile(r'\{([a-zA-Z_]+)\}')

def _build_naming_tokens(meta, primary_artist):
    """Map every token the Naming Templates UI advertises to its rendered
    (but not yet sanitized — render_naming_template() does that per path
    segment) value for this track."""
    try:
        track_padded = f"{int(meta.get('track', 1)):02d}"
    except Exception:
        track_padded = str(meta.get('track', '1'))

    artist_list = meta.get("artist_list") or [primary_artist]
    _albumartist = meta.get("artist") or ", ".join(artist_list) or primary_artist
    return {
        "artist": primary_artist,
        # Full multi-artist credit string (e.g. "Juice WRLD, Trippie Redd") —
        # distinct from {artist} so a template can still opt into showing
        # every credited artist in a folder/file name if desired.
        "albumartist": _albumartist,
        # Underscore alias for {albumartist} — some UI surfaces (and most
        # other taggers' token syntax) use "album_artist"; both spellings
        # resolve to the same value so a template never silently renders
        # blank just because it used the other convention.
        "album_artist": _albumartist,
        "album": meta.get("album", "Unknown Album"),
        "title": meta.get("title", "Unknown"),
        "year": str(meta.get("year", "Unknown Year")),
        "track": track_padded,
        "disc": str(meta.get("disc", "1")),
        "genre": meta.get("genre") or "Unknown",
        "isrc": meta.get("isrc") or "Unknown",
        # Composer wasn't previously exposed as a naming token even though
        # it's fetched, editable, and written to every file's tags — added
        # so {composer} works in folder/filename templates too.
        "composer": meta.get("composer") or "Unknown",
    }

def render_naming_template(template, tokens, seg_maxlen=80):
    """Render a folder/filename template like '{artist}/{year} - {album}'
    into a list of filesystem-safe path segments — one per '/'-separated
    piece of the template, each sanitized independently so a token value
    that happens to contain '/' (or '..', ':', etc.) can never escape the
    folder level the template put it in. Unknown/malformed {tokens} are
    dropped rather than left as literal braces in the final name. Empty
    segments (e.g. a token that rendered to nothing) are skipped.
    """
    if not template or not template.strip():
        template = "{artist}"
    segments = []
    for raw_segment in re.split(r'[\\/]+', template.strip()):
        rendered = _TOKEN_RE.sub(lambda m: str(tokens.get(m.group(1), "")), raw_segment)
        if not rendered.strip():
            continue  # segment had no real content (e.g. a token that resolved to "") — drop it rather than insert a placeholder folder level
        segments.append(sanitize_filename(rendered, seg_maxlen))
    return segments

def _compute_destination(file_path, meta, reserve=False):
    """Work out the library folder + final filename for a track's metadata,
    driven by the user-configurable NAMING_FOLDER_TEMPLATE /
    NAMING_FILENAME_TEMPLATE (see config.py; defaults reproduce the app's
    original hardcoded "Artist/Year - Album" + "Track - Title" layout, so
    leaving them untouched changes nothing).

    Multi-artist tracks are filed under a single artist — picked by fame,
    see pick_primary_artist() — instead of the raw multi-artist credit
    string, so a feature/collab track doesn't split its artist's library
    across a second, differently-named folder.

    Side effects limited to read-only os.path.exists() collision checks —
    no makedirs, no copy. Shared by apply_tags_and_move() (the real archive
    pipeline) and the --dry-run preview path, so a preview's reported
    destination is guaranteed to match what a real run would produce.

    reserve=True (used by the real archive path) atomically reserves the
    returned filepath against concurrent workers — see _unique_path(); the
    caller must release_destination() it after the copy lands (or fails).

    Returns (folder_structure, new_filepath, track_formatted, primary_artist).
    """
    ext = os.path.splitext(file_path)[1].lstrip('.').lower()

    artist_list = meta.get("artist_list") or split_artists(meta.get("artist", ""))
    primary_artist = pick_primary_artist(artist_list)
    tokens = _build_naming_tokens(meta, primary_artist)

    folder_template = state.CONF.get("NAMING_FOLDER_TEMPLATE") or DEFAULT_FOLDER_TEMPLATE
    filename_template = state.CONF.get("NAMING_FILENAME_TEMPLATE") or DEFAULT_FILENAME_TEMPLATE

    folder_segments = render_naming_template(folder_template, tokens, seg_maxlen=60)
    if not folder_segments:
        folder_segments = [sanitize_filename(primary_artist, 40)]   # template rendered to nothing — don't archive into MUSIC_DIR's root
    folder_structure = os.path.join(paths.MUSIC_DIR, *folder_segments)

    # Filename template may itself contain '/', which extends the folder
    # one level further (e.g. filename template "{album}/{track} - {title}");
    # every segment but the last becomes an extra folder level.
    filename_segments = render_naming_template(filename_template, tokens, seg_maxlen=120)
    if not filename_segments:
        filename_segments = [sanitize_filename(tokens["title"], 100)]
    if len(filename_segments) > 1:
        folder_structure = os.path.join(folder_structure, *filename_segments[:-1])
    base_filename = filename_segments[-1]

    track_formatted = tokens["track"]
    new_filename = f"{base_filename}.{ext}"
    new_filepath = os.path.join(folder_structure, new_filename)

    # Collision guard — append a counter instead of overwriting an existing
    # file of the same name. reserve=True also claims the path against
    # concurrent workers until the caller's copy actually exists on disk.
    new_filepath = _unique_path(new_filepath, reserve=reserve)

    return folder_structure, new_filepath, track_formatted, primary_artist
