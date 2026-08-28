#!/usr/bin/env python3
import os
import sys
import subprocess

# Set environment variable to make librosa/numba fast and quiet
import tempfile
# Portable temp dir — the hardcoded "/tmp" resolved to \tmp on Windows.
os.environ.setdefault("NUMBA_CACHE_DIR", tempfile.gettempdir())


def ensure_dependencies():
    """Self-install missing packages, mirroring rezakir.py's ensure_dependencies().
    Runs before the third-party imports below so a bare Python install is enough
    to get started — status goes to stderr so it never pollutes the JSON on stdout.
    """
    standard_deps = {
        "numpy": "numpy", "librosa": "librosa", "mutagen": "mutagen",
        "requests": "requests", "soundfile": "soundfile",
    }
    missing = []
    for pip_name, import_name in standard_deps.items():
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pip_name)

    if missing:
        print(f"Installing Python dependencies: {', '.join(missing)}...", file=sys.stderr)
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "--break-system-packages"] + missing,
                stdout=sys.stderr, stderr=sys.stderr,
            )
        except subprocess.CalledProcessError as e:
            print(f"[CRITICAL ERROR] pip failed to install {missing}: {e}", file=sys.stderr)
            sys.exit(1)


ensure_dependencies()

import json
import re
import math
import base64
import numpy as np
import librosa
import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, USLT, SYLT, TCON, TCOM, TPE1, TPE2, TEXT, TBPM, TKEY, TPOS, TSRC
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm, AtomDataType
import requests

# Fallback values
_LOSSLESS_EXTENSIONS = {'flac', 'wav', 'aiff', 'alac'}


def _sanitize_json(obj):
    """Replace NaN/Infinity with None. Python's json module serializes these as
    bare NaN/Infinity tokens by default, which is invalid JSON and makes
    JSON.parse() throw on the Node side — turning one bad float into a full
    request failure (and, upstream of that, a crashed React tree)."""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_json(v) for v in obj]
    return obj


def _print_json(obj):
    print(json.dumps(_sanitize_json(obj)))

def get_audio_duration(path):
    try:
        probe = mutagen.File(path)
        if probe and probe.info:
            return float(probe.info.length)
    except Exception: pass
    try: return float(librosa.get_duration(path=path))
    except Exception: return 0.0

def _librosa_load_safe(path, sr=22050, mono=True, offset=0.0, duration=None):
    import io, soundfile as _sf
    ffmpeg_exe = "ffmpeg"
    cmd = [ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "error"]
    if offset > 0.0: cmd += ["-ss", str(offset)]
    cmd += ["-i", path]
    if duration is not None: cmd += ["-t", str(duration)]
    if mono: cmd += ["-ac", "1"]
    if sr: cmd += ["-ar", str(sr)]
    cmd += ["-f", "wav", "-"]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True)
        y, file_sr = _sf.read(io.BytesIO(res.stdout), dtype="float32")
        if y.ndim == 2 and mono: y = y.mean(axis=1)
        return y, file_sr
    except Exception:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return librosa.load(path, sr=sr, mono=mono, offset=offset, duration=duration)

