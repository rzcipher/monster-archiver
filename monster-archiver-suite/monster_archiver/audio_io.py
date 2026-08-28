"""Audio-loading helpers shared by the Whisper transcription pipeline and the
audio-features/BPM-key analysis: a Windows-safe librosa.load wrapper and a
mutagen/librosa duration lookup.
"""
import subprocess

import librosa
import mutagen


def get_audio_duration(path):
    """Return audio duration in seconds via mutagen then librosa fallback, or 0.0."""
    try:
        probe = mutagen.File(path)
        if probe and probe.info:
            dur = float(probe.info.length)
            if dur > 0:
                return dur
    except Exception:
        pass
    try:
        # librosa 0.9+ uses a `path` kwarg (no full array in RAM); older builds use `filename` instead — fall back on TypeError.
        try:
            return float(librosa.get_duration(path=path))
        except TypeError:
            # librosa < 0.9 used 'filename=' instead of 'path='.
            return float(librosa.get_duration(filename=path))
    except Exception:
        return 0.0

def _librosa_load_safe(path, sr=22050, mono=True, offset=0.0, duration=None):
    """Drop-in replacement for librosa.load that avoids the PySoundFile/audioread
    warning chain on Windows (soundfile can't decode MP3/AAC/OGG there natively).
    Pipes audio through ffmpeg to a temp WAV file first; falls back to plain
    librosa.load if ffmpeg is unavailable or fails.
    """
    import io, soundfile as _sf

    ffmpeg_exe = "ffmpeg"  # already on PATH via imageio-ffmpeg injection

    # ffmpeg decode → signed 16-bit PCM WAV on stdout. -ss must precede -i for fast input seek (after -i forces a slow full decode-and-discard).
    cmd = [
        ffmpeg_exe,
        "-y",                          # overwrite without prompting
        "-hide_banner", "-loglevel", "error",
    ]
    if offset and offset > 0.0:
        cmd += ["-ss", str(offset)]    # input seek — must precede -i
    cmd += ["-i", path]
    if duration is not None:
        cmd += ["-t", str(duration)]
    if mono:
        cmd += ["-ac", "1"]
    if sr:
        cmd += ["-ar", str(sr)]
    cmd += ["-f", "wav", "-"]          # write WAV to stdout

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        buf = io.BytesIO(result.stdout)
        y, file_sr = _sf.read(buf, dtype="float32", always_2d=False)
        if y.ndim == 2 and mono:
            y = y.mean(axis=1)
        return y, file_sr
    except Exception:
        # ffmpeg unavailable or failed — fall back to librosa's own loader.
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return librosa.load(path, sr=sr, mono=mono, offset=offset,
                                duration=duration)
