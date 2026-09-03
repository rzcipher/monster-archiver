"""Demucs vocal/instrumental stem separation.

Extracted verbatim from rezakir.py's isolate_vocals_with_demucs (originally
~lines 1747-1794); only the bare-global references were rewired onto
state.CONF / state.gpu_lock, paths.LOGS_DIR, and ui.update_ui / ui.log.
"""
import os
import sys
import subprocess
from glob import glob

import torch

from monster_archiver import state, paths, ui


def isolate_vocals_with_demucs(file_path, vid, worker_idx):
    out_dir = os.path.join(paths.LOGS_DIR, f"{vid}_demucs")
    os.makedirs(out_dir, exist_ok=True)

    # Reuse cached stems from disk (retry case) if non-empty and made by the currently configured model; model-specific sub-path avoids stale reuse after a model switch.
    _current_demucs_model = state.CONF.get("DEMUCS_MODEL", "htdemucs_ft")
    _model_out_dir = os.path.join(out_dir, _current_demucs_model)
    existing_vocals = glob(os.path.join(_model_out_dir, "**", "vocals.*"), recursive=True)
    if existing_vocals:
        _cached = existing_vocals[0]
        if os.path.getsize(_cached) > 0:
            ui.update_ui(worker_idx, msg="Demucs: Reusing cached stems...")
            return _cached, out_dir
        else:
            # Empty file from a previous crashed run — remove and redo separation.
            try:
                os.remove(_cached)
            except Exception:
                pass

    ui.update_ui(worker_idx, msg="Demucs: Isolating Vocals...")

    cmd = [
        sys.executable, "-m", "demucs.separate",
        "-n", state.CONF.get("DEMUCS_MODEL", "htdemucs_ft"),
        "--two-stems=vocals",
        "-d", "cuda" if torch.cuda.is_available() else "cpu",  # explicit device — Demucs auto-detect diverges from PyTorch on WSL2 / custom CUDA paths
        "-o", out_dir,
        file_path,
    ]

    try:
        if state.CONF.get("VRAM_SAFE_MODE", True):
            ui.update_ui(worker_idx, msg="Demucs: Waiting for GPU...")
            with state.gpu_lock:
                ui.update_ui(worker_idx, msg="Demucs: Isolating Vocals...")
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        else:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        # Search only the current model's own output folder — a recursive
        # glob over out_dir/** could pick up stems left behind by a
        # previously-configured Demucs model (e.g. htdemucs leftovers when
        # DEMUCS_MODEL is now htdemucs_ft), silently returning lower-quality
        # vocals. This mirrors the pre-run cache check above.
        vocals_path = glob(os.path.join(_model_out_dir, "**", "vocals.*"), recursive=True)
        if vocals_path:
            return vocals_path[0], out_dir
    except Exception as e:
        if state.CONF.get("DEBUG_MODE"):
            ui.log(f"Demucs Err: {e}", "red")

    return None, out_dir