def detect_lossy_upconvert_detailed(file_path):
    ext = os.path.splitext(file_path)[1].lstrip('.').lower()
    is_lossless = ext in _LOSSLESS_EXTENSIONS
    
    try:
        duration = get_audio_duration(file_path)
        offset = max(0.0, (duration - 30.0) / 2) if duration > 30 else 0.0
        
        # Load audio at 22050Hz (or native for spectrogram)
        y, sr = _librosa_load_safe(file_path, sr=22050, mono=True, offset=offset, duration=30.0)
        if len(y) == 0:
            return {"suspect": False, "reason": "Empty audio content", "spectrogram": []}

        # Calculate spectrogram. Sanitize immediately — a handful of NaN/inf samples
        # from a codec edge case propagates through mean/percentile/log into the
        # entire spectrogram, which then serializes as bare (invalid) NaN/Infinity
        # JSON tokens and breaks JSON.parse() on the Node side.
        S = np.nan_to_num(np.abs(librosa.stft(y)) ** 2, nan=0.0, posinf=0.0, neginf=0.0)
        freqs = librosa.fft_frequencies(sr=sr)
        
        # Calculate frequency power over time (decibel values)
        power_per_bin = S.mean(axis=1)
        total_power = power_per_bin.sum()
        
        # Convert to dB relative to peak
        S_db = librosa.amplitude_to_db(np.sqrt(S), ref=np.max)
        bin_db_95 = np.percentile(S_db, 95, axis=1) # 95th percentile dB level per bin

        # Downsample frequency values to 128 bins for the web UI visualizer
        chunk_size = len(bin_db_95) // 128 if len(bin_db_95) >= 128 else 1
        spec_data = []
        for i in range(128):
            start = i * chunk_size
            end = start + chunk_size
            chunk_vals = bin_db_95[start:end]
            db_val = float(np.mean(chunk_vals)) if len(chunk_vals) > 0 else -80.0
            freq_val = float(freqs[start]) if start < len(freqs) else float(i * (sr / 256.0))
            spec_data.append({"freq": int(freq_val), "db": round(db_val, 1)})

        # Cutoff check
        active_bins = freqs[bin_db_95 > -65]
        max_active_freq = active_bins[-1] if len(active_bins) > 0 else 0.0
        
        low_mask = freqs < 16000
        low_power = power_per_bin[low_mask].sum()
        ratio = float(low_power / total_power) if total_power > 0 else 1.0

        # Suspect if active frequency is low and ratio is extremely high
        suspect = is_lossless and (max_active_freq < 18500) and (ratio >= 0.999)

        return {
            "suspect": bool(suspect),
            "is_lossless": is_lossless,
            "energy_below_16k": float(ratio),
            "max_active_freq_hz": float(max_active_freq),
            "spectrogram": spec_data
        }
    except Exception as e:
        return {"suspect": False, "error": str(e), "spectrogram": []}

def _bin_axis(arr, axis, target):
    """Block-average `arr` down to `target` bins along `axis`. No-op if already <= target.
    Vectorized with np.add.reduceat rather than a per-bin Python loop + fancy-indexed
    take() — librosa.amplitude_to_db returns an F-ordered array, and gathering along
    the strided axis via take() in a loop turned a 4-minute track into a ~2 minute
    operation (cache-hostile scattered reads). reduceat does the same reduction as one
    vectorized C call regardless of memory layout, in well under a second."""
    n = arr.shape[axis]
    if n <= target:
        return arr
    arr = np.ascontiguousarray(arr)
    edges = np.linspace(0, n, target + 1).astype(np.int64)
    edges[-1] = n
    sums = np.add.reduceat(arr, edges[:-1], axis=axis)
    counts = np.maximum(np.diff(edges), 1)
    shape = [1] * arr.ndim
    shape[axis] = len(counts)
    return sums / counts.reshape(shape)

