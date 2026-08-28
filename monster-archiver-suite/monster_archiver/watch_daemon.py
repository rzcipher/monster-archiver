"""Watch-folder daemon: monitors a directory for new audio files (via
watchdog) and auto-archives them unattended. Renamed from the original
_run_watch_mode to run_watch_mode (dropped the leading underscore) since it's
now called across module boundaries — sessions.py's "watch" shortcut and
cli.py's --watch flag both call into this module.
"""
import concurrent.futures
import os
import queue
import sys
import threading
import time

from rich.live import Live
from rich.prompt import Prompt

from . import pipeline
from . import state
from . import ui


def run_watch_mode(watch_path):
    """Monitor *watch_path* for new audio files and auto-process them.

    Uses watchdog's Observer so no polling is needed.  All interactive prompts
    are suppressed (WATCH_MODE=True) so the pipeline runs fully unattended.
    """
    state.WATCH_MODE = True

    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        state.console.print("[bold red]watchdog not installed — run with `pip install watchdog`[/bold red]")
        sys.exit(1)

    audio_extensions = ('.mp3', '.flac', '.m4a', '.wav', '.ogg', '.opus', '.mkv', '.webm', '.mp4')
    _watch_q = queue.Queue()

    class _AudioHandler(FileSystemEventHandler):
        # Dedup guard: abs_path→True while its stability-poll thread runs, preventing duplicate queuing from rapid/overlapping fs events.
        _enqueue_seen: dict = {}
        _enqueue_lock = threading.Lock()

        def _enqueue(self, path):
            if not path.lower().endswith(audio_extensions):
                return
            abs_path = os.path.abspath(path)

            with _AudioHandler._enqueue_lock:
                if abs_path in _AudioHandler._enqueue_seen:
                    return   # already tracking this path — skip duplicate event
                _AudioHandler._enqueue_seen[abs_path] = True

            def _wait_stable_then_queue():
                """Poll file size until it stops changing, then push to the
                processing queue. Handles partial downloads (waits for the
                download manager to finish), duplicate on_created+on_moved
                events (deduped by _enqueue_seen above), and intermediate
                format files (e.g. yt-dlp's temp .webm) that get deleted
                before stabilising — discarded silently via OSError.
                """
                prev_size = -1
                stable_count = 0
                deadline = time.time() + 300   # 5-minute ceiling for very large files
                try:
                    while time.time() < deadline:
                        try:
                            curr_size = os.path.getsize(abs_path)
                        except OSError:
                        # File deleted before stabilising — likely an intermediate format (e.g. yt-dlp's temp .webm/.m4a); discard silently.
                            return
                        if curr_size > 0 and curr_size == prev_size:
                            stable_count += 1
                            if stable_count >= 3:   # 3 × 2 s = 6 s of no growth with non-zero size
                                _watch_q.put(abs_path)
                                return
                        elif curr_size == 0:
                            # File exists but is empty — could be a partial download;
                            # keep waiting but don't count this as stable.
                            stable_count = 0
                        else:
                            stable_count = 0
                            prev_size = curr_size
                        time.sleep(2)
                    # Deadline reached: if the file still exists, queue it anyway.
                    if os.path.exists(abs_path):
                        _watch_q.put(abs_path)
                finally:
                    with _AudioHandler._enqueue_lock:
                        _AudioHandler._enqueue_seen.pop(abs_path, None)

            threading.Thread(target=_wait_stable_then_queue, daemon=True).start()

        def on_created(self, event):
            if not event.is_directory:
                self._enqueue(event.src_path)

        # Also handle moves (e.g. download managers that write to a temp name then rename).
        def on_moved(self, event):
            if not event.is_directory:
                self._enqueue(event.dest_path)

    # try/finally around the observer+executor lifecycle guarantees cleanup even if Observer.start() raises (e.g. inotify limit hit on Linux).
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=state.CONF["MAX_WORKERS"])
    observer = Observer()
    # batch_stats initialised before the try block so it's always defined even if observer.start() raises; NameError guard below should never fire.
    batch_stats = pipeline.BatchStats()
    try:
        observer.schedule(_AudioHandler(), watch_path, recursive=True)
        observer.start()

        ui.log(f"👁️  Watch mode active → {watch_path}", "bold cyan")
        ui.log("Drop audio files into the folder — they will be archived automatically. Ctrl+C to stop.", "dim")

        _slot_counter = 0
        active_futures = {}   # future → file_path

        try:
            with Live(ui.generate_dashboard_table(), refresh_per_second=4) as live:
                with state.cli_lock:
                    state.active_live_ui = live
                while True:
                    # Drain pending input requests — force_interactive=True prompts (lyrics selection) bypass WATCH_MODE auto-resolve and land here.
                    try:
                        req = state._input_queue.get_nowait()
                    except queue.Empty:
                        req = None

                    if req is not None:
                        try:
                            with state.cli_lock:
                                if live.is_started:
                                    live.stop()
                                print()
                                if req["table"]:
                                    state.console.print(req["table"])
                                req["answer"][0] = Prompt.ask(
                                    req["prompt"],
                                    choices=req["choices"],
                                    default=req["default"],
                                )
                                if not live.is_started:
                                    live.start()
                        finally:
                            req["event"].set()

                    # Drain new files from the watch queue and submit them.
                    while not _watch_q.empty():
                        try:
                            fp = _watch_q.get_nowait()
                            slot = _slot_counter % state.CONF["MAX_WORKERS"]
                            _slot_counter += 1
                            ui.log(f"📥 Queuing: {os.path.basename(fp)}", "cyan")
                            fut = executor.submit(pipeline.process_local_file, fp, slot, stats=batch_stats)
                            active_futures[fut] = fp
                        except queue.Empty:
                            break

                    # Clean up completed futures.
                    for fut in list(active_futures):
                        if fut.done():
                            del active_futures[fut]

                    # Refresh dashboard.
                    with state.cli_lock:
                        if live.is_started:
                            try:
                                live.update(ui.generate_dashboard_table())
                            except Exception:
                                pass
                    time.sleep(0.25)

        except KeyboardInterrupt:
            pass
    finally:
        # Drain pending prompt requests so worker threads blocked on done_event.wait() can unblock and exit (watch mode was the only entry point missing this).
        ui.drain_pending_prompts()
        state.WATCH_MODE = False  # Restore interactive-prompt behaviour for any caller that returns here
        try:
            observer.stop()
            observer.join(timeout=5)   # give the watchdog thread 5 s to exit cleanly; don't hang forever
        except Exception:
            pass
        executor.shutdown(wait=False)   # don't block on in-flight GPU/network tasks at Ctrl+C
        with state.cli_lock:
            state.active_live_ui = None

    # batch_stats is always defined (initialised before the try block) so this
    # NameError guard should never fire — kept only as a belt-and-braces safety net.
    try:
        batch_stats.print_summary()
    except NameError:
        pass
    state.console.print("\n[bold green]Watch session ended.[/bold green]")
