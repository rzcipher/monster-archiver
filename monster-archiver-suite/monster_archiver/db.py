"""SQLite persistence: the downloads/fingerprints tables, status tracking,
and acoustic-duplicate lookups. DB_FILE is read from paths.DB_FILE at call
time (not destructured at import) so the web app's --library-dir override
(paths.set_library_dir(), called before init_db()) actually takes effect.
"""
import os
import sqlite3

from . import paths
from . import state
from . import ui


def init_db():
    for d in (paths.MUSIC_DIR, paths.LOGS_DIR):
        os.makedirs(d, exist_ok=True)

    with state._db_lock, sqlite3.connect(paths.DB_FILE) as conn:
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
        # Recent-operations log (undo/activity feature) — one row per
        # archive-move/tag-write/merge-move so a subset can be reverted later.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                action TEXT,
                file_path TEXT,
                prior_path TEXT,
                prior_tags_json TEXT,
                details TEXT,
                reverted INTEGER DEFAULT 0
            )
        """)
        try:
            conn.execute("ALTER TABLE activity_log ADD COLUMN reverted INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # Column already exists — safe to ignore

        # Caches Deezer fan counts per artist name so pick_primary_artist()
        # (naming.py) hits the network once per artist ever, not once per
        # track — an album with 12 tracks all crediting the same two artists
        # would otherwise re-query Deezer 24 times for one archive run.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS artist_fame_cache (
                artist_key TEXT PRIMARY KEY,
                artist_name TEXT,
                fan_count INTEGER,
                fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)


def db_log_status(vid, title, status, file_path=None):
    try:
        with state._db_lock, sqlite3.connect(paths.DB_FILE) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO downloads (vid, title, status, file_path) VALUES (?, ?, ?, ?)",
                (vid, title, status, file_path),
            )
    except Exception as e:
        # Always log DB write failures — a silently lost FAILED status means
        # --retry-failed will never find and re-queue that file.
        ui.log(f"DB write error (db_log_status): {e}", "bold red")


def mark_replaced_by_path(file_path):
    """Downgrade any SUCCESS row pointing at *file_path* to REPLACED.

    Used by the quality-upgrade path in pipeline.py after the old library
    file has been deleted — without this the downloads table keeps claiming
    a file exists at a path that was just removed, and is_processed() would
    keep skipping a re-import of the (now replaced) source."""
    if not file_path:
        return
    try:
        with state._db_lock, sqlite3.connect(paths.DB_FILE) as conn:
            conn.execute(
                "UPDATE downloads SET status='REPLACED' WHERE file_path=? AND status='SUCCESS'",
                (file_path,),
            )
    except Exception as e:
        if state.CONF.get("DEBUG_MODE"):
            ui.log(f"DB write error (mark_replaced_by_path): {e}", "red")


def get_failed_entries():
    """Return [(vid, title, file_path)] for every row whose status starts with FAILED."""
    try:
        with state._db_lock, sqlite3.connect(paths.DB_FILE) as conn:
            cur = conn.execute(
                "SELECT vid, title, file_path FROM downloads WHERE status LIKE 'FAILED%'"
            )
            return cur.fetchall()
    except Exception as e:
        if state.CONF.get("DEBUG_MODE"):
            ui.log(f"DB read error (get_failed_entries): {e}", "red")
        return []


def is_processed(vid):
    try:
        with state._db_lock, sqlite3.connect(paths.DB_FILE) as conn:
            cur = conn.execute("SELECT status FROM downloads WHERE vid=?", (vid,))
            row = cur.fetchone()
            return bool(row and row[0] in ("SUCCESS", "DUPLICATE"))
    except Exception as e:
        if state.CONF.get("DEBUG_MODE"):
            ui.log(f"DB read error (is_processed): {e}", "red")
        return False


# ---------- Acoustic Duplicate Detection ----------
def _normalize_fingerprint(fingerprint):
    """Normalise a chromaprint fingerprint to its canonical ASCII text form.

    acoustid.fingerprint_file() returns raw bytes (base64-ish ASCII like
    b'AQAA...'). Older versions of this code stored str(bytes) — Python's
    "b'AQAA...'" repr — which corrupted the stored value for any future use
    (re-matching, export, migration). Decode bytes properly here, and keep a
    legacy variant for lookups so databases written by the old code still
    match (see check_acoustic_duplicate).
    """
    if isinstance(fingerprint, bytes):
        return fingerprint.decode("ascii", errors="replace")
    return str(fingerprint)


def check_acoustic_duplicate(fingerprint):
    """Return stored metadata dict if fingerprint already exists in the library, else None."""
    if not fingerprint:
        return None
    canonical = _normalize_fingerprint(fingerprint)
    # Legacy rows were stored as str(bytes) — "b'AQAA...'" — so check that
    # form too; existing libraries keep their duplicate detection intact.
    legacy = str(fingerprint) if isinstance(fingerprint, bytes) else f"b'{fingerprint}'"
    try:
        with state._db_lock, sqlite3.connect(paths.DB_FILE) as conn:
            cur = conn.execute(
                "SELECT title, artist, library_path FROM fingerprints WHERE fingerprint IN (?, ?)",
                (canonical, legacy)
            )
            row = cur.fetchone()
            if row:
                return {"title": row[0], "artist": row[1], "path": row[2]}
    except Exception as e:
        if state.CONF.get("DEBUG_MODE"):
            ui.log(f"DB read error (check_acoustic_duplicate): {e}", "red")
    return None


def store_fingerprint(fingerprint, title, artist, library_path):
    """Store a chromaprint fingerprint alongside its library location."""
    if not fingerprint:
        return
    canonical = _normalize_fingerprint(fingerprint)
    try:
        with state._db_lock, sqlite3.connect(paths.DB_FILE) as conn:
            # Migrate any legacy str(bytes)-repr row for this fingerprint in
            # place, so the table converges on the canonical form over time.
            legacy = str(fingerprint) if isinstance(fingerprint, bytes) else f"b'{fingerprint}'"
            conn.execute("DELETE FROM fingerprints WHERE fingerprint=?", (legacy,))
            conn.execute(
                "INSERT OR REPLACE INTO fingerprints (fingerprint, title, artist, library_path) "
                "VALUES (?, ?, ?, ?)",
                (canonical, title or "Unknown", artist or "Unknown", library_path or "")
            )
    except Exception as e:
        if state.CONF.get("DEBUG_MODE"):
            ui.log(f"DB write error (store_fingerprint): {e}", "red")


# ---------- Artist Fame Cache (multi-artist folder-naming) ----------
def get_cached_artist_fame(artist_key):
    """Return the cached Deezer fan count for artist_key (lowercased artist
    name), or None if it's never been looked up. A stored fan_count of -1
    means "looked up, no match found" — still a cache hit, so a garbled
    or made-up artist name isn't re-queried on every track either."""
    try:
        with state._db_lock, sqlite3.connect(paths.DB_FILE) as conn:
            cur = conn.execute(
                "SELECT fan_count FROM artist_fame_cache WHERE artist_key=?",
                (artist_key,)
            )
            row = cur.fetchone()
            return row[0] if row else None
    except Exception as e:
        if state.CONF.get("DEBUG_MODE"):
            ui.log(f"DB read error (get_cached_artist_fame): {e}", "red")
        return None