def compute_full_spectrogram(file_path, max_time_bins=640, max_freq_bins=320):
    """Full-track, full-resolution STFT spectrogram (time x frequency heatmap) plus
    the technical stats for the Audio File Information panel. Unlike
    detect_lossy_upconvert_detailed() above (a 30s snippet at 22050Hz, downsampled to
    a single 128-point averaged curve — enough for a fast lossy/lossless verdict),
    this analyzes the ENTIRE track at its native sample rate so the heatmap and the
    Nyquist/sample-rate/frame-count readout reflect the real file. The pixel grid is
    downsampled to max_time_bins x max_freq_bins for a reasonably sized JSON payload,
    but analysisFrames/fftSize/freqResolution below report the true pre-downsample
    analysis grid.
    """
    ext = os.path.splitext(file_path)[1].lstrip('.').lower()
    file_size = os.path.getsize(file_path)
    is_lossless = ext in _LOSSLESS_EXTENSIONS

    # Probe native format properties without decoding, so these reflect the source
    # file rather than our analysis (which always downmixes to mono for the STFT).
    native_sr, channels, bit_depth = None, None, None
    try:
        import soundfile as sf
        info = sf.info(file_path)
        native_sr = int(info.samplerate)
        channels = int(info.channels)
        m = re.search(r'(\d+)', info.subtype or "")
        if m: bit_depth = int(m.group(1))
    except Exception:
        pass
    if native_sr is None or channels is None:
        try:
            probe = mutagen.File(file_path)
            if probe and probe.info:
                native_sr = native_sr or (int(getattr(probe.info, "sample_rate", 0)) or None)
                channels = channels or (int(getattr(probe.info, "channels", 0)) or None)
                bit_depth = bit_depth or (int(getattr(probe.info, "bits_per_sample", 0)) or None)
        except Exception:
            pass
    native_sr = native_sr or 44100
    channels = channels or 2

    try:
        # Full-length mono load at the native sample rate, capped at 96kHz — hi-res
        # masters above that gain nothing visually (inaudible bins) but would
        # needlessly balloon STFT time, so we cap the analysis rate, not the file.
        load_sr = min(native_sr, 96000)
        y, sr = _librosa_load_safe(file_path, sr=load_sr, mono=True)
        if y is None or len(y) == 0:
            return {"error": "Empty or undecodable audio content"}

        n_fft = 4096
        hop_length = 1024
        S = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop_length))
        S = np.nan_to_num(S, nan=0.0, posinf=0.0, neginf=0.0)
        S_db = librosa.amplitude_to_db(S, ref=np.max)
        true_freq_bins, true_frames = S_db.shape

        S_small = _bin_axis(S_db, 1, max_time_bins)     # downsample time
        S_small = _bin_axis(S_small, 0, max_freq_bins)  # downsample frequency
        S_small = S_small[::-1, :]  # row 0 = highest frequency, so the browser can paint top-down

        db_floor, db_ceil = -80.0, 0.0
        quant = np.clip(S_small, db_floor, db_ceil)
        quant = ((quant - db_floor) / (db_ceil - db_floor) * 255.0).astype(np.uint8)
        image_b64 = base64.b64encode(quant.tobytes()).decode("ascii")

        total_samples = int(len(y))
        duration = float(total_samples / sr) if sr else 0.0

        return {
            "image": {
                "data": image_b64,
                "rows": int(quant.shape[0]),
                "cols": int(quant.shape[1]),
                "dbFloor": db_floor,
                "dbCeil": db_ceil,
                "maxFreqHz": float(sr / 2.0),
            },
            "fileInfo": {
                "type": ext.upper(),
                "sampleRate": native_sr,
                "bitDepth": bit_depth if is_lossless else None,
                "channels": channels,
                "duration": round(duration, 2),
                "nyquist": round(sr / 2.0 / 1000.0, 1),
                "sizeBytes": file_size,
                "samples": total_samples,
                "analysisFrames": int(true_frames),
                "fftSize": n_fft,
                "freqResolution": round(sr / n_fft, 2),
            }
        }
    except Exception as e:
        return {"error": str(e)}

