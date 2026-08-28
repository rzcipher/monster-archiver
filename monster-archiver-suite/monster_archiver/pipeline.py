"""The master per-file archive pipeline. process_local_file() orchestrates
fingerprinting, duplicate/quality checks, metadata fetch, lyrics fetch (with
AI transcription fallback), tag writing, and DB bookkeeping for one audio
file — called by every session runner (interactive, retry-failed, watch,
scan --fix) with a different task list but identical per-file logic.
BatchStats is the thread-safe per-session counter it reports into.
"""
import hashlib
import os
import threading
import time

import mutagen
from rich.panel import Panel

from . import audio_features
from . import db
from . import fingerprint
from . import lyrics
from . import metadata
from . import naming
from . import paths
from . import state
from . import tag_writer
from . import ui
from . import whisper_transcribe


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
                # bool is a subclass of int in Python, so True would pass an
                # int(bpm) > 0 check and record a bogus BPM of 1 — reject
                # bools explicitly alongside None/0/non-numerics.
                if bpm is not None and not isinstance(bpm, bool) and int(bpm) > 0:
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
        state.console.print(Panel(
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

    if db.is_processed(vid) and not force:
        ui.update_ui(worker_idx, vid, "Skipped")
        if stats:
            stats.record_skipped()
        time.sleep(1)
        ui.update_ui(worker_idx, "--", "Idle")
        return

    try:
        ui.update_ui(worker_idx, vid, "Fingerprinting")
        fp_meta, raw_fingerprint = fingerprint.get_metadata_via_fingerprint(file_path)
        if fp_meta:
            title = fp_meta.get('title') or title
            uploader = fp_meta.get('artist') or uploader

        # ── Feature 2: Fake-FLAC detection ── runs before the expensive Demucs+Whisper pipeline to avoid wasting GPU time; only applies to "lossless" extensions.
        _ext_lower = os.path.splitext(file_path)[1].lstrip('.').lower()
        if _ext_lower in audio_features._LOSSLESS_EXTENSIONS:
            ui.update_ui(worker_idx, msg="Checking lossless integrity...")
            _upconv = audio_features.detect_lossy_upconvert(file_path)
            if _upconv and _upconv["suspect"]:
                _ratio_pct = f"{_upconv['energy_below_16k']:.1%}"
                _warn_msg  = (
                    f"⚠️  POSSIBLE LOSSY UPCONVERT: '{os.path.basename(file_path)}' "
                    f"claims to be lossless ({_ext_lower.upper()}) but "
                    f"{_ratio_pct} of spectral energy is below 16 kHz — "
                    f"consistent with a 128–192 kbps MP3/AAC master. "
                    f"This file may be a renamed lossy encode."
                )
                ui.log(_warn_msg, "bold red")
                if state.CONF.get("REJECT_LOSSY_UPCONVERT", False):
                    ui.update_ui(worker_idx, msg="[bold red]Rejected — suspected upconvert[/bold red]")
                    db.db_log_status(vid, title, "FAILED: suspected lossy upconvert", file_path)
                    if stats:
                        stats.record_failed()
                    time.sleep(2)
                    ui.update_ui(worker_idx, "--", "Idle")
                    return
                # REJECT_LOSSY_UPCONVERT is False → warn and continue.
                ui.log("   Set REJECT_LOSSY_UPCONVERT=true in config.json to hard-reject these files.", "yellow")
        # ─────────────────────────────────────────────────────────────────

        # ── Feature 3: Acoustic duplicate quality-upgrade ── if already in the library, compare quality and offer to replace if the incoming file is clearly better.
        if raw_fingerprint:
            dup = db.check_acoustic_duplicate(raw_fingerprint)
            if dup and not force and not state.GLOBAL_DRY_RUN:
                _existing_path = dup.get("path") or ""
                _incoming_score  = audio_features.get_audio_quality_score(file_path)
                _existing_score  = audio_features.get_audio_quality_score(_existing_path) if _existing_path and os.path.exists(_existing_path) else (0, 0)

                _incoming_better = _incoming_score > _existing_score   # tuple comparison: format first, then bitrate

                if _incoming_better:
                    # The incoming file is higher quality.  Ask (or in watch mode, auto-accept).
                    _in_fmt   = os.path.splitext(file_path)[1].lstrip('.').upper()
                    _ex_fmt   = os.path.splitext(_existing_path)[1].lstrip('.').upper() if _existing_path else "?"
                    _in_kbps  = _incoming_score[1] if _incoming_score[1] != 9999 else "lossless"
                    _ex_kbps  = _existing_score[1] if _existing_score[1] != 9999 else "lossless"
                    ui.log(
                        f"🔄 Quality upgrade detected: incoming {_in_fmt} ({_in_kbps} kbps) "
                        f"outranks existing {_ex_fmt} ({_ex_kbps} kbps) "
                        f"— '{dup['title']}' by {dup['artist']}",
                        "bold cyan",
                    )
                    _upgrade_choice = ui.pause_ui_and_ask(
                        f"[bold cyan]Replace existing {_ex_fmt} with higher-quality {_in_fmt}? "
                        f"(Y=replace, N=skip, F=force full re-process)[/bold cyan]",
                        choices=["Y", "y", "N", "n", "F", "f"],
                        default="Y",
                    )
                    if _upgrade_choice.lower() == "n":
                        ui.log(f"⏭  Quality upgrade declined — keeping existing {_ex_fmt}.", "yellow")
                        ui.update_ui(worker_idx, vid, "[bold yellow]Upgrade declined — skipped[/bold yellow]")
                        if stats:
                            stats.record_skipped()
                        db.db_log_status(vid, title, "DUPLICATE")
                        time.sleep(1)
                        ui.update_ui(worker_idx, "--", "Idle")
                        return
                    elif _upgrade_choice.lower() == "f":
                        # Treat as a full force-reprocess — fall through to the main pipeline.
                        ui.log("🔥 Force re-process requested for quality upgrade.", "bold orange1")
                    else:   # "Y" — replace the file
                        _removed_ok = False
                        if _existing_path and os.path.exists(_existing_path):
                            try:
                                os.remove(_existing_path)
                                _removed_ok = True
                                ui.log(f"🗑  Removed old {_ex_fmt}: {_existing_path}", "dim")
                            except Exception as _rm_err:
                                ui.log(f"⚠️  Could not remove old file: {_rm_err}", "yellow")
                        else:
                            # Old file already gone (moved/deleted by hand) — nothing to remove.
                            _removed_ok = True
                        if _removed_ok:
                            # Keep the DB honest: the SUCCESS row pointing at the
                            # deleted path is downgraded so nothing claims a file
                            # exists there anymore.
                            db.mark_replaced_by_path(_existing_path)
                            # Regenerate the old folder's playlist so it no longer
                            # lists the deleted file (tag_writer only regenerates
                            # the NEW file's folder later).
                            _old_folder = os.path.dirname(_existing_path) if _existing_path else ""
                            if _old_folder and os.path.isdir(_old_folder):
                                tag_writer._write_album_playlist(_old_folder)
                            # Only announce the replacement when the removal actually succeeded.
                            ui.log("✅ Replacing with higher-quality version — re-archiving...", "bold green")
                        else:
                            ui.log("⚠️  Old file could not be removed — the incoming file will still be archived alongside it.", "yellow")
                        # Fall through — pipeline re-tags/re-archives the incoming file; fingerprint updated to point to the new path at the end.
                        # Do NOT return — let the main pipeline continue below.
                else:
                    # Same or lower quality — standard duplicate skip.
                    ui.log(
                        f"⚠️  Acoustic duplicate: '{dup['title']}' by {dup['artist']} "
                        f"already exists in library → {dup['path']}",
                        "bold yellow",
                    )
                    ui.update_ui(worker_idx, vid, "[bold yellow]Duplicate — skipped[/bold yellow]")
                    if stats:
                        stats.record_skipped()
                    db.db_log_status(vid, title, "DUPLICATE")
                    time.sleep(1)
                    ui.update_ui(worker_idx, "--", "Idle")
                    return
            elif dup and not force and state.GLOBAL_DRY_RUN:
                # Dry-run: report the same decision the real pipeline would make,
                # without prompting, deleting, or replacing anything.
                _existing_path  = dup.get("path") or ""
                _incoming_score = audio_features.get_audio_quality_score(file_path)
                _existing_score = audio_features.get_audio_quality_score(_existing_path) if _existing_path and os.path.exists(_existing_path) else (0, 0)
                if _incoming_score > _existing_score:
                    ui.log(
                        f"📝 [DRY RUN] Quality-upgrade candidate — would offer to replace existing "
                        f"'{dup['title']}' by {dup['artist']} ({dup['path']})",
                        "bold cyan",
                    )
                    # Falls through to the metadata/destination preview below — nothing is deleted or moved.
                else:
                    ui.log(
                        f"📝 [DRY RUN] Acoustic duplicate — would be skipped: '{dup['title']}' by {dup['artist']} "
                        f"already exists → {dup['path']}",
                        "dim cyan",
                    )
                    if stats:
                        stats.record_skipped()
                    ui.update_ui(worker_idx, "--", "Idle")
                    return
        # ─────────────────────────────────────────────────────────────────

        ui.update_ui(worker_idx, msg="Querying Meta")
        meta_dict = metadata.fetch_smart_metadata(title, uploader, worker_idx)

        if state.GLOBAL_DRY_RUN:
            # Dry-run preview: skip lyrics fetching/AI transcription and the real
            # tag-write/copy/move/DB-write entirely — just report where this file
            # would land and with what metadata, then move on to the next file.
            ui.update_ui(worker_idx, msg="Analyzing (dry-run)...")
            if "bpm" not in meta_dict:
                _features = audio_features.analyze_audio_features(file_path)
                if _features:
                    meta_dict.update(_features)
            _folder, _new_filepath, _track_fmt, _primary_artist = naming._compute_destination(file_path, meta_dict)
            _rel_dest = os.path.relpath(_new_filepath, paths.MUSIC_DIR)
            ui.log(f"📝 [DRY RUN] Would archive → {_rel_dest}", "bold cyan")
            if state.CONF.get("DEBUG_MODE"):
                ui.log(
                    f"    title='{meta_dict.get('title')}' artist='{meta_dict.get('artist')}' "
                    f"album='{meta_dict.get('album')}' year='{meta_dict.get('year')}' "
                    f"genre='{meta_dict.get('genre')}' cover={'yes' if meta_dict.get('cover') else 'no'}",
                    "dim",
                )
            if stats:
                stats.record_processed()
            ui.update_ui(worker_idx, msg="[bold cyan]Dry-run preview complete[/bold cyan]")
            time.sleep(1)
            ui.update_ui(worker_idx, "--", "Idle")
            return

        lyrics_source = "none"
        lrc = None
        if state.GLOBAL_ENABLE_LYRICS:
            ui.update_ui(worker_idx, msg="Fetching Lyrics")
            lrc = lyrics.fetch_ultimate_lyrics(meta_dict["title"], meta_dict["artist"], vid, worker_idx, audio_path=file_path)

            # Pop any sync-with-AI hint deposited by fetch_ultimate_lyrics when the
            # user pressed "S" on plain embedded lyrics.  None when not applicable.
            with state._lyrics_hint_lock:
                _embedded_hint = state._lyrics_hint_store.pop(vid, None)

            if lrc is state._LYRICS_SKIP:
                lrc = None  # User explicitly skipped — do not fall through to AI transcription
            elif lrc:
                lyrics_source = "synced"
            else:
                lrc = whisper_transcribe.transcribe_audio(file_path, vid, worker_idx, meta_dict=meta_dict, lyrics_hint=_embedded_hint)
                if lrc:
                    lyrics_source = "ai"
        else:
            ui.update_ui(worker_idx, msg="Lyrics Disabled")

        if stats:
            stats.record_lyrics(lyrics_source)

        # BPM/key normally come from transcribe_audio's Demucs stem — if transcription was skipped (DB lyrics found), analyze the raw file instead so every track still gets these tags.
        if "bpm" not in meta_dict:
            ui.update_ui(worker_idx, msg="Analyzing BPM & Key...")
            _features = audio_features.analyze_audio_features(file_path)
            if _features:
                meta_dict.update(_features)

        if stats:
            stats.record_bpm(meta_dict.get("bpm"))

        ui.update_ui(worker_idx, msg="Tagging & Sorting")
        new_filepath = tag_writer.apply_tags_and_move(file_path, meta_dict, lrc)

        # Feature 6: keep an .m3u8 playlist in sync with this album's folder contents.
        tag_writer._write_album_playlist(os.path.dirname(new_filepath))

        # Store fingerprint so future imports of the same audio are caught as duplicates.
        if raw_fingerprint:
            db.store_fingerprint(
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

        db.db_log_status(vid, meta_dict["title"], "SUCCESS", new_filepath)
        # Undo/activity log entry (best-effort, non-fatal) — enables the web
        # GUI's recent-activity revert feature.
        db.log_activity("archive", file_path=new_filepath, prior_path=file_path,
                         details=f"{meta_dict.get('artist')} - {meta_dict.get('title')}")
        if stats:
            stats.record_processed()
        ui.update_ui(worker_idx, msg="[bold green]Complete[/bold green]")
        time.sleep(2)
        ui.update_ui(worker_idx, "--", "Idle")

    except Exception as e:
        err_msg = str(e)[:200]   # 200 chars gives enough context for DB + dashboard without flooding
        ui.update_ui(worker_idx, msg=f"[bold red]Error: {err_msg}[/bold red]")
        db.db_log_status(vid, title, f"FAILED: {err_msg}", file_path)
        if stats:
            stats.record_failed()
        # Clean up any temp LRC that was written before the failure — without this
        # the file would linger in LOGS_DIR indefinitely after a tagging crash.
        try:
            _lrc_candidate = os.path.join(paths.LOGS_DIR, f"{vid}_lyrics.lrc")
            if os.path.exists(_lrc_candidate):
                os.remove(_lrc_candidate)
        except Exception:
            pass
        time.sleep(2)
        ui.update_ui(worker_idx, "--", "Idle")
