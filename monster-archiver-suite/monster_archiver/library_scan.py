"""Library health scan (--scan/--fix): walks the library folder and reports
files with missing genre/year/BPM/cover/lyrics, writing a Rich table + CSV
report. --fix re-queues every flagged file through the normal pipeline.

Returns a summary dict in addition to printing, so cli.py's --json mode
(for the web app's Library panel) can report results as JSON without this
module needing to know anything about JSON — cli.py just points
state.console at stderr first, same trick --webui-transcribe already uses.
"""
import concurrent.futures
import csv
import os

import mutagen
from rich.live import Live
from rich.table import Table
from rich import box

from . import lyrics
from . import paths
from . import pipeline
from . import state
from . import ui


def run_library_scan(fix=False):
    """Walk MUSIC_DIR and report files with missing/poor metadata: missing
    genre, no cover art, no lyrics tag, missing year, or BPM 0. Outputs a
    Rich table and CSV to LOGS_DIR. When fix=True, flagged files are
    re-queued through fetch_smart_metadata + apply_tags_and_move using the
    same worker/stats infrastructure as a normal batch.
    """
    audio_extensions = ('.mp3', '.flac', '.m4a', '.wav', '.ogg', '.opus', '.mkv', '.webm', '.mp4')
    state.console.print(f"\n[bold cyan]🔍 Library Health Scan — {paths.MUSIC_DIR}[/bold cyan]\n")

    issues = []   # list of (path, list_of_issue_strings)

    all_files = []
    _logs_abs = os.path.abspath(paths.LOGS_DIR) + os.sep
    for root, _, files in os.walk(paths.MUSIC_DIR):
        # Skip the .logs subdirectory itself (os.sep-anchored so a sibling like ".logs_backup" isn't accidentally matched).
        _root_abs = os.path.abspath(root)
        if _root_abs == os.path.abspath(paths.LOGS_DIR) or _root_abs.startswith(_logs_abs):
            continue
        for fname in files:
            # Skip macOS resource-fork sidecars (._filename) — same extension as real audio but no audio data, breaks mutagen.
            if fname.startswith('._'):
                continue
            if fname.lower().endswith(audio_extensions):
                all_files.append(os.path.join(root, fname))

    if not all_files:
        state.console.print("[bold yellow]No audio files found in the library.[/bold yellow]")
        return {"total": 0, "flagged": 0, "clean": 0, "issues": [], "csv_path": None}

    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        transient=True,
        console=state.console,
    ) as progress:
        task = progress.add_task("Scanning...", total=len(all_files))

        for fpath in all_files:
            progress.advance(task)
            file_issues = []
            try:
                audio = mutagen.File(fpath, easy=True)
                if audio is None:
                    file_issues.append("unreadable (mutagen returned None)")
                    issues.append((fpath, file_issues))
                    continue

                # ── Genre ────────────────────────────────────────────────
                genre_val = (audio.get("genre") or audio.get("GENRE") or [""])[0]
                if not genre_val or str(genre_val).strip().lower() in ("unknown", ""):
                    file_issues.append("genre = Unknown / missing")

                # ── Year / Date ──────────────────────────────────────────
                year_val = (audio.get("date") or audio.get("year") or [""])[0]
                if not year_val or str(year_val).strip().lower() in ("unknown year", "unknown", ""):
                    file_issues.append("year = Unknown / missing")

                # ── BPM ──────────────────────────────────────────────────
                bpm_raw = (audio.get("bpm") or audio.get("BPM") or ["0"])[0]
                try:
                    bpm_val = int(str(bpm_raw).split('.')[0])
                except (ValueError, TypeError):
                    bpm_val = 0
                if bpm_val == 0:
                    file_issues.append("BPM = 0 / missing")

            except Exception:
                file_issues.append("unreadable (exception)")
                issues.append((fpath, file_issues))
                continue

            # ── Cover art (requires format-specific check) ───────────────
            try:
                art_bytes, _ = lyrics._extract_embedded_art(fpath)
                if not art_bytes:
                    file_issues.append("no cover art")
            except Exception:
                file_issues.append("cover art check failed")

            # ── Lyrics ───────────────────────────────────────────────────
            try:
                emb_lyr = lyrics._extract_embedded_lyrics(fpath)
                if not emb_lyr:
                    file_issues.append("no lyrics tag")
            except Exception:
                file_issues.append("lyrics check failed")

            if file_issues:
                issues.append((fpath, file_issues))

    # ── Summary table ──────────────────────────────────────────────────────
    total      = len(all_files)
    flagged    = len(issues)
    clean      = total - flagged

    state.console.print(f"\n[bold white]📊 Scan complete:[/bold white] "
                  f"[bold green]{clean} clean[/bold green]  "
                  f"[bold yellow]{flagged} flagged[/bold yellow]  "
                  f"(total: {total})\n")

    csv_path = None
    if issues:
        tbl = Table(
            title="[bold yellow]⚠  Flagged Library Files[/bold yellow]",
            box=box.ROUNDED,
            show_lines=True,
        )
        tbl.add_column("File", style="cyan", max_width=60, overflow="fold")
        tbl.add_column("Issues", style="yellow")

        for fpath, fissues in issues[:200]:   # cap at 200 rows for readability
            rel_path = os.path.relpath(fpath, paths.MUSIC_DIR)
            tbl.add_row(rel_path, " | ".join(fissues))

        if len(issues) > 200:
            tbl.add_row(f"… and {len(issues) - 200} more", "[dim](see CSV for full list)[/dim]")

        state.console.print(tbl)

        # Write CSV report.
        csv_path = os.path.join(paths.LOGS_DIR, "scan_report.csv")
        try:
            with open(csv_path, "w", newline="", encoding="utf-8") as _cf:
                writer = csv.writer(_cf)
                writer.writerow(["path", "issues"])
                for fpath, fissues in issues:
                    writer.writerow([fpath, " | ".join(fissues)])
            state.console.print(f"[dim]Full report saved → {csv_path}[/dim]\n")
        except Exception as _csv_err:
            state.console.print(f"[dim yellow]CSV write failed: {_csv_err}[/dim yellow]")

    if fix and issues:
        state.console.print(f"\n[bold magenta]🔧 --fix requested: re-queuing {flagged} flagged file(s)...[/bold magenta]\n")
        fix_paths  = [fp for fp, _ in issues]
        fix_stats  = pipeline.BatchStats()

        try:
            with Live(ui.generate_dashboard_table(), refresh_per_second=4) as live:
                with state.cli_lock:
                    state.active_live_ui = live
                try:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=state.CONF["MAX_WORKERS"]) as executor:
                        futures = [
                            executor.submit(
                                pipeline.process_local_file,
                                fp,
                                i % state.CONF["MAX_WORKERS"],
                                force=True,
                                stats=fix_stats,
                            )
                            for i, fp in enumerate(fix_paths)
                        ]
                        ui.run_batch(futures)
                finally:
                    with state.cli_lock:
                        state.active_live_ui = None
        except (KeyboardInterrupt, EOFError):
            state.console.print("\n[bold red]Fix interrupted.[/bold red]")

        fix_stats.print_summary()
        state.console.print("\n[bold green]Fix pass complete.[/bold green]\n")
    elif not issues:
        state.console.print("[bold green]✅ Library is clean — no issues found.[/bold green]\n")

    return {
        "total": total,
        "flagged": flagged,
        "clean": clean,
        "issues": [{"path": fp, "issues": fissues} for fp, fissues in issues],
        "csv_path": csv_path,
    }