def extract_audio_info(file_path):
    ext = os.path.splitext(file_path)[1].lstrip('.').lower()
    
    meta = {
        "title": os.path.splitext(os.path.basename(file_path))[0],
        "artist": "Unknown",
        "album": "Unknown Album",
        "album_artist": "Unknown",
        "year": "Unknown",
        "track": "1",
        "disc": "1",
        "genre": "Unknown",
        "composer": "Unknown",
        "isrc": "",
        "duration": 0.0,
        "bpm": 0,
        "key": "Unknown",
        "has_lyrics": False,
        "has_cover": False
    }

    try:
        meta["duration"] = get_audio_duration(file_path)
        audio = mutagen.File(file_path, easy=True)
        if audio:
            if 'title' in audio: meta["title"] = audio['title'][0]
            if 'artist' in audio: meta["artist"] = ", ".join(audio['artist'])   # ARTIST is a multi-value tag for multi-artist tracks — join all of them, don't drop everyone but the first
            if 'album' in audio: meta["album"] = audio['album'][0]
            # ALBUMARTIST — a real, distinct EasyID3/FLAC/EasyMP4 key (maps to
            # TPE2/ALBUMARTIST/aART); previously never read, so a file already
            # tagged with a compilation/album artist always looked blank here.
            if 'albumartist' in audio: meta["album_artist"] = audio['albumartist'][0]
            if 'date' in audio: meta["year"] = audio['date'][0][:4]
            if 'tracknumber' in audio:
                t = audio['tracknumber'][0]
                meta["track"] = t.split('/')[0] if '/' in t else t
            # DISCNUMBER — same "N" or "N/total" shape as tracknumber.
            if 'discnumber' in audio:
                d = audio['discnumber'][0]
                meta["disc"] = d.split('/')[0] if '/' in d else d
            if 'genre' in audio: meta["genre"] = audio['genre'][0]
            if 'composer' in audio: meta["composer"] = audio['composer'][0]
            # ISRC — a valid EasyID3/FLAC easy key, but EasyMP4 doesn't
            # register it, so M4A/AAC falls through to the raw-atom read below.
            if 'isrc' in audio: meta["isrc"] = audio['isrc'][0]

        if not meta["isrc"] and ext in ('m4a', 'aac'):
            try:
                m4_raw = MP4(file_path)
                if m4_raw.tags and '----:com.apple.iTunes:ISRC' in m4_raw.tags:
                    val = m4_raw.tags['----:com.apple.iTunes:ISRC']
                    if val:
                        meta["isrc"] = bytes(val[0]).decode("utf-8", errors="ignore")
            except Exception:
                pass
    except Exception: pass

    # Album artist fallback to artist — same convention as composer below;
    # most tracks outside compilations don't tag ALBUMARTIST separately.
    if meta["album_artist"] in ("", "Unknown") and meta["artist"] not in ("", "Unknown"):
        meta["album_artist"] = meta["artist"]

    # Composer credit is rarely tagged outside classical/soundtrack music, and
    # the online sources below (iTunes/Deezer/plain MusicBrainz search) mostly
    # don't carry it either — that's why some tracks "just work" (well-catalogued
    # releases) and others always land on "Unknown". Falling back to the artist
    # is the same convention the CLI's tag_writer.py already uses at write-time;
    # doing it here means it also shows up as the editable default in the panel.
    if meta["composer"] in ("", "Unknown") and meta["artist"] not in ("", "Unknown"):
        meta["composer"] = meta["artist"]

    # Embedded cover art check
    try:
        if ext == 'flac':
            f_aud = FLAC(file_path)
            meta["has_cover"] = bool(f_aud.pictures)
            meta["has_lyrics"] = bool(f_aud.get("LYRICS") or f_aud.get("UNSYNCEDLYRICS"))
        elif ext == 'mp3':
            m_aud = MP3(file_path, ID3=ID3)
            meta["has_cover"] = bool(m_aud.tags and m_aud.tags.getall('APIC'))
            meta["has_lyrics"] = bool(m_aud.tags and (m_aud.tags.getall('USLT') or m_aud.tags.getall('SYLT')))
        elif ext in ('m4a', 'aac'):
            m4_aud = MP4(file_path)
            if m4_aud.tags:
                meta["has_cover"] = 'covr' in m4_aud.tags
                meta["has_lyrics"] = '©lyr' in m4_aud.tags
    except Exception: pass

    return meta

def analyze_bpm_and_key(file_path):
    try:
        y, sr = _librosa_load_safe(file_path, sr=22050, mono=True)
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        bpm = int(round(float(np.atleast_1d(tempo)[0])))

        ks_major = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
        ks_minor = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
        
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        chroma_mean = chroma.mean(axis=1)

        chromatic_notes = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
        best_score = -np.inf
        best_key = "C major"

        for i, note in enumerate(chromatic_notes):
            score_major = np.corrcoef(chroma_mean, np.roll(ks_major, i))[0, 1]
            score_minor = np.corrcoef(chroma_mean, np.roll(ks_minor, i))[0, 1]
            if np.isfinite(score_major) and score_major > best_score:
                best_score = score_major
                best_key = f"{note} major"
            if np.isfinite(score_minor) and score_minor > best_score:
                best_score = score_minor
                best_key = f"{note} minor"

        return {"bpm": bpm, "key": best_key}
    except Exception:
        return {"bpm": 0, "key": "Unknown"}

