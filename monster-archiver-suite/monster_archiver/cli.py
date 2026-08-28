"""Entry point: argparse, the interactive engine/language/lyrics prompts, and
routing to the right session type (--scan, --merge-albums, --retry-failed,
--watch, --webui-transcribe, or the default interactive session).
"""
import argparse
import json
import os
import sys
import time

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from . import activity
from . import album_merge
from . import config
from . import db
from . import library_scan
from . import naming
from . import paths
from . import sessions
from . import state
from . import watch_daemon
from . import whisper_transcribe


def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')


def display_banner():
    clear_screen()

    # ── Feature reference table ───────────────────────────────────────────────
    feat = Table.grid(padding=(0, 2))
    feat.add_column(style="bold cyan",    no_wrap=True, min_width=22)
    feat.add_column(style="white")

    feat.add_row("▸ Interactive",
                 "Drop file paths into the prompt — prefix with [bold]force [/bold] to reprocess")
    feat.add_row("▸ --force",
                 "Skip SUCCESS / DUPLICATE checks — re-run the full pipeline on every file")
    feat.add_row("▸ --watch [PATH]",
                 "Daemon mode: auto-archives audio files dropped into the watched folder  "
                 "[dim](also typeable at the prompt)[/dim]")
    feat.add_row("   Default path",
                 f"[dim]{paths.DEFAULT_WATCH_PATH}[/dim]")
    feat.add_row("▸ --retry-failed",
                 "Re-queue every FAILED entry recorded in the SQLite database (implies --force)")
    feat.add_row("▸ --scan",
                 "Library health scan: reports files with missing genre, art, lyrics, year, or BPM "
                 "[dim](outputs Rich table + scan_report.csv)[/dim]")
    feat.add_row("▸ --fix",
                 "Used with --scan: re-process every flagged file to attempt metadata re-fetch")
    feat.add_row("▸ --engine N",
                 "Translation engine — 0=off  1=Google-Auto  2=MyMemory  "
                 "3=Google-Line  5=Local-LLM")
    feat.add_row("▸ --lang LANG",
                 "Source language override — auto | ja | ko | en | es | fr")

    state.console.print(Panel(
        feat,
        title=f"[bold white]Monster Archiver — NEXUS Edition v{config.VERSION}[/bold white]",
        subtitle="[dim]Ctrl+C to quit[/dim]",
        border_style="blue",
        expand=True,
        padding=(1, 2),
    ))
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Monster Archiver — Nexus Edition",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  # Normal interactive session
  python rezakir.py

  # Re-process already-archived files
  python rezakir.py --force

  # Batch-recover every FAILED entry from the database (scriptable, cron-friendly)
  python rezakir.py --retry-failed --engine 1 --lang auto

  # Watch folder daemon
  python rezakir.py --watch ~/Downloads/Music

  # Library health scan (report only)
  python rezakir.py --scan

  # Library health scan + auto-fix (re-archive every flagged file)
  python rezakir.py --scan --fix --engine 1 --lang auto

  # Preview a folder drop before committing to it — nothing is written or moved
  python rezakir.py --dry-run

  # Find + merge albums split across multiple folders (e.g. by a "feat." credit)
  python rezakir.py --merge-albums

  # Preview which albums would be merged, without touching any files
  python rezakir.py --merge-albums --dry-run

  # Web-app-driven scan/merge against a specific library folder, JSON output
  python rezakir.py --scan --fix --library-dir ~/Music/MyLibrary --json
