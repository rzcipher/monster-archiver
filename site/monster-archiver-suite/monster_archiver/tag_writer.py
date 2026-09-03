"""Writes final tags (FLAC/MP3/M4A) and cover art onto the archived copy of a
track, regenerates the per-album .m3u8 playlist, and normalises the
ALBUMARTIST tag during album-merge. Also carries the small embedded-art /
lyrics-format helpers apply_tags_and_move() depends on.
"""
import os
import re
import shutil
import time

import mutagen
import requests
from mutagen.flac import FLAC, Picture
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, USLT, SYLT, TCON, TCOM, TPE1, TPE2, TEXT, TBPM, TKEY, TPOS, TSRC, TLAN, TXXX
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm, AtomDataType

from . import lyrics
from . import metadata
from . import naming
from . import state
from . import ui

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


def _strip_lrc_timestamps(text):
    """Remove LRC [mm:ss.xx] and Enhanced-LRC <mm:ss.xx> markers from *text*,
    leaving clean lyrics for plain-text tag atoms (USLT, ©lyr). Also strips
    leading/trailing whitespace.
    """
    text = re.sub(r'(?m)^(?:\[\d+:\d+(?:\.\d+)?\])+', '', text)
    text = re.sub(r'<\d+:\d+(?:\.\d+)?>', '', text)
    return '\n'.join(ln.strip() for ln in text.splitlines()).strip()