def clear_artwork(filepath):
    """Strip every embedded cover picture from filepath, unconditionally.
    Always called before embed_artwork() so a track's old artwork can never
    survive alongside (or instead of) a newly chosen one — previously this
    only ran when a replacement image had already been downloaded, so a
    cleared "Album Artwork URL" field (or a failed high-res fetch) silently
    left the original cover in place."""
    ext = os.path.splitext(filepath)[1].lstrip('.').lower()
    try:
        if ext == "flac":
            audio = FLAC(filepath)
            audio.clear_pictures()
            audio.save()
        elif ext == "mp3":
            audio = MP3(filepath, ID3=ID3)
            if audio.tags is not None:
                audio.tags.delall('APIC')
                audio.save()
        elif ext in ("m4a", "aac"):
            audio = MP4(filepath)
            if audio.tags is not None and 'covr' in audio.tags:
                del audio.tags['covr']
                audio.save()
        return True
    except Exception as e:
        sys.stderr.write(f"Clear artwork failed: {e}\n")
        return False

def embed_artwork(filepath, cover_img_path, mime_type):
    """Embed cover_img_path as the sole cover picture. Assumes clear_artwork()
    already ran, so this only ever adds — it never needs to worry about
    stale pictures left behind by a previous cover."""
    ext = os.path.splitext(filepath)[1].lstrip('.').lower()
    try:
        with open(cover_img_path, "rb") as f:
            img_data = f.read()

        if ext == "flac":
            audio = FLAC(filepath)
            pic = Picture()
            pic.type = 3
            pic.mime = mime_type
            pic.data = img_data
            audio.add_picture(pic)
            audio.save()
        elif ext == "mp3":
            audio = MP3(filepath, ID3=ID3)
            if audio.tags is None: audio.add_tags()
            audio.tags.add(APIC(encoding=3, mime=mime_type, type=3, desc='Cover', data=img_data))
            audio.save()
        elif ext in ("m4a", "aac"):
            audio = MP4(filepath)
            if audio.tags is None: audio.add_tags()
            fmt = MP4Cover.FORMAT_PNG if "png" in mime_type else MP4Cover.FORMAT_JPEG
            audio.tags['covr'] = [MP4Cover(img_data, imageformat=fmt)]
            audio.save()
        return True
    except Exception as e:
        sys.stderr.write(f"Embed artwork failed: {e}\n")
        return False

def apply_bpm_key_tags(filepath, bpm, key):
    """Write BPM and musical key using format-native tags/atoms. Mutagen's
    'easy' interface (used for title/artist/album/etc.) has no BPM/key keys,
    so each format needs its own tag: FLAC's Vorbis comments, MP3's TBPM/TKEY
    ID3 frames, or M4A's tmpo/freeform atoms. Mirrors monster_archiver's CLI
    (tag_writer.py) so the webapp embeds the same tags the desktop tool does.
    """
    has_bpm = bool(bpm)
    has_key = bool(key) and str(key).strip().lower() != "unknown"
    if not has_bpm and not has_key:
        return

    ext = os.path.splitext(filepath)[1].lstrip('.').lower()
    try:
        if ext == "flac":
            audio = FLAC(filepath)
            if has_bpm: audio["BPM"] = str(bpm)
            if has_key: audio["INITIALKEY"] = str(key)
            audio.save()
        elif ext == "mp3":
            audio = MP3(filepath, ID3=ID3)
            if audio.tags is None: audio.add_tags()
            if has_bpm: audio.tags.add(TBPM(encoding=3, text=str(bpm)))
            if has_key: audio.tags.add(TKEY(encoding=3, text=str(key)))
            audio.save()
        elif ext in ("m4a", "aac"):
            audio = MP4(filepath)
            if audio.tags is None: audio.add_tags()
            if has_bpm:
                # tmpo is a native M4A integer atom (16-bit unsigned); Plex,
                # Jellyfin, and iTunes all recognise it directly.
                audio["tmpo"] = [int(bpm)]
            if has_key:
                audio["----:com.apple.iTunes:initialkey"] = [
                    MP4FreeForm(str(key).encode("utf-8"), dataformat=AtomDataType.UTF8)
                ]
            audio.save()
    except Exception as e:
        sys.stderr.write(f"BPM/Key tag write failed: {e}\n")