""",
    )
    parser.add_argument(
        "--watch", metavar="PATH", nargs="?",
        const="",   # bare --watch (no PATH) → args.watch = "" (falsy but not None)
        help=(
            "Daemon mode: monitor PATH for new audio files and archive them automatically.  "
            "Omit PATH to use the default: your ~/Downloads folder"
        ),
    )
    parser.add_argument(
        "--force", action="store_true",
        help=(
            "Force-reprocess files even if they are already marked SUCCESS or DUPLICATE "
            "in the database.  Equivalent to typing 'force ' before every path in the "
            "interactive session."
        ),
    )
    parser.add_argument(
        "--retry-failed", action="store_true", dest="retry_failed",
        help=(
            "Read the SQLite database for all FAILED entries and reprocess them.  "
            "Implies --force.  File paths must have been recorded during a previous run "
            "(any run after this version).  Files that have moved or been deleted are "
            "reported but skipped."
        ),
    )
    parser.add_argument(
        "--scan", action="store_true",
        help=(
            "Library health scan mode: walk MUSIC_DIR and report every file with "
            "missing genre, cover art, lyrics, year, or BPM = 0.  "
            "Outputs a Rich table to the console and saves a CSV to LOGS_DIR. "
            "Pair with --fix to attempt metadata re-fetch for all flagged files."
        ),
    )
    parser.add_argument(
        "--fix", action="store_true",
        help=(
            "Used together with --scan: after reporting flagged files, "
            "re-run the full archive pipeline (force mode) on every flagged file "
            "to attempt metadata re-fetch and tag repair."
        ),
    )
    parser.add_argument(
        "--engine", metavar="N", choices=["0", "1", "2", "3", "5"], default=None,
        help=(
            "Translation engine for this session — skips the interactive prompt.  "
            "0=off  1=Google-auto  2=MyMemory  3=Google-line  5=Local-LLM"
        ),
    )
    parser.add_argument(
        "--lang", metavar="LANG", choices=["auto", "ja", "ko", "en", "es", "fr", "fa"], default=None,
        help=(
            "Source audio language — skips the interactive language prompt.  "
            "auto=detect  ja=Japanese  ko=Korean  en=English  es=Spanish  fr=French  fa=Persian"
        ),
    )
    parser.add_argument(
        "--no-lyrics", action="store_true", dest="no_lyrics",
        help=(
            "Skip all lyrics fetching and AI transcription for this session.  "
            "Files will be tagged with metadata only — no LRC file, no Whisper run."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help=(
            "Preview mode: run fingerprinting, duplicate/quality checks, and metadata "
            "lookup as normal and report exactly where each file would land — but never "
            "copy, tag, move, or delete anything, and never write to the database.  "
            "Lyrics fetching / AI transcription are skipped since they don't affect the "
            "preview.  Works with a normal session, --retry-failed, --watch, --scan --fix, "
            "and --merge-albums."
        ),
    )
    parser.add_argument(
        "--merge-albums", action="store_true", dest="merge_albums",
        help=(
            "Library maintenance mode: scan MUSIC_DIR for the same album split across "
            "multiple folders — usually caused by a featured artist changing the folder-"
            "naming artist string on just some tracks — and merge them into one folder, "
            "keeping the copy with the most tracks and normalising ALBUMARTIST across the "
            "merged set.  Combine with --dry-run to preview the merges first."
        ),
    )
    parser.add_argument(
        "--library-dir", metavar="PATH", default=None, dest="library_dir",
        help=(
            "Use PATH instead of the default ~/Music/Monster_Library for this run "
            "(overrides MUSIC_DIR/LOGS_DIR/DB_FILE). Used by the web app's Library "
            "panel so scan/fix/merge can target a user-configured folder."
        ),
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_mode",
        help=(
            "Non-interactive JSON output mode for --scan/--fix/--merge-albums: prints one "
            "JSON summary to stdout instead of Rich tables (log output goes to stderr, "
            "same trick --webui-transcribe uses), and never prompts — --fix/--merge-albums "
            "auto-resolve every choice instead of waiting on stdin. Used by the web app."
        ),
    )
    parser.add_argument(
        "--activity-log", metavar="LIMIT", nargs="?", const=20, type=int, default=None,
        dest="activity_log",
        help=(
            "Print the last LIMIT (default 20) activity_log entries as JSON (implies --json). "
            "Used by the web app's Activity panel."
        ),
    )
    parser.add_argument(
        "--revert-activity", metavar="ID", type=int, default=None, dest="revert_activity",
        help=(
            "Revert one activity_log entry by id — moves the file back to where it was "
            "before the archive/merge that moved it (implies --json). Used by the web app's "
            "Activity panel revert button."
        ),
    )
    parser.add_argument(
        "--webui-transcribe", metavar="PATH", default=None, dest="webui_transcribe",
        help=(
            "Non-interactive one-shot mode for the web GUI (webapp/): run AI transcription "
            "(Demucs vocal isolation + multi-pass Faster-Whisper) on a single file and print "
            "the resulting LRC lyrics as JSON on stdout — all log output goes to stderr so "
            "stdout stays valid JSON. Translation is skipped; the web GUI translates "
            "separately. Combine with --lang and the --webui-title/-artist/-genre primers."
        ),
    )
    parser.add_argument("--webui-title",  metavar="TITLE",  default=None, dest="webui_title",
        help="Track title, used only to prime Faster-Whisper's initial_prompt in --webui-transcribe mode.")
    parser.add_argument("--webui-artist", metavar="ARTIST", default=None, dest="webui_artist",
        help="Track artist, used only to prime Faster-Whisper's initial_prompt in --webui-transcribe mode.")
    parser.add_argument("--webui-genre",  metavar="GENRE",  default=None, dest="webui_genre",
        help="Track genre, used only to prime Faster-Whisper's initial_prompt in --webui-transcribe mode.")
    parser.add_argument(
        "--webui-archive", metavar="PATH", default=None, dest="webui_archive",
        help=(
            "Non-interactive one-shot mode for the web GUI (webapp/): given an "
            "already-tagged audio file at PATH and --webui-metadata, compute the "
            "same Artist/Year - Album destination naming.py's NAMING_FOLDER_TEMPLATE "
            "would produce for it (creating the folder), and print {\"folder\":, "
            "\"path\":, \"primary_artist\":} as JSON on stdout. Does not copy or move "
            "PATH itself — the web GUI does that once it has the destination. "
            "Combine with --library-dir to target a specific library folder. "
            "Implies --json."
        ),
    )
    parser.add_argument(
        "--webui-metadata", metavar="JSON", default=None, dest="webui_metadata",
        help=(
            "JSON object of tag fields (title/artist/album/year/track/disc/genre/isrc) "
            "used to render the naming template in --webui-archive mode. Required "
            "when --webui-archive is given."
        ),
    )
    parser.add_argument(
        "--webui-video-caption", metavar="PATH", default=None, dest="webui_video_caption",
        help=(
            "Non-interactive one-shot mode for the web GUI (webapp/): extract the audio "
            "track from the video at PATH, transcribe it (Faster-Whisper, Demucs/BPM-key "
            "analysis skipped since this isn't music), diarize speakers with "
            "pyannote.audio, and print {\"segments\": [{start,end,text,speaker}], "
            "\"speakers\": [...], \"audioDuration\": ...} as JSON on stdout — nothing is "
            "burned into the video yet, so the web GUI can show the transcript for "
            "correction first. Combine with --lang and the --webui-title/-artist/-genre "
            "primers exactly like --webui-transcribe. Requires HUGGINGFACE_TOKEN in "
            "config.json (see video_captions.py)."
        ),
    )
    parser.add_argument(
        "--webui-burn-subtitles", metavar="PATH", default=None, dest="webui_burn_subtitles",
        help=(
            "Non-interactive one-shot mode for the web GUI: burn the (corrected) caption "
            "segments from --webui-segments-file into the video at PATH as color-coded, "
            "per-speaker ASS subtitles, and print {\"outputPath\": ...} as JSON on stdout. "
            "Requires --webui-segments-file."
        ),
    )
    parser.add_argument(
        "--webui-segments-file", metavar="PATH", default=None, dest="webui_segments_file",
        help=(
            "Path to a JSON file containing the caption segments array "
            "([{start,end,text,speaker}, ...]) for --webui-burn-subtitles. A file rather "
            "than an inline argument because a full video transcript can exceed the "
            "Windows command-line length limit."
        ),
    )
    parser.add_argument(
        "--webui-font-size", metavar="N", type=int, default=None, dest="webui_font_size",
        help=(
            "Optional caption font size (1280x720 script-coordinate space, same as "
            "everything else in build_ass()'s Style line) for --webui-burn-subtitles. "
            "Clamped to [16, 96]; defaults to 40 if omitted — see build_ass() in "
            "video_captions.py."
        ),
    )
    args = parser.parse_args()

    if args.webui_transcribe:
        state.console = Console(file=sys.stderr)  # keep stdout clean — it must stay valid JSON
        state.GLOBAL_AUDIO_LANG = args.lang if args.lang else "auto"
        state.GLOBAL_TRANS_METHOD = "0"  # the web GUI translates separately via its own local-LLM call
        meta_dict = {}
        if args.webui_title:  meta_dict["title"]  = args.webui_title
        if args.webui_artist: meta_dict["artist"] = args.webui_artist
        if args.webui_genre:  meta_dict["genre"]  = args.webui_genre
        vid = f"webui_{int(time.time())}_{os.getpid()}"
        try:
            # word_level_lrc=False — the web GUI wants normal, one-timestamp-per-line
            # LRC (sentence-level), not Enhanced-LRC karaoke-style per-word tags,
            # regardless of the CLI's own WORD_LEVEL_LRC config default.
            out_path = whisper_transcribe.transcribe_audio(
                args.webui_transcribe, vid, worker_idx=0, meta_dict=meta_dict or None,
                word_level_lrc=False,
            )
            if not out_path:
                print(json.dumps({"error": "No speech detected (instrumental or silent track?)"}))
                sys.exit(1)
            with open(out_path, "r", encoding="utf-8") as f:
                lrc_text = f.read()
            print(json.dumps({"lrc": lrc_text}))
        except Exception as e:
            print(json.dumps({"error": str(e)}))
            sys.exit(1)
        return

    if args.webui_video_caption:
        state.console = Console(file=sys.stderr)  # keep stdout clean — it must stay valid JSON
        state.GLOBAL_AUDIO_LANG = args.lang if args.lang else "auto"
        state.GLOBAL_TRANS_METHOD = "0"  # captions aren't translated at transcribe time — the web GUI reviews/translates separately
        meta_dict = {}
        if args.webui_title:  meta_dict["title"]  = args.webui_title
        if args.webui_artist: meta_dict["artist"] = args.webui_artist
        if args.webui_genre:  meta_dict["genre"]  = args.webui_genre
        vid = f"webui_{int(time.time())}_{os.getpid()}"
        try:
            from . import video_captions
            result = video_captions.caption_video(
                args.webui_video_caption, vid, worker_idx=0,
                meta_dict=meta_dict or None, lang=state.GLOBAL_AUDIO_LANG,
            )
            if not result:
                print(json.dumps({"error": "No speech detected in the video's audio track"}))
                sys.exit(1)
            print(json.dumps(result))
        except Exception as e:
            print(json.dumps({"error": str(e)}))
            sys.exit(1)
        return

    if args.webui_burn_subtitles:
        state.console = Console(file=sys.stderr)
        if not args.webui_segments_file:
            print(json.dumps({"error": "--webui-burn-subtitles requires --webui-segments-file"}))
            sys.exit(1)
        vid = f"webui_{int(time.time())}_{os.getpid()}"
        try:
            with open(args.webui_segments_file, "r", encoding="utf-8") as f:
                segments = json.load(f)
            if not isinstance(segments, list) or not segments:
                print(json.dumps({"error": "Segments file must contain a non-empty JSON array"}))
                sys.exit(1)
            from . import video_captions
            burn_kwargs = {}
            if args.webui_font_size:
                burn_kwargs["font_size"] = args.webui_font_size
            out_path = video_captions.burn_video_subtitles(args.webui_burn_subtitles, segments, vid, **burn_kwargs)
            if not out_path:
                print(json.dumps({"error": "ffmpeg failed to burn subtitles — rerun with DEBUG_MODE for details"}))
                sys.exit(1)
            print(json.dumps({"outputPath": out_path}))
        except Exception as e:
            print(json.dumps({"error": str(e)}))
            sys.exit(1)
        return

    # --activity-log / --revert-activity / --webui-archive are always non-interactive
    # JSON calls, same as passing --json explicitly.
    if args.activity_log is not None or args.revert_activity is not None or args.webui_archive is not None:
        args.json_mode = True

    # --library-dir overrides MUSIC_DIR/LOGS_DIR/DB_FILE for this whole invocation —
    # must happen before init_db() so the right database file gets created/opened.
    if args.library_dir:
        paths.set_library_dir(args.library_dir)

    if args.json_mode:
        state.console = Console(file=sys.stderr)  # keep stdout clean — it must stay valid JSON
        state.NON_INTERACTIVE = True

    db.init_db()

    if args.json_mode:
        # --scan/--fix/--merge-albums/--activity-log/--revert-activity only —
        # no interactive banner/prompts, no translation-engine/language/lyrics
        # questions (the web app doesn't need them here), just run the
        # requested action and print its summary as one line of JSON on stdout.
        try:
            if args.scan:
                result = library_scan.run_library_scan(fix=args.fix)
            elif args.merge_albums:
                result = album_merge.run_album_merge(dry_run=args.dry_run)
            elif args.revert_activity is not None:
                result = activity.run_revert(args.revert_activity)
            elif args.activity_log is not None:
                result = {"entries": db.get_recent_activity(limit=args.activity_log)}
            elif args.webui_archive is not None:
                if not args.webui_metadata:
                    result = {"error": "--webui-archive requires --webui-metadata"}
                else:
                    try:
                        _meta_in = json.loads(args.webui_metadata)
                    except Exception as _json_err:
                        result = {"error": f"Invalid --webui-metadata JSON: {_json_err}"}
                    else:
                        _artist_str = _meta_in.get("artist") or "Unknown"
                        _meta = {
                            "title":        _meta_in.get("title") or "Unknown",
                            "artist":       _artist_str,
                            "artist_list":  _meta_in.get("artist_list") or naming.split_artists(_artist_str),
                            "album":        _meta_in.get("album") or "Unknown Album",
                            "year":         _meta_in.get("year") or "Unknown Year",
                            "track":        _meta_in.get("track") or "1",
                            "disc":         _meta_in.get("disc") or "1",
                            "genre":        _meta_in.get("genre") or "Unknown",
                            "isrc":         _meta_in.get("isrc") or "",
                            "composer":     _meta_in.get("composer") or "Unknown",
                        }
                        folder_structure, new_filepath, track_formatted, primary_artist = \
                            naming._compute_destination(args.webui_archive, _meta)
                        os.makedirs(folder_structure, exist_ok=True)
                        result = {
                            "folder": folder_structure,
                            "path": new_filepath,
                            "primary_artist": primary_artist,
                        }
            else:
                print(json.dumps({"error": "--json requires --scan, --merge-albums, --activity-log, --revert-activity, or --webui-archive"}))
                sys.exit(1)
            print(json.dumps(result))
        except Exception as e:
            print(json.dumps({"error": str(e)}))
            sys.exit(1)
        return

    display_banner()

    state.GLOBAL_DRY_RUN = args.dry_run
    if state.GLOBAL_DRY_RUN:
        state.console.print("[bold cyan]🔍 Dry-run mode — previewing only, no files will be written, moved, or deleted.[/bold cyan]\n")

    # --merge-albums never touches metadata fetch, lyrics, or translation — skip these prompts entirely.
    if not args.merge_albums:
        # ── Translation engine ────────────────────────────────────────────────
        if args.engine is not None:
            state.GLOBAL_TRANS_METHOD = args.engine
            state.console.print(f"[dim]Translation engine set via CLI: {args.engine}[/dim]")
        else:
            state.console.print("[bold magenta]🌍 Select Translation Engine For This Session:[/bold magenta]")
            state.console.print("1. [bold green]Google Translate (Smart Auto)[/bold green] - Auto-detects English, Romaji, and Kanji (Recommended)")
            state.console.print("2. [bold yellow]MyMemory (Batch API)[/bold yellow] - Good fallback")
            state.console.print("3. [bold red]Google Translate (Line-by-Line)[/bold red] - Slow")
            _local_model = state.CONF.get("LOCAL_LLM_MODEL", "gemma4:12b")
            state.console.print(f"5. [bold cyan]🖥️  Local LLM (Ollama · {_local_model})[/bold cyan] - Runs on your GPU — no rate limits, no token caps, no internet needed")
            state.console.print("0. [bold white]Disable Lyrics Translation[/bold white]")
            state.GLOBAL_TRANS_METHOD = Prompt.ask(
                "\n[bold cyan]Choose (0-3, 5)[/bold cyan]",
                choices=["0", "1", "2", "3", "5"],
                default=state.CONF["DEFAULT_TRANSLATION_ENGINE"],
            )

        # ── Source audio language ─────────────────────────────────────────────
        if args.lang is not None:
            state.GLOBAL_AUDIO_LANG = args.lang
            state.console.print(f"[dim]Audio language set via CLI: {args.lang}[/dim]")
        else:
            state.console.print("\n[bold magenta]🗣️ Select Source Audio Language For AI Adapter:[/bold magenta]")
            state.console.print("1. [bold cyan]Auto-Detect Language[/bold cyan] - Recommended for mixed libraries (Arknights, etc.)")
            state.console.print("2. [bold green]Japanese (ja)[/bold green] - Perfect for archiving J-Pop/Anime tracks")
            state.console.print("3. [bold yellow]English (en)[/bold yellow] - Ideal for Western tracks")
            state.console.print("4. [bold blue]Spanish (es)[/bold blue]")
            state.console.print("5. [bold magenta]French (fr)[/bold magenta] - French / Francophone tracks")
            state.console.print("6. [bold green]Korean (ko)[/bold green] - K-Pop / K-Drama tracks")
            lang_choice = Prompt.ask(
                "\n[bold cyan]Choose Audio Language (1-6)[/bold cyan]",
                choices=["1", "2", "3", "4", "5", "6"],
                default="1",
            )
            state.GLOBAL_AUDIO_LANG = {"1": "auto", "2": "ja", "3": "en", "4": "es", "5": "fr", "6": "ko"}[lang_choice]

        # ── Lyrics enable / disable ─────────────────────────────────────────────
        if args.no_lyrics:
            state.GLOBAL_ENABLE_LYRICS = False
            state.console.print("[dim]Lyrics disabled via --no-lyrics flag.[/dim]")
        else:
            state.console.print("\n[bold magenta]🎤 Lyrics Options:[/bold magenta]")
            state.console.print("Y. [bold green]Yes — fetch DB lyrics and run AI transcription as fallback[/bold green] (default)")
            state.console.print("N. [bold red]No  — skip all lyrics entirely (no DB fetch, no Whisper)[/bold red]")
            _lyrics_choice = Prompt.ask(
                "\n[bold cyan]Enable lyrics fetching for this session? (Y/N)[/bold cyan]",
                choices=["Y", "y", "N", "n"],
                default="Y",
            )
            state.GLOBAL_ENABLE_LYRICS = _lyrics_choice.upper() == "Y"
            if not state.GLOBAL_ENABLE_LYRICS:
                state.console.print("[yellow]⚠  Lyrics disabled — files will be tagged with metadata only.[/yellow]")
    # ─────────────────────────────────────────────────────────────────────────

    # ── Route to the right session type ──────────────────────────────────────
    # ── Guard: --fix without --scan is a no-op ───────────────────────────────
    if args.fix and not args.scan:
        state.console.print("[bold yellow]⚠  --fix has no effect without --scan.  "
                      "Did you mean: --scan --fix?[/bold yellow]")

    try:
        if args.scan:
            # --scan (+optional --fix): prompts above are asked even in scan-only mode since --fix may need them for re-archiving; scan-only never runs the pipeline.
            library_scan.run_library_scan(fix=args.fix)
        elif args.merge_albums:
            # --merge-albums: pure folder/tag consolidation — never touches metadata fetch or lyrics.
            album_merge.run_album_merge(dry_run=state.GLOBAL_DRY_RUN)
        elif args.retry_failed:
            # --retry-failed: recover every FAILED entry from the DB; implies force.
            sessions._run_retry_failed_session()
        elif args.watch is not None:
            # Watch-folder daemon — args.watch is "" when --watch given with no path.
            watch_path = os.path.abspath(args.watch or paths.DEFAULT_WATCH_PATH)
            if not os.path.isdir(watch_path):
                state.console.print(f"[bold red]Watch path does not exist or is not a directory: {watch_path}[/bold red]")
                sys.exit(1)
            watch_daemon.run_watch_mode(watch_path)
        else:
            # Normal interactive session; --force propagates into every batch.
            sessions._run_interactive_session(force_override=args.force)
    except (KeyboardInterrupt, EOFError):
        state.console.print("\n[bold red]Interrupt detected. Exiting...[/bold red]")
        sys.exit(0)
