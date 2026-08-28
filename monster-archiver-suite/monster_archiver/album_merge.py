"""Album-merge (split-folder consolidation): finds album folders that almost
certainly belong to the same release (same year + normalized title, split
across multiple folders — usually a featured-artist credit difference) and
merges them, keeping the copy with the most tracks as canonical.

Root cause of the split: _compute_destination() builds the top-level folder
from meta["artist"], which is the *display* artist string for one specific
track. A track with a featured artist ("Juice WRLD, Trippie Redd") therefore
lands in a different top folder than the rest of the same album ("Juice
WRLD"), even though every track belongs to one release. Small differences in
the fetched album title (a stray "(Explicit)"/"(Deluxe)" tag from one source)
can split things the same way even under the same artist folder.

Returns a summary dict in addition to printing, for cli.py's --json mode.
"""
import os
import re
import shutil

from rich.prompt import Prompt
from rich.table import Table
from rich import box

from . import db
from . import naming
from . import paths
from . import state
from . import tag_writer
from . import ui

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
    if not os.path.isdir(paths.MUSIC_DIR):
        return records
    _logs_abs = os.path.abspath(paths.LOGS_DIR) + os.sep

    for artist_name in sorted(os.listdir(paths.MUSIC_DIR)):
        artist_path = os.path.join(paths.MUSIC_DIR, artist_name)
        if not os.path.isdir(artist_path):
            continue
        _artist_abs = os.path.abspath(artist_path)
        if _artist_abs == os.path.abspath(paths.LOGS_DIR) or _artist_abs.startswith(_logs_abs):
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

    In state.NON_INTERACTIVE mode (the web app's JSON scan/merge API), every
    candidate is auto-merged without prompting — same "auto-resolve to
    default" behavior WATCH_MODE/GLOBAL_DRY_RUN already get elsewhere. v1
    limitation: no per-group web confirmation UI yet, so callers should run
    dry_run=True first and only invoke a real merge after the user reviews
    the preview.
    """
    state.console.print(f"\n[bold cyan]🔗 Album Merge Scan — {paths.MUSIC_DIR}[/bold cyan]\n")
    records = _scan_album_folders()
    if not records:
        state.console.print("[bold yellow]No album folders found in the library.[/bold yellow]\n")
        return {"candidates": 0, "merged": 0, "skipped": 0, "dry_run": dry_run}

    groups = {}
    for rec in records:
        key = (rec["year"], _normalize_album_key(rec["raw_album"]))
        groups.setdefault(key, []).append(rec)

    # Only groups spanning more than one distinct on-disk folder are merge candidates.
    candidates = [recs for recs in groups.values() if len({r["path"] for r in recs}) > 1]

    if not candidates:
        state.console.print("[bold green]✅ No split albums found — every album already lives in a single folder.[/bold green]\n")
        return {"candidates": 0, "merged": 0, "skipped": 0, "dry_run": dry_run}

    state.console.print(f"[bold yellow]Found {len(candidates)} album(s) split across multiple folders:[/bold yellow]\n")

    auto_yes = state.NON_INTERACTIVE  # JSON/unattended mode merges every candidate, no prompting
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
        tbl.add_row(os.path.relpath(canonical["path"], paths.MUSIC_DIR), str(len(canonical["files"])),
                    "[bold green]KEEP (most tracks)[/bold green]")
        for o in others:
            tbl.add_row(os.path.relpath(o["path"], paths.MUSIC_DIR), str(len(o["files"])), "[yellow]merge in →[/yellow]")
        state.console.print(tbl)

        if dry_run:
            state.console.print("[dim cyan]  (dry run — no files moved)[/dim cyan]\n")
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
                state.console.print()
                continue

        # ── Perform the merge ──
        moved_any = False
        for o in others:
            for fname in o["files"]:
                src = os.path.join(o["path"], fname)
                dst = naming._unique_path(os.path.join(canonical["path"], fname))   # never overwrite a track
                try:
                    shutil.move(src, dst)
                    moved_any = True
                    # Undo/activity log entry (best-effort) — lets the web GUI's
                    # recent-activity revert feature move a merged track back.
                    db.log_activity("merge", file_path=dst, prior_path=src,
                                     details=f"{fname} merged into {os.path.relpath(canonical['path'], paths.MUSIC_DIR)}")
                except Exception as _mv_err:
                    ui.log(f"⚠️  Could not move '{src}' → '{dst}': {_mv_err}", "yellow")

            # Clean up the now-(mostly-)empty source folder. Only remove the exact
            # playlist file this script itself would have written — never touch
            # anything else left behind, and only rmdir() when truly empty, so a
            # folder holding unrelated files is safely left in place instead of
            # having those files deleted.
            _stale_playlist = os.path.join(o["path"], naming.sanitize_filename(os.path.basename(o["path"]), 100) + ".m3u8")
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
                if os.path.abspath(_parent) != os.path.abspath(paths.MUSIC_DIR) and os.path.isdir(_parent) and not os.listdir(_parent):
                    os.rmdir(_parent)
            except OSError as _rm_err:
                if state.CONF.get("DEBUG_MODE"):
                    ui.log(f"Folder not removed (still has other files): {o['path']} ({_rm_err})", "dim")

        if moved_any:
            # Normalise ALBUMARTIST across every track now in the canonical folder —
            # per-track ARTIST (feature credits) is left untouched.
            try:
                for fname in os.listdir(canonical["path"]):
                    fpath = os.path.join(canonical["path"], fname)
                    if os.path.isfile(fpath) and fname.lower().endswith(_MERGE_AUDIO_EXTENSIONS):
                        tag_writer._set_album_artist_tag(fpath, canonical["artist_folder"])
            except Exception:
                pass

            tag_writer._write_album_playlist(canonical["path"])
            merged_count += 1
            state.console.print(f"[bold green]✅ Merged into {os.path.relpath(canonical['path'], paths.MUSIC_DIR)}[/bold green]\n")

    if dry_run:
        state.console.print(f"[bold cyan]Dry run complete — {len(candidates)} album(s) would be merged.[/bold cyan]\n")
    else:
        state.console.print(f"[bold white]📊 Album merge complete:[/bold white] "
                      f"[bold green]{merged_count} merged[/bold green]  "
                      f"[bold yellow]{skipped_count} skipped[/bold yellow]\n")

    return {
        "candidates": len(candidates),
        "merged": merged_count,
        "skipped": skipped_count,
        "dry_run": dry_run,
    }