def apply_tags_and_move(file_path, meta, lyrics_path=None):
    ext = os.path.splitext(file_path)[1].lstrip('.').lower()
    # _unique_path/_compute_destination now live in monster_archiver.naming.
    # reserve=True claims new_filepath against concurrent workers (TOCTOU
    # guard) — released in the finally below once the copy exists on disk
    # (os.path.exists takes over) or the attempt failed.
    folder_structure, new_filepath, track_formatted, primary_artist = naming._compute_destination(file_path, meta, reserve=True)

    # Composer/lyricist fallback to artist name — metadata sources (MusicBrainz/iTunes/Deezer) often return "Unknown" for phonk/Telegram-sourced tracks.
    effective_composer = (meta.get("composer") or "").strip()
    if not effective_composer or effective_composer == "Unknown":
        effective_composer = (meta.get("artist") or "").strip()

    effective_lyricist = (meta.get("lyricist") or "").strip()
    if not effective_lyricist or effective_lyricist == "Unknown":
        effective_lyricist = (meta.get("artist") or "").strip()

    # Feature 4: normalise the advisory flag once up front — reused across all three format blocks below.
    # _explicit_flag now lives in monster_archiver.metadata (its home module).
    _explicit_val = metadata._explicit_flag(meta)
    # Feature 3: 3-letter language code for LANGUAGE/TLAN, once up front too.
    _lang_code = _ISO_639_1_TO_2T.get(meta.get("language", ""), meta.get("language", "")) if meta.get("language") else ""

    try:
        os.makedirs(folder_structure, exist_ok=True)
        shutil.copy2(file_path, new_filepath)
    finally:
        # Copy either landed (os.path.exists() now guards the path for other
        # workers) or raised (path is free again either way) — release the
        # in-memory reservation so the set doesn't grow unboundedly.
        naming.release_destination(new_filepath)

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
        if state.CONF.get("DEBUG_MODE"):
            ui.log(f"Easy-tag write failed: {e}", "red")

    img_data = None
    mime_type = "image/jpeg"

    if meta.get("cover"):
        _cover_url = meta["cover"]

        # Feature 5: batch-scoped cache — an album drop resolves every track to the
        # same cover URL, so check for already-downloaded bytes before hitting the network.
        with state._art_cache_lock:
            _cache_hit = state._ALBUM_ART_CACHE.get(_cover_url)
            if _cache_hit is not None:
                state._ALBUM_ART_CACHE.move_to_end(_cover_url)   # LRU touch

        if _cache_hit is not None:
            img_data, mime_type = _cache_hit
            if state.CONF.get("DEBUG_MODE"):
                ui.log(f"Cover: reused cached art for this batch ({mime_type})", "dim cyan")
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
                with state._art_cache_lock:
                    state._ALBUM_ART_CACHE[_cover_url] = (img_data, mime_type)
                    state._ALBUM_ART_CACHE.move_to_end(_cover_url)
                    while len(state._ALBUM_ART_CACHE) > state._ART_CACHE_MAX_ENTRIES:
                        state._ALBUM_ART_CACHE.popitem(last=False)   # evict oldest

    # Fall back to art already embedded in the source file when no
    # online cover was found — preserves artwork from well-tagged rips.
    if not img_data:
        # _extract_embedded_art now lives in monster_archiver.lyrics (its home module).
        img_data, mime_type = lyrics._extract_embedded_art(file_path)
        if img_data and state.CONF.get("DEBUG_MODE"):
            ui.log(f"Cover: using embedded art from source file ({mime_type})", "cyan")

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
            # ISRC — industry-standard recording ID, already fetched by
            # fetch_smart_metadata() but previously never actually written
            # to the file. ISRC is a free-text Vorbis comment key (no
            # standardised tag class needed, unlike MP3/M4A below).
            if meta.get("isrc"):
                f_audio["ISRC"] = str(meta["isrc"])
            f_audio["ARTIST"] = meta["artist_list"]
    # ALBUMARTIST keeps multi-artist albums grouped across all players; always the single primary_artist that decided the folder name, even when ARTIST is a list.
            f_audio["ALBUMARTIST"] = primary_artist
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
                # _lrc_to_sylt now lives in monster_archiver.lyrics (its home module).
                sylt_entries = lyrics._lrc_to_sylt(txt)
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
            # ISRC — TSRC is the standard ID3v2 frame for it; imported above
            # but never actually written until now.
            if meta.get("isrc"):
                m_audio.tags.add(TSRC(encoding=3, text=str(meta["isrc"])))
            m_audio["TPE1"] = TPE1(encoding=3, text=meta["artist_list"])
            # TPE2 = Album Artist — single normalised string, not a list, matching Plex/Rockbox/foobar2000 expectations. Uses the same primary_artist that decided the folder name.
            m_audio.tags.add(TPE2(encoding=3, text=[primary_artist]))
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
            # Apple Music, Infuse, Jellyfin, and other M4A-aware players. Uses the
            # same primary_artist that decided the folder name.
            m4_audio["aART"] = [primary_artist]
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
            # ISRC — M4A has no native atom for this, so it goes in the same
            # iTunes freeform namespace as initialkey/LYRICIST above.
            if meta.get("isrc"):
                m4_audio["----:com.apple.iTunes:ISRC"] = [
                    MP4FreeForm(str(meta["isrc"]).encode("utf-8"), dataformat=AtomDataType.UTF8)
                ]
            m4_audio.save()

    except Exception as e:
        # new_filepath is always assigned before this try block; no conditional needed.
        ui.log(f"Advanced tagging failed for {new_filepath}: {e}", "bold red")
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
    with state._m3u_lock:
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

            playlist_name = naming.sanitize_filename(os.path.basename(folder_structure), 100) + ".m3u8"
            playlist_path = os.path.join(folder_structure, playlist_name)

            lines = ["#EXTM3U"]
            for _track_no, fname, duration, title in entries:
                lines.append(f"#EXTINF:{duration},{title}")
                lines.append(fname)   # relative path — playlist lives in the same folder as the tracks

            with open(playlist_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception as _m3u_err:
            if state.CONF.get("DEBUG_MODE"):
                ui.log(f"Playlist write failed for {folder_structure}: {_m3u_err}", "dim red")


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
        if state.CONF.get("DEBUG_MODE"):
            ui.log(f"ALBUMARTIST normalisation failed for {filepath}: {e}", "dim red")
