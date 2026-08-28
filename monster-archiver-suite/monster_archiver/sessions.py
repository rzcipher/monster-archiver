"""Interactive session runners: the standard drag-and-drop loop and the
--retry-failed batch recovery mode. Both submit process_local_file() calls to
a ThreadPoolExecutor and drive them with ui.run_batch()/drain_pending_prompts()
(previously copy-pasted inline in each of these — and in library_scan.py's
--fix branch and watch_daemon.py — now shared).
"""
import concurrent.futures
import os
import re
import shlex
import sys

from rich.live import Live

from . import db
from . import paths
from . import pipeline
from . import state
from . import ui
from . import watch_daemon


def _run_retry_failed_session():
    """Reprocess every FAILED file from the SQLite DB, with force=True so
    duplicate/already-processed checks are bypassed. Missing files are
    reported and skipped. Shares _run_interactive_session's event-loop
    structure so interactive prompts still work.
    """
    state.console.print("[bold cyan]🔁 Retry-Failed Mode: scanning database…[/bold cyan]\n")
    failed = db.get_failed_entries()

    if not failed:
        state.console.print("[bold yellow]No FAILED entries found in the database.[/bold yellow]")
        return

    tasks = []
    for _vid, _title, _fpath in failed:
        if _fpath and os.path.exists(_fpath):
            tasks.append(_fpath)
            state.console.print(f"  [cyan]→ Queuing:[/cyan] {os.path.basename(_fpath)}  [dim]({_title})[/dim]")
        else:
            state.console.print(
                f"  [dim red]✗ Not found:[/dim red] "
                f"{_fpath or '(no path stored)'}  [dim]— {_title}[/dim]"
            )

    if not tasks:
        state.console.print(
            "\n[bold red]No recoverable files found.[/bold red]  "
            "Files may have moved or been deleted.\n"
            "[dim]Re-drop them into the interactive session to re-index.[/dim]"
        )
        return

    state.console.print(f"\n[bold green]Retrying {len(tasks)} file(s) with force mode…[/bold green]\n")
    batch_stats = pipeline.BatchStats()

    try:
        with Live(ui.generate_dashboard_table(), refresh_per_second=4) as live:
            with state.cli_lock:
                state.active_live_ui = live
            with concurrent.futures.ThreadPoolExecutor(max_workers=state.CONF["MAX_WORKERS"]) as executor:
                futures = [
                    executor.submit(
                        pipeline.process_local_file,
                        fp,
                        i % state.CONF["MAX_WORKERS"],
                        force=True,
                        stats=batch_stats,
                    )
                    for i, fp in enumerate(tasks)
                ]
                ui.run_batch(futures)
    except (KeyboardInterrupt, EOFError):
        state.console.print("\n[bold red]Interrupt detected. Exiting…[/bold red]")
        ui.drain_pending_prompts()
        sys.exit(0)
    finally:
        with state.cli_lock:
            state.active_live_ui = None

    batch_stats.print_summary()
    state.console.print("\n[bold green]Retry session complete![/bold green]\n")


def _run_interactive_session(force_override=False):
    """Standard interactive session: user drags & drops files/folders.
    force_override=True (from --force) processes every batch with force=True
    without needing the 'force ' prefix; the prefix still works and stacks.
    """
    try:
        while True:
            print("\n")
            raw = state.console.input("[bold yellow]➡️ Drag & Drop Audio File, Folder Path, or 'q' to quit: [/bold yellow]").strip()
            if raw.lower() in ("q", "quit", "exit"):
                break

            # ── Watch-mode shortcut ────────────────────────────────────────
            # Accepts: watch | -watch | --watch | watch <path> | --watch <path>
            _watch_m = re.match(r'^(?:--?)?watch(?:\s+(.+))?$', raw, re.IGNORECASE)
            if _watch_m:
                _wp = (_watch_m.group(1) or paths.DEFAULT_WATCH_PATH).strip().strip('"\'')
                if not os.path.isdir(_wp):
                    ui.log(f"❌ Watch path not found or not a folder: {_wp}", "red")
                else:
                    state.console.print(f"\n[bold cyan]👁️  Switching to Watch Mode → {_wp}[/bold cyan]\n")
                    watch_daemon.run_watch_mode(_wp)
                    state.console.print("[bold green]↩  Returned to interactive session.[/bold green]")
                continue

            # force_override=True (--force flag) makes every batch a force-run
            # without the user needing to type the 'force ' prefix each time.
            force_mode = force_override
            if raw.lower().startswith("force "):
                force_mode = True
                raw = raw[6:].strip()
                ui.log("🔥 Force Mode Active", style="bold orange1")

            raw_clean = raw.strip()

            if raw_clean.startswith('& '):
                raw_clean = raw_clean[2:].strip()

            # shlex.split handles quoted/space-separated paths in one shot; fall back to treating the whole string as one path on parse error (unmatched quote).
            try:
                extracted_paths = shlex.split(raw_clean)
            except ValueError:
                extracted_paths = [raw_clean.strip(' "\'')]

            tasks = []
            audio_extensions = ('.mp3', '.flac', '.m4a', '.wav', '.ogg', '.opus', '.mkv', '.webm', '.mp4')

            for raw_path in extracted_paths:
                p = raw_path.strip()
                p = p.strip('\u200e').strip('\u200f')

                if not os.path.exists(p) and '\\ ' in p:
                    p = p.replace('\\ ', ' ')

                if os.path.isdir(p):
                    ui.log(f"📁 Scanning directory: {p}", "cyan")
                    for root, _, files in os.walk(p):
                        for f in files:
                            if f.lower().endswith(audio_extensions):
                                tasks.append(os.path.abspath(os.path.join(root, f)))

                elif os.path.isfile(p) and p.lower().endswith(audio_extensions):
                    tasks.append(os.path.abspath(p))

            if not tasks:
                ui.log("❌ Invalid path or unsupported file type.", "red")
                continue

            # Reset per-batch counters while keeping a fresh stats object
            batch_stats = pipeline.BatchStats()

            with Live(ui.generate_dashboard_table(), refresh_per_second=4) as live:
                with state.cli_lock:
                    state.active_live_ui = live
                try:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=state.CONF["MAX_WORKERS"]) as executor:
                        futures = []
                        for i, file_path in enumerate(tasks):
                            futures.append(executor.submit(
                                pipeline.process_local_file,
                                file_path,
                                i % state.CONF["MAX_WORKERS"],
                                force=force_mode,
                                stats=batch_stats,
                            ))

                        ui.run_batch(futures)
                finally:
                    with state.cli_lock:
                        state.active_live_ui = None

            # Print post-batch statistics summary.
            batch_stats.print_summary()
            state.console.print("\n[bold green]Tagging & Alignment Complete![/bold green]\n")

    except (KeyboardInterrupt, EOFError):
        state.console.print("\n[bold red]Interrupt detected. Exiting...[/bold red]")
        # Drain any pending prompt requests so worker threads blocked on
        # done_event.wait() can unblock and terminate cleanly.
        ui.drain_pending_prompts()
        sys.exit(0)