def store_artist_fame(artist_key, artist_name, fan_count):
    try:
        with state._db_lock, sqlite3.connect(paths.DB_FILE) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO artist_fame_cache (artist_key, artist_name, fan_count) VALUES (?, ?, ?)",
                (artist_key, artist_name, int(fan_count))
            )
    except Exception as e:
        if state.CONF.get("DEBUG_MODE"):
            ui.log(f"DB write error (store_artist_fame): {e}", "red")


def log_activity(action, file_path=None, prior_path=None, prior_tags=None, details=None):
    """Record one entry in activity_log for the undo/recent-activity feature.
    Best-effort — a logging failure must never break the archive pipeline."""
    import json as _json
    try:
        with state._db_lock, sqlite3.connect(paths.DB_FILE) as conn:
            conn.execute(
                "INSERT INTO activity_log (action, file_path, prior_path, prior_tags_json, details) "
                "VALUES (?, ?, ?, ?, ?)",
                (action, file_path, prior_path, _json.dumps(prior_tags) if prior_tags else None, details),
            )
    except Exception as e:
        if state.CONF.get("DEBUG_MODE"):
            ui.log(f"DB write error (log_activity): {e}", "red")


def get_recent_activity(limit=20):
    """Return the most recent activity_log rows as a list of dicts, newest first."""
    try:
        with state._db_lock, sqlite3.connect(paths.DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT id, timestamp, action, file_path, prior_path, prior_tags_json, details, reverted "
                "FROM activity_log ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        if state.CONF.get("DEBUG_MODE"):
            ui.log(f"DB read error (get_recent_activity): {e}", "red")
        return []


def get_activity_by_id(activity_id):
    try:
        with state._db_lock, sqlite3.connect(paths.DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute("SELECT * FROM activity_log WHERE id=?", (activity_id,))
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as e:
        if state.CONF.get("DEBUG_MODE"):
            ui.log(f"DB read error (get_activity_by_id): {e}", "red")
        return None


def mark_activity_reverted(activity_id):
    with state._db_lock, sqlite3.connect(paths.DB_FILE) as conn:
        conn.execute("UPDATE activity_log SET reverted=1 WHERE id=?", (activity_id,))
