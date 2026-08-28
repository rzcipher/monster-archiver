"""BPM/key analysis (Krumhansl-Schmuckler), fake-lossless/upconvert spectral
detection, and format/bitrate quality scoring for the duplicate-upgrade path.
"""
import os

import numpy as np
import librosa
import mutagen

from . import audio_io
from . import state
from . import ui

# ---------------- KEY NOTATION LOOKUP TABLES ----------------
# Converts analyze_audio_features()'s plain-text key names into KEY_NOTATION format.
# Chromatic order used throughout: C C# D Eb E F F# G Ab A Bb B (flats match Camelot/Open Key labelling).

_CAMELOT_MAP = {
    # Major keys — inner wheel (B suffix)
    "C major":  "8B",  "G major":  "9B",  "D major":  "10B", "A major":  "11B",
    "E major":  "12B", "B major":  "1B",  "F# major": "2B",  "C# major": "3B",
    "Ab major": "4B",  "Eb major": "5B",  "Bb major": "6B",  "F major":  "7B",
    # Minor keys — outer wheel (A suffix)
    "A minor":  "8A",  "E minor":  "9A",  "B minor":  "10A", "F# minor": "11A",
    "C# minor": "12A", "Ab minor": "1A",  "Eb minor": "2A",  "Bb minor": "3A",
    "F minor":  "4A",  "C minor":  "5A",  "G minor":  "6A",  "D minor":  "7A",
}

_OPENKEY_MAP = {
    # Major keys (d = dur)
    "C major":  "1d",  "G major":  "2d",  "D major":  "3d",  "A major":  "4d",
    "E major":  "5d",  "B major":  "6d",  "F# major": "7d",  "C# major": "8d",
    "Ab major": "9d",  "Eb major": "10d", "Bb major": "11d", "F major":  "12d",
    # Minor keys (m = moll)
    "A minor":  "1m",  "E minor":  "2m",  "B minor":  "3m",  "F# minor": "4m",
    "C# minor": "5m",  "Ab minor": "6m",  "Eb minor": "7m",  "Bb minor": "8m",
    "F minor":  "9m",  "C minor":  "10m", "G minor":  "11m", "D minor":  "12m",
}
# ------------------------------------------------------------


def analyze_audio_features(audio_path):
    """Analyze BPM and musical key from an audio file.
    BPM via librosa.beat.beat_track() on a 22kHz mono downmix; key via the
    Krumhansl-Schmuckler algorithm (chroma energy vs. K-S profiles, 24 keys).
    Prefers the Demucs instrumental stem when available (locks BPM better
    than vocals or full mix); falls back to the original file otherwise.
    Returns {"bpm": int, "key": str} on success or {} on failure. Key format
    follows KEY_NOTATION: "text" (F# minor), "camelot" (11A), "openkey" (4m).
    """
    try:
        # 22kHz mono is enough for beat/chroma analysis and much faster than full-quality decode; ffmpeg loader avoids soundfile's Windows MP3/AAC/OGG issues.
        y, sr = audio_io._librosa_load_safe(audio_path, sr=22050, mono=True)

        # ── BPM ──────────────────────────────────────────────────────────────
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        # librosa ≥ 0.10 may return a 1-element array; ensure we get a plain int.
        bpm = int(round(float(np.atleast_1d(tempo)[0])))

        # ── KEY (Krumhansl-Schmuckler) ── classic K-S profiles (Krumhansl 1990): perceived stability of each chromatic degree vs. the tonic.
        ks_major = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                              2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
        ks_minor = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                              2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

        # Constant-Q chroma gives better pitch accuracy on musical audio than
        # STFT chroma; sum across time to get the overall pitch-class histogram.
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        chroma_mean = chroma.mean(axis=1)   # shape: (12,) — one value per semitone

        # Correlate observed chroma against 24 rotated key profiles; np.roll aligns each template's tonic to chromatic index i (C=0, C#=1...).
        chromatic_notes = ["C", "C#", "D", "Eb", "E", "F",
                           "F#", "G", "Ab", "A", "Bb", "B"]
        best_score = -np.inf
        best_key   = "C major"

        for i, note in enumerate(chromatic_notes):
            major_profile = np.roll(ks_major, i)
            minor_profile = np.roll(ks_minor, i)

            score_major = np.corrcoef(chroma_mean, major_profile)[0, 1]
            score_minor = np.corrcoef(chroma_mean, minor_profile)[0, 1]

            # Guard against NaN from zero-variance inputs (silence, DC offset).
            if np.isfinite(score_major) and score_major > best_score:
                best_score = score_major
                best_key   = f"{note} major"
            if np.isfinite(score_minor) and score_minor > best_score:
                best_score = score_minor
                best_key   = f"{note} minor"

        # Convert to the notation requested in config.
        notation = state.CONF.get("KEY_NOTATION", "text")
        if notation == "camelot":
            key_str = _CAMELOT_MAP.get(best_key, best_key)
        elif notation == "openkey":
            key_str = _OPENKEY_MAP.get(best_key, best_key)
        else:
            key_str = best_key   # plain text: "F# minor"

        return {"bpm": bpm, "key": key_str}

    except Exception as e:
        if state.CONF.get("DEBUG_MODE"):
            ui.log(f"Audio feature analysis failed: {e}", "red")
        return {}