def apply_metadata_tags(filepath, data, lyrics_text=None, cover_path=None, cover_mime=None):
    ext = os.path.splitext(filepath)[1].lstrip('.').lower()

    # Composer fallback to artist — same convention as the CLI's tag_writer.py.
    # Covers the file even if the panel's Composer field somehow reached
    # Compile still blank/Unknown (e.g. a manually-cleared field).
    effective_composer = (data.get("composer") or "").strip()
    if not effective_composer or effective_composer == "Unknown":
        effective_composer = (data.get("artist") or "").strip()

    # Album artist fallback to artist — same convention as composer above.
    effective_album_artist = (data.get("album_artist") or "").strip()
    if not effective_album_artist or effective_album_artist == "Unknown":
        effective_album_artist = (data.get("artist") or "").strip()

    disc = str(data.get("disc") or "").strip()
    isrc = (data.get("isrc") or "").strip()

    # 1. Easy Tagging
    try:
        audio = mutagen.File(filepath, easy=True)
        if audio is not None:
            audio["title"] = data.get("title", "")
            audio["artist"] = [data.get("artist", "")]
            audio["album"] = data.get("album", "")
            audio["date"] = str(data.get("year", ""))
            audio["tracknumber"] = str(data.get("track", ""))
            audio["genre"] = data.get("genre", "")
            # EasyMP4 has no "composer" key (only "composersort"), so this
            # throws for M4A/AAC — wrapped so it can't abort the rest of this
            # block (title/artist/album/etc. below) the way it silently did
            # before; M4A/AAC composer is instead written as a native ©wrt
            # atom in step 1e below, same atom the CLI's tag_writer.py uses.
            if effective_composer and effective_composer != "Unknown":
                try:
                    audio["composer"] = [effective_composer]
                except Exception:
                    pass
            # ALBUMARTIST — valid EasyID3/FLAC/EasyMP4 key across all three formats.
            if effective_album_artist and effective_album_artist != "Unknown":
                audio["albumartist"] = [effective_album_artist]
            # DISCNUMBER — omit "1"/blank the same way tracknumber's non-mandatory
            # peers do elsewhere, so a single-disc album doesn't get a stray tag.
            if disc and disc != "1":
                audio["discnumber"] = disc
            # ISRC — valid EasyID3/FLAC key; EasyMP4 has no such key, so M4A/AAC
            # falls through to the raw-atom write below instead.
            if isrc:
                try:
                    audio["isrc"] = [isrc]
                except Exception:
                    pass
            audio.save()
    except Exception as e:
        sys.stderr.write(f"Easy tag failed: {e}\n")

    # 1b. BPM & Key — separate pass since easy-tag keys don't cover these.
    apply_bpm_key_tags(filepath, data.get("bpm"), data.get("key"))

    # 1c. ISRC on M4A/AAC — EasyMP4 doesn't register an "isrc" key, so write
    # it as the same iTunes freeform atom the CLI's tag_writer.py uses.
    if isrc and ext in ("m4a", "aac"):
        try:
            m4_audio = MP4(filepath)
            if m4_audio.tags is None:
                m4_audio.add_tags()
            m4_audio.tags["----:com.apple.iTunes:ISRC"] = [
                MP4FreeForm(isrc.encode("utf-8"), dataformat=AtomDataType.UTF8)
            ]
            m4_audio.save()
        except Exception as e:
            sys.stderr.write(f"ISRC (M4A freeform) write failed: {e}\n")

    # 1e. Composer on M4A/AAC — EasyMP4 doesn't register a "composer" key
    # (only "composersort"), so the easy-tag attempt above is caught and
    # skipped for this format. ©wrt is the actual iTunes/M4A composer atom
    # every player reads, and the same one the CLI's tag_writer.py writes.
    if effective_composer and effective_composer != "Unknown" and ext in ("m4a", "aac"):
        try:
            m4_audio = MP4(filepath)
            if m4_audio.tags is None:
                m4_audio.add_tags()
            m4_audio.tags["\xa9wrt"] = [effective_composer]
            m4_audio.save()
        except Exception as e:
            sys.stderr.write(f"Composer (M4A \xa9wrt) write failed: {e}\n")

    # 1d. ISRC on MP3 — belt-and-suspenders: EasyID3 does support "isrc" (→
    # TSRC) so 1. above should already cover it, but write the native TSRC
    # frame directly too in case an EasyID3 registration ever changes upstream.
    if isrc and ext == "mp3":
        try:
            m_audio = MP3(filepath, ID3=ID3)
            if m_audio.tags is None:
                m_audio.add_tags()
            m_audio.tags.add(TSRC(encoding=3, text=isrc))
            m_audio.save()
        except Exception as e:
            sys.stderr.write(f"ISRC (TSRC) write failed: {e}\n")

    # 2. Cover art: always wipe whatever's embedded first, then embed the new
    # (high-res) one if a download succeeded. Unconditional clearing means an
    # explicitly-removed cover actually gets removed instead of silently
    # surviving, and a failed high-res fetch never leaves a stale cover behind.
    clear_artwork(filepath)
    if cover_path and cover_mime:
        embed_artwork(filepath, cover_path, cover_mime)

    # 3. Embedding Lyrics
    if lyrics_text:
        try:
            if ext == "flac":
                audio = FLAC(filepath)
                audio["LYRICS"] = lyrics_text
                audio.save()
            elif ext == "mp3":
                audio = MP3(filepath, ID3=ID3)
                if audio.tags is None: audio.add_tags()
                # USLT plain lyric strip
                uslt_text = re.sub(r'(?m)^(?:\[\d+:\d+(?:\.\d+)?\])+', '', lyrics_text)
                uslt_text = re.sub(r'<\d+:\d+(?:\.\d+)?>', '', uslt_text)
                uslt_text = '\n'.join(ln.strip() for ln in uslt_text.splitlines()).strip()
                audio.tags.add(USLT(encoding=3, lang='eng', desc='', text=uslt_text))
                audio.save()
            elif ext in ("m4a", "aac"):
                audio = MP4(filepath)
                if audio.tags is None: audio.add_tags()
                audio["©lyr"] = [lyrics_text]
                audio.save()
        except Exception as e:
            sys.stderr.write(f"Lyrics embed failed: {e}\n")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        _print_json({"error": "Missing parameters"})
        sys.exit(1)

    cmd = sys.argv[1]

    # Startup self-check called by server.ts — no file argument needed;
    # ensure_dependencies() already ran at import time above.
    if cmd == "ensure_deps":
        _print_json({"status": "ok"})
        sys.exit(0)

    if len(sys.argv) < 3:
        _print_json({"error": "Missing parameters"})
        sys.exit(1)

    file_path = sys.argv[2]

    if not os.path.exists(file_path):
        _print_json({"error": f"File does not exist: {file_path}"})
        sys.exit(1)

    if cmd == "analyze":
        meta = extract_audio_info(file_path)
        spectral = detect_lossy_upconvert_detailed(file_path)
        bpm_key = analyze_bpm_and_key(file_path)
        spectrogram_full = compute_full_spectrogram(file_path)

        # extract_audio_info() only reads existing tags, so meta["bpm"]/["key"]
        # start at 0/"Unknown" — copy the freshly-detected values in so the
        # Track Metadata Tags panel (and whatever gets embedded on Compile)
        # matches the Audio Features Output panel instead of showing blank/Unknown.
        meta["bpm"] = bpm_key["bpm"]
        meta["key"] = bpm_key["key"]

        result = {
            "metadata": meta,
            "spectral": {
                "suspect": spectral.get("suspect"),
                "is_lossless": spectral.get("is_lossless"),
                "energy_below_16k": spectral.get("energy_below_16k"),
                "max_active_freq_hz": spectral.get("max_active_freq_hz"),
            },
            "spectrogram": spectral.get("spectrogram", []),
            "spectrogramFull": spectrogram_full if "error" not in spectrogram_full else None,
            "bpm": bpm_key["bpm"],
            "key": bpm_key["key"]
        }
        _print_json(result)

    elif cmd == "apply_tags":
        if len(sys.argv) < 4:
            _print_json({"error": "Missing tags json data"})
            sys.exit(1)
            
        try:
            tags_data = json.loads(sys.argv[3])
            lyrics_text = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4].strip() else None
            cover_path = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5].strip() else None
            cover_mime = sys.argv[6] if len(sys.argv) > 6 and sys.argv[6].strip() else "image/jpeg"
            
            apply_metadata_tags(file_path, tags_data, lyrics_text, cover_path, cover_mime)
            _print_json({"status": "success", "file_path": file_path})
        except Exception as ex:
            _print_json({"error": f"Exception raised: {ex}"})
            sys.exit(1)
