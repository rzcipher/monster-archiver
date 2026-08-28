"""Smart metadata lookup: MusicBrainz + iTunes (concurrent), Deezer fallback,
and the interactive multi-match selection/re-search UI.

Extracted verbatim from rezakir.py's _explicit_flag (~line 203),
_fetch_deezer_genre_by_isrc and fetch_smart_metadata (~lines 2528-2957).
_explicit_flag lives here (its only caller, tag_writer.apply_tags_and_move,
imports and calls monster_archiver.metadata._explicit_flag) rather than
duplicated in tag_writer.py. Bare globals were rewired onto state.CONF,
ui.log/update_ui/pause_ui_and_ask, config.VERSION, and naming.split_artists.
"""
import re
import urllib.parse
import concurrent.futures

import requests
import musicbrainzngs
from rich.table import Table
from rich import box

from monster_archiver import config, state, ui
from monster_archiver.naming import split_artists

# Original monolith called this once at startup (right after CONF was loaded);
# this module is the one that actually uses musicbrainzngs, so it owns the call now.
musicbrainzngs.set_useragent("MonsterArchiver", config.VERSION, state.CONF.get("MUSICBRAINZ_CONTACT_EMAIL", ""))


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


def _fetch_deezer_genre_by_isrc(isrc):
    """Fetch album-level genre from Deezer via a track ISRC (more reliable
    than MusicBrainz's crowd-sourced tags). Two calls: GET /track/isrc:{isrc}
    to resolve the album ID (the track endpoint has no genre data), then
    GET /album/{album_id} for the genre list. Returns the first genre name,
    or None on failure/no match.
    """
    if not isrc:
        return None
    _headers = {"User-Agent": f"MonsterArchiver/{config.VERSION}"}
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
                # Escape Lucene special characters (most importantly the
                # double-quote, which would terminate the phrase and break —
                # or garbage — the whole search) before embedding values in
                # the query string.
                def _lucene_escape(v):
                    return re.sub(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)', r'\\\1', str(v))
                if _uploader_is_usable:
                    mb_query = f'recording:"{_lucene_escape(clean_title)}" AND artist:"{_lucene_escape(query_uploader)}"'
                else:
                    mb_query = f'recording:"{_lucene_escape(clean_title)}"'

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
                        with state._caa_cache_lock:
                            _cached_cover = state._CAA_LOOKUP_CACHE.get(release_mbid)
                            if _cached_cover is not None:
                                state._CAA_LOOKUP_CACHE.move_to_end(release_mbid)   # LRU touch

                        if _cached_cover is not None:
                            # Feature 5: another track in this batch already resolved this exact
                            # release's cover art — reuse it instead of re-hitting CAA.
                            cover_url = _cached_cover
                            if state.CONF.get("DEBUG_MODE"):
                                ui.log(f"CAA lookup: reused cached release {release_mbid}", "dim cyan")
                        else:
                            # Query the CAA JSON index for the direct full-res front-image URL (avoids the /front redirect hop and thumbnail risk).
                            _caa_api = f"https://coverartarchive.org/release/{release_mbid}"
                            try:
                                _caa_r = requests.get(
                                    _caa_api,
                                    timeout=8,
                                    headers={
                                        "Accept":     "application/json",
                                        "User-Agent": f"MonsterArchiver/{config.VERSION}",
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
                                if state.CONF.get("DEBUG_MODE"):
                                    ui.log(f"MusicBrainz CAA JSON fetch failed for {release_mbid}: {_caa_err}", "dim")
                            # JSON failed or returned no images — fall back to the /front redirect URL.
                            if not cover_url:
                                _caa_front = f"https://coverartarchive.org/release/{release_mbid}/front"
                                try:
                                    if requests.head(_caa_front, allow_redirects=True, timeout=5).status_code == 200:
                                        cover_url = _caa_front
                                except Exception as _caa_fallback_err:
                                    if state.CONF.get("DEBUG_MODE"):
                                        ui.log(f"MusicBrainz CAA HEAD fallback failed for {release_mbid}: {_caa_fallback_err}", "dim")

                            # Cache the outcome — including "" for a confirmed no-art release — so
                            # the rest of this run never re-queries the same release_mbid.
                            with state._caa_cache_lock:
                                state._CAA_LOOKUP_CACHE[release_mbid] = cover_url
                                state._CAA_LOOKUP_CACHE.move_to_end(release_mbid)
                                while len(state._CAA_LOOKUP_CACHE) > state._CAA_CACHE_MAX_ENTRIES:
                                    state._CAA_LOOKUP_CACHE.popitem(last=False)

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
                if state.CONF.get("DEBUG_MODE"):
                    ui.log(f"MusicBrainz fetch error: {_mb_err}", "dim red")
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
                if state.CONF.get("DEBUG_MODE"):
                    ui.log(f"iTunes fetch error: {_it_err}", "dim red")
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
            ui.update_ui(worker_idx, msg="[bold yellow]WAITING FOR INPUT[/bold yellow]")
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

            choice = ui.pause_ui_and_ask(
                "[bold cyan]Enter Key, 0 to use raw data, or 'M' to manual search[/bold cyan]",
                choices=[str(i) for i in range(len(results)+1)] + ["M", "m"],
                default="1",
                table_to_print=table,
            )
            if choice.lower() == 'm':
                manual_query = ui.pause_ui_and_ask("[bold yellow]Enter Custom Search Query: [/bold yellow]", choices=None, default="")
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
            ui.update_ui(worker_idx, msg="[bold yellow]WAITING FOR INPUT[/bold yellow]")
            choice = ui.pause_ui_and_ask(
                f"[bold red]No metadata found for '{query_title}'.[/bold red]\n[bold cyan]Press 'M' to manually search or '0' to skip:[/bold cyan]",
                choices=["0", "M", "m"],
                default="0",   # Watch-mode safe: "M" auto-resolves to a bogus manual search the daemon can't handle
            )
            if choice.lower() == 'm':
                manual_query = ui.pause_ui_and_ask("[bold yellow]Enter Custom Search Query: [/bold yellow]", choices=None, default="")
                if manual_query:
                    query_title, query_uploader = manual_query, "Unknown"
                    continue   # re-search with new query
            return selected_meta   # "0" or empty manual → return defaults