# ── Feature 2: Fake-FLAC / lossy-upconvert detection ──
_LOSSLESS_EXTENSIONS = frozenset({'flac', 'wav', 'aiff', 'aif', 'alac'})

def detect_lossy_upconvert(file_path):
    """Check whether a 'lossless' file is actually a lossy upconvert.
    Loads a 30s centre-sample, computes the STFT power spectrum, and checks
    if >= UPCONVERT_ENERGY_THRESHOLD of energy sits below 16kHz (a spectral
    ceiling consistent with lossy encoding). Returns
    {"suspect": bool, "energy_below_16k": float, "threshold_used": float}
    on success or None on failure. Only called for _LOSSLESS_EXTENSIONS files.
    """
    ext = os.path.splitext(file_path)[1].lstrip('.').lower()
    if ext not in _LOSSLESS_EXTENSIONS:
        return None

    threshold = float(state.CONF.get("UPCONVERT_ENERGY_THRESHOLD", 0.95))

    try:
        # Load 30 s from the middle of the track — intros are often silence-padded
        # and could skew the spectral measurement.
        probe_duration = audio_io.get_audio_duration(file_path)
        offset = max(0.0, (probe_duration - 30.0) / 2) if probe_duration > 30 else 0.0

        # Use the ffmpeg-based safe loader — avoids the PySoundFile/audioread
        # warning chain on Windows for lossy source files (MP3, AAC, OGG).
        y, sr = audio_io._librosa_load_safe(file_path, sr=None, mono=True, offset=offset, duration=30.0)
        if len(y) == 0 or len(y) < sr:   # guard: less than 1 second of audio (librosa always returns ndarray, never None)
            return None

        # Full-bandwidth power spectrum via STFT.
        S = np.abs(librosa.stft(y)) ** 2
        freqs = librosa.fft_frequencies(sr=sr)

        # Total energy across the analysed window.
        power_per_bin = S.mean(axis=1)   # average power per frequency bin
        total_power   = power_per_bin.sum()
        if total_power <= 0:
            return None

        # Energy contained in bins below 16 kHz.
        low_mask  = freqs < 16000
        low_power = power_per_bin[low_mask].sum()
        ratio     = float(low_power / total_power)

        return {
            "suspect":           ratio >= threshold,
            "energy_below_16k":  ratio,
            "threshold_used":    threshold,
        }
    except Exception as e:
        if state.CONF.get("DEBUG_MODE"):
            ui.log(f"Transcode detection error: {e}", "dim")
        return None


# ── Feature 3: audio quality scoring (duplicate quality-upgrade path) ──
# Format rank: higher = better; used for comparison when bitrate is equal/unavailable (FLAC has no bitrate tag).
_FORMAT_RANK = {'flac': 5, 'wav': 5, 'aiff': 5, 'aif': 5, 'alac': 5,
                'm4a': 3, 'aac': 3, 'ogg': 3, 'opus': 3,
                'mp3': 2, 'wma': 2,
                'mp4': 1, 'webm': 1, 'mkv': 1}

def get_audio_quality_score(file_path):
    """Return a (format_rank, bitrate_kbps) tuple for quality comparison (higher
    is better). Lossless formats get max rank (5) and bitrate 9999 so they
    always outrank lossy formats. Returns (0, 0) on failure.
    """
    try:
        ext = os.path.splitext(file_path)[1].lstrip('.').lower()
        rank = _FORMAT_RANK.get(ext, 0)

        if rank == 5:
            # Lossless — bitrate is meaningless; assign a sentinel value
            # that is higher than any real lossy bitrate.
            return (rank, 9999)

        probe = mutagen.File(file_path)
        bitrate = 0
        if probe and probe.info:
            bitrate = int(getattr(probe.info, 'bitrate', 0) or 0) // 1000   # bps → kbps
        return (rank, bitrate)
    except Exception:
        return (0, 0)
