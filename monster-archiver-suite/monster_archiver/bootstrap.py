"""Dependency auto-install + ffmpeg injection.

Imported exactly once, first, by monster_archiver/__init__.py — before any
other submodule loads. That ordering is load-bearing: ensure_dependencies()
must run (and, on a fresh install, self-install everything and exit asking
the user to re-run) before any module tries to `import torch`/`librosa`/etc.,
or a missing dependency would surface as a raw ImportError instead of the
guided self-install flow.
"""
import os
import shutil
import subprocess
import sys
from glob import glob

from . import config
from . import state

BASE_DIR = config.BASE_DIR

# --- CUDA DLL CLEANUP & SAFE LOADING (Windows only) ---
if os.name == 'nt':
    import site
    # Purge conflicting DLLs from the root folder and register NVIDIA package bins
    dll_patterns = ["cublas*.dll", "cudnn*.dll", "nvblas*.dll"]
    for pattern in dll_patterns:
        for bad_dll in glob(os.path.join(BASE_DIR, pattern)):
            try:
                os.remove(bad_dll)
            except Exception:
                pass

    try:
        site_packages = getattr(site, 'getsitepackages', lambda: [])()
        if hasattr(site, 'getusersitepackages'):
            try:
                site_packages.append(site.getusersitepackages())
            except Exception:
                pass

        for sp in site_packages:
            nvidia_dir = os.path.join(sp, "nvidia")
            if os.path.isdir(nvidia_dir):
                for subdir in os.listdir(nvidia_dir):
                    bin_path = os.path.join(nvidia_dir, subdir, "bin")
                    if os.path.isdir(bin_path):
                        os.environ["PATH"] = bin_path + os.pathsep + os.environ.get("PATH", "")
                        if hasattr(os, 'add_dll_directory'):
                            try: os.add_dll_directory(bin_path)
                            except Exception: pass
    except Exception:
        pass
# ----------------------------------------

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"


def ensure_dependencies():
    # Everything below prints to stderr, not stdout — --webui-transcribe mode
    # (invoked as a subprocess by webapp/server.ts) needs stdout to stay pure
    # JSON even on a first run that triggers a fresh install. Terminal users
    # see stderr interleaved with stdout exactly as before.
    try:
        import torch
    except ImportError:
        # torch not installed yet — it will be pulled in as a transitive dependency
        # when pip installs faster-whisper below; nothing extra to do here.
        pass
    except Exception as e:
        if "WinError 127" in str(e):
            print(
                "\n[CRITICAL ERROR] PyTorch DLL mismatch (WinError 127). "
                "Visit https://pytorch.org for the correct install command for your CUDA version.",
                file=sys.stderr,
            )
            sys.exit(1)
        # Other runtime errors (e.g. driver / hardware issues) — warn, don't crash yet.
        print(f"\n[WARNING] Unexpected PyTorch import error: {e}", file=sys.stderr)

    standard_deps = {
        "rich": "rich", "mutagen": "mutagen", "requests": "requests",
        "syncedlyrics": "syncedlyrics", "deep-translator": "deep_translator",
        "pyacoustid": "acoustid", "musicbrainzngs": "musicbrainzngs",
        "pykakasi": "pykakasi", "imageio-ffmpeg": "imageio_ffmpeg", "demucs": "demucs",
        "faster-whisper": "faster_whisper", "librosa": "librosa", "numpy": "numpy",
        "watchdog": "watchdog",
    }

    missing = []
    for pip_name, import_name in standard_deps.items():
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pip_name)

    needs_dll_fix = False
    if os.name == 'nt':
        try:
            import importlib.metadata as _imeta
            _imeta.version("nvidia-cublas-cu12")
            _imeta.version("nvidia-cudnn-cu12")
        except Exception:
            # Catches PackageNotFoundError (cu12 not installed) and ImportError
            # (importlib.metadata unavailable on unusual Python builds).
            needs_dll_fix = True

    if missing or needs_dll_fix:
        print("\n📦 Auto-aligning environment and fixing dependency conflicts...", file=sys.stderr)

        if missing:
            print("Installing standard dependencies...", file=sys.stderr)
            cmd = [sys.executable, "-m", "pip", "install"] + missing
            try:
                subprocess.check_call(cmd, stdout=sys.stderr, stderr=sys.stderr)
            except subprocess.CalledProcessError:
                print("\n[CRITICAL ERROR] Pip failed to install standard dependencies.", file=sys.stderr)
                sys.exit(1)

        if needs_dll_fix:
            print("Purging old cu11 packages and downloading cu12 DLLs (Fixing Error 126)...", file=sys.stderr)
            try:
                subprocess.call([sys.executable, "-m", "pip", "uninstall", "-y", "nvidia-cublas-cu11", "nvidia-cudnn-cu11"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.check_call([
                    sys.executable, "-m", "pip", "install",
                    "nvidia-cudnn-cu12",
                    "nvidia-cublas-cu12"
                ], stdout=sys.stderr, stderr=sys.stderr)
            except subprocess.CalledProcessError:
                print("\n[CRITICAL ERROR] Failed to download NVIDIA DLLs.", file=sys.stderr)
                sys.exit(1)

        print("\n✅ Dependencies successfully aligned! Please run the script one more time.", file=sys.stderr)
        sys.exit(0)


ensure_dependencies()

# ---------------- FFMPEG INJECTION ----------------
try:
    import imageio_ffmpeg
    pip_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    local_ffmpeg = os.path.join(BASE_DIR, "ffmpeg.exe" if os.name == 'nt' else "ffmpeg")

    if not os.path.exists(local_ffmpeg):
        shutil.copy2(pip_ffmpeg, local_ffmpeg)

    _path_entries = os.environ.get("PATH", "").split(os.pathsep)
    if BASE_DIR not in _path_entries:
        os.environ["PATH"] = BASE_DIR + os.pathsep + os.environ.get("PATH", "")
except Exception as e:
    if state.CONF.get("DEBUG_MODE"):
        print(f"FFmpeg injection failed: {e}", file=sys.stderr)
