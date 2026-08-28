"""Library/log/database paths. A module (not a state.py constant) because the
web app's library-folder feature needs to override these at runtime via
set_library_dir() before init_db() runs — plain constants can't do that.
"""
import os

MUSIC_DIR = os.path.join(os.path.expanduser("~"), "Music", "Monster_Library")
LOGS_DIR = os.path.join(MUSIC_DIR, ".logs")
DB_FILE = os.path.join(LOGS_DIR, "archiver.sqlite")

# Default watch-folder path — override at runtime with --watch <path>
DEFAULT_WATCH_PATH = os.path.join(os.path.expanduser("~"), "Downloads")


def set_library_dir(path: str) -> None:
    """Override MUSIC_DIR/LOGS_DIR/DB_FILE for this process — used by
    --library-dir (the web app's per-request library folder setting).
    Must be called before db.init_db() so the new DB_FILE takes effect.
    """
    global MUSIC_DIR, LOGS_DIR, DB_FILE
    MUSIC_DIR = os.path.abspath(os.path.expanduser(path))
    LOGS_DIR = os.path.join(MUSIC_DIR, ".logs")
    DB_FILE = os.path.join(LOGS_DIR, "archiver.sqlite")
