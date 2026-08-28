"""Lyrics acquisition pipeline: embedded-tag extraction, LRCLIB two-phase
fetch, the syncedlyrics provider chain, candidate scoring + interactive
selection, coverage-based rejection, and the atomic LRC write.

Extracted verbatim from rezakir.py's get_lrc_coverage, _lrc_to_sylt,
_score_lyrics_option, fetch_ultimate_lyrics (~lines 2959-3384), plus
_extract_embedded_art and _extract_embedded_lyrics (~lines 3387-3509).
_lrc_to_sylt's only caller, tag_writer.apply_tags_and_move, imports and
calls monster_archiver.lyrics._lrc_to_sylt rather than duplicating it.
Bare globals were rewired onto state.CONF / state._lyrics_hint_store /
state._lyrics_hint_lock / state._LYRICS_SKIP, paths.LOGS_DIR, and
ui.log/update_ui/pause_ui_and_ask.
"""
import os
import re
import logging
import tempfile
import urllib.parse

import requests
import syncedlyrics
import mutagen
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.id3 import ID3
from mutagen.mp4 import MP4, MP4Cover
from rich.table import Table
from rich import box

from monster_archiver import config, state, paths, ui
from monster_archiver.audio_io import get_audio_duration
from monster_archiver.translation import translate_lrc_file


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
            ui.log(
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
            headers={'User-Agent': f'MonsterArchiver/{config.VERSION}'},
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
                            headers={'User-Agent': f'MonsterArchiver/{config.VERSION}'},
                            timeout=(10, 30),
                        )
                        id_resp.raise_for_status()
                        id_data = id_resp.json()
                        synced  = bool(id_data.get('syncedLyrics'))
                        txt     = id_data.get('syncedLyrics') or id_data.get('plainLyrics')
                    except Exception as _id_err:
                        # FIX #3: log ID-fetch failures instead of swallowing silently.
                        if state.CONF.get("DEBUG_MODE"):
                            ui.log(f"LRCLIB /api/get/{track_id} error: {_id_err}", "dim")

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
        ui.log(
            f"LRCLIB search error: {_lrc_err}",
            "red" if state.CONF.get("DEBUG_MODE") else "dim",
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
            if not state.CONF.get("DEBUG_MODE"):
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
            ui.log(
                f"{_label} lyrics error: {_prov_err}",
                "red" if state.CONF.get("DEBUG_MODE") else "dim",
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
    if state.CONF.get("DEBUG_MODE") and len(options) > 1:
        for _rank, _o in enumerate(options):
            ui.log(
                f"[Lyrics] Rank {_rank+1}: {_o['source']} / {_o['artist']} "
                f"synced={_o['synced']} dur={_o.get('dur')} "
                f"score={_score_lyrics_option(_o, meta_artist, _audio_dur_for_scoring)}",
                "dim",
            )
    # ──────────────────────────────────────────────────────────────────────

    selected_text   = None
    selected_synced = False   # tracks the chosen option's synced flag for the coverage check below

    if len(options) == 1:
        ui.update_ui(worker_idx, msg="[bold yellow]WAITING FOR INPUT[/bold yellow]")

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
        choice = ui.pause_ui_and_ask(
            "[bold cyan]Select (1) found lyrics, (A) for AI transcription, or (0) to skip[/bold cyan]",
            choices=["0", "1", "A", "a"],
            default="1",
            table_to_print=table,
            force_interactive=True,   # ask even in watch mode — DB lyrics may be poor
        ) if options[0]["synced"] else ui.pause_ui_and_ask(
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
            with state._lyrics_hint_lock:
                state._lyrics_hint_store[vid] = options[0]["text"]
            ui.log(f"🎤 Sync-with-AI requested — {options[0]['source']} lyrics queued as Whisper context", "cyan")
            return None
        elif choice.lower() == "a":
            return None  # Signal process_local_file to fall through to AI transcription
        else:  # choice == "0" — user explicitly skipped; must NOT trigger AI transcription
            return state._LYRICS_SKIP
    else:
        ui.update_ui(worker_idx, msg="[bold yellow]WAITING FOR INPUT[/bold yellow]")

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

        choice = ui.pause_ui_and_ask(
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
                _sync_choice = ui.pause_ui_and_ask(
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
                    ui.log("⚠️  Invalid sync choice — falling through to AI transcription", "yellow")
                    return None

            _sync_opt = options[_sync_idx - 1]
            with state._lyrics_hint_lock:
                state._lyrics_hint_store[vid] = _sync_opt["text"]
            ui.log(
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
            return state._LYRICS_SKIP

    if selected_text:
        # Reject synced lyrics covering <50% of the track, falling through to AI. Use the option's own `synced` flag, not get_lrc_coverage()>0 — plain Genius text can contain bracket markers that look like timestamps.
        if selected_synced and audio_path:
            audio_duration = get_audio_duration(audio_path)
            if audio_duration > 60:
                lrc_end = get_lrc_coverage(selected_text)
                if lrc_end > 0:   # sanity guard — only meaningful for true LRC content
                    ratio = lrc_end / audio_duration
                    if ratio < 0.50:
                        ui.update_ui(worker_idx, msg=f"[yellow]Lyrics only cover {ratio:.0%} of track — falling through to AI[/yellow]")
                        return None

        out_path = os.path.join(paths.LOGS_DIR, f"{vid}_lyrics.lrc")
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
        ui.update_ui(worker_idx, msg="Translating")
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
        if state.CONF.get("DEBUG_MODE"):
            ui.log(f"Embedded lyrics extraction error: {e}", "dim")

    return None
# -----------------------------------------------
