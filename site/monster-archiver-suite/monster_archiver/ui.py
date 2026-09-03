"""Rich console/dashboard layer: logging, the worker-status table, the
thread-safe prompt dispatcher, and run_batch()/drain_pending_prompts() — the
shared polling loop that used to be copy-pasted in run_library_scan's fix
branch, _run_retry_failed_session, _run_interactive_session, and
_run_watch_mode.
"""
import queue
import threading
import time

from rich.live import Live
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich import box

from . import config
from . import state


def log(msg, style="bold white"):
    ts = time.strftime("%H:%M:%S")
    formatted = f"[dim]{ts}[/dim] {msg}"
    # Route through the Live console so output appears permanently above the
    # panel rather than being overwritten on the next 4 Hz refresh tick.
    with state.cli_lock:
        live = state.active_live_ui
        if live and live.is_started:
            live.console.print(formatted, style=style)
        else:
            state.console.print(formatted, style=style)


def generate_dashboard_table():
    table = Table(box=box.SIMPLE_HEAD, expand=True)
    table.add_column("Worker", style="cyan", width=12)
    table.add_column("Task / File ID", style="white")
    table.add_column("Status", style="magenta")

    # Read under ui_lock — update_ui() always writes under the same lock.
    with state.ui_lock:
        snapshot = {k: dict(v) for k, v in state.worker_status.items()}

    for i in range(state.CONF["MAX_WORKERS"]):
        data = snapshot.get(i, {"id": "--", "msg": "Idle"})
        table.add_row(f"Thread-{i+1}", data["id"], data["msg"])

    return Panel(
        table,
        title=f"[bold cyan]Monster Archiver v{config.VERSION} "
              f"(Genre / Composer / Lyricist + AI Translation Engine)[/bold cyan]",
        border_style="blue",
    )


def update_ui(worker_idx, vid=None, msg=None):
    with state.ui_lock:
        if worker_idx not in state.worker_status:
            state.worker_status[worker_idx] = {"id": "--", "msg": "Idle"}
        if vid is not None:
            state.worker_status[worker_idx]["id"] = vid
        if msg is not None:
            state.worker_status[worker_idx]["msg"] = msg


def pause_ui_and_ask(prompt_msg, choices=None, default="1", table_to_print=None, force_interactive=False):
    """Thread-safe prompt dispatcher. In WATCH_MODE/NON_INTERACTIVE/dry-run,
    auto-resolves to the default (unless force_interactive=True, used for
    lyrics selection). Otherwise queues the request and blocks until the
    main-thread loop (run_batch) signals completion via a threading.Event, so
    worker threads never fight over the console.
    """
    if (state.WATCH_MODE or state.GLOBAL_DRY_RUN or state.NON_INTERACTIVE) and not force_interactive:
        # Daemon mode / dry-run / --json preview: use `is not None` since
        # default="" is valid/falsy and must be returned as-is, not fall
        # through to choices[0].
        answer = default if default is not None else (choices[0] if choices else "1")
        if state.CONF.get("DEBUG_MODE") and table_to_print:
            with state.cli_lock:
                state.console.print(table_to_print)
            _tag = "Watch" if state.WATCH_MODE else ("JSON" if state.NON_INTERACTIVE else "Dry Run")
            log(f"[{_tag}] Auto-selected '{answer}' for: {prompt_msg[:60]}", "dim")
        return answer

    answer_holder = [None]
    done_event = threading.Event()
    state._input_queue.put({
        "prompt":  prompt_msg,
        "choices": choices,
        "default": default,
        "table":   table_to_print,
        "answer":  answer_holder,
        "event":   done_event,
    })
    done_event.wait()
    return answer_holder[0]


def run_batch(futures):
    """Drive a batch of already-submitted worker futures to completion:
    drains queued prompt requests one at a time (never overlapping across
    worker threads) and refreshes the Rich dashboard, until every future is
    done. Caller must already have a Live dashboard assigned to
    state.active_live_ui (under state.cli_lock) — that assignment/teardown
    varies slightly per call site (interactive session, retry session, watch
    mode, scan --fix), so it stays with the caller; this is just the shared
    polling loop.
    """
    while any(not f.done() for f in futures):
        try:
            req = state._input_queue.get_nowait()
        except queue.Empty:
            req = None

        if req is not None:
            try:
                with state.cli_lock:
                    _snap = state.active_live_ui
                    if _snap and _snap.is_started:
                        _snap.stop()
                    print()
                    if req["table"]:
                        state.console.print(req["table"])
                    req["answer"][0] = Prompt.ask(
                        req["prompt"],
                        choices=req["choices"],
                        default=req["default"],
                    )
                    if _snap and not _snap.is_started:
                        _snap.start()
            finally:
                req["event"].set()

        with state.cli_lock:
            live = state.active_live_ui
            if live and live.is_started:
                try:
                    live.update(generate_dashboard_table())
                except Exception:
                    pass
        time.sleep(0.1)   # 100 ms poll keeps prompt wait short


def drain_pending_prompts():
    """Unblock any worker threads still waiting on a queued prompt (e.g.
    after Ctrl+C) so they can terminate cleanly instead of hanging forever
    on done_event.wait()."""
    while not state._input_queue.empty():
        try:
            req = state._input_queue.get_nowait()
            req["answer"][0] = req.get("default") or "0"
            req["event"].set()
        except Exception:
            break
