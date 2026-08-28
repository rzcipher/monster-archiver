"""Video caption pipeline: pull the audio out of a video file, transcribe it
with the existing Faster-Whisper pipeline (whisper_transcribe.transcribe_audio,
completely unmodified aside from the skip_demucs bypass), diarize speakers
with pyannote.audio, merge the two into per-speaker-labelled caption
segments, render them as an ASS subtitle track with one colour-coded Style
per speaker, and burn that track into the video with ffmpeg (libass +
libfribidi — already in the bundled binary, see bootstrap.py's ffmpeg
injection, confirmed to handle Persian/RTL shaping correctly).

pyannote.audio is deliberately NOT added to bootstrap.py's eager
standard_deps install list. It pulls in its own separate dependency tree
(pytorch-lightning, pyannote.database, etc.) that only this feature needs;
every other command (plain tagging, --scan, --webui-transcribe, ...)
shouldn't pay that install cost or inherit any version-conflict risk from
it, especially given how much tuning bootstrap.py already does to keep the
torch/faster-whisper/demucs stack stable. Instead it's installed on first
use, in _load_diarization_pipeline() below, mirroring bootstrap.py's own
"pip install, then ask the user to re-run" flow but scoped to just this path.

Two-phase design, mirroring the existing "AI: transcribe -> web UI review ->
write" flow the Lyrics Studio already uses:
  1. caption_video()        — extract + transcribe + diarize + merge. Returns
                               the segments for the web UI to display and let
                               the user fix (text and/or speaker labels)
                               before anything is burned in.
  2. burn_video_subtitles()  — takes the *corrected* segments back from the
                               UI, builds the ASS file, and burns it in.
Nothing is burned until step 2 runs on reviewed data.
"""
import os
import re
import subprocess
import sys
import tempfile

from monster_archiver import state, paths, ui


# ---------------------------------------------------------------------------
# 1. Audio extraction
# ---------------------------------------------------------------------------

def extract_audio_track(video_path, vid):
    """Pull the audio track out of *video_path* into a 16kHz mono WAV under
    LOGS_DIR (same temp-artifact convention as demucs_stems' out_dir).
    Returns the WAV path, or None if the video has no usable audio stream.
    """
    os.makedirs(paths.LOGS_DIR, exist_ok=True)
    out_path = os.path.join(paths.LOGS_DIR, f"{vid}_audio.wav")

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", video_path,
        "-vn",              # drop video stream — audio only
        "-ac", "1",         # mono — what Whisper/pyannote both expect
        "-ar", "16000",
        "-f", "wav",
        out_path,
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return out_path
    except subprocess.CalledProcessError as e:
        if state.CONF.get("DEBUG_MODE"):
            _err = e.stderr.decode(errors="replace")[:300] if e.stderr else str(e)
            ui.log(f"Audio extraction failed: {_err}", "red")
    return None


# ---------------------------------------------------------------------------
# 2. LRC -> segments (re-parses transcribe_audio's own output instead of
#    threading a new return value through that function, so its already
#    heavily-tuned contract stays untouched for every other caller)
# ---------------------------------------------------------------------------

_LRC_LINE_RE = re.compile(r'^\[(\d+):(\d+(?:\.\d+)?)\](.*)$')
_ENHANCED_TAG_RE = re.compile(r'<\d+:\d+(?:\.\d+)?>')


def _parse_lrc_segments(lrc_path, audio_duration=None):
    """Turn the plain per-line LRC transcribe_audio() writes into a list of
    {"start": float, "end": float, "text": str} dicts. `end` isn't in the
    LRC format itself, so it's derived: each line ends where the next one
    starts (minus a hair, so adjacent captions don't visually overlap), and
    the last line gets a capped fallback bounded by the real audio length.
    """
    segments = []
    with open(lrc_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            m = _LRC_LINE_RE.match(line)
            if not m:
                continue
            mins, secs, text = m.groups()
            start = int(mins) * 60 + float(secs)
            text = _ENHANCED_TAG_RE.sub("", text).strip()
            if not text:
                continue
            segments.append({"start": start, "text": text})

    for i, seg in enumerate(segments):
        if i + 1 < len(segments):
            seg["end"] = max(seg["start"] + 0.5, segments[i + 1]["start"] - 0.05)
        else:
            fallback_end = seg["start"] + 4.0
            if audio_duration:
                fallback_end = min(fallback_end, audio_duration)
            seg["end"] = max(seg["start"] + 0.5, fallback_end)

    return segments


# ---------------------------------------------------------------------------
# 3. Speaker diarization (pyannote.audio)
# ---------------------------------------------------------------------------

def _load_diarization_pipeline():
    if state.DIARIZATION_PIPELINE is not None:
        return state.DIARIZATION_PIPELINE

    with state.ai_lock:
        if state.DIARIZATION_PIPELINE is None:
            # pyannote.audio 4.x is a breaking rewrite versus the 3.x this
            # module was written against: the auth kwarg was renamed
            # (use_auth_token -> token), pipeline(...) now returns a
            # DiarizeOutput wrapper instead of a bare Annotation (so the
            # diarization.itertracks(yield_label=True) call below would
            # need output.speaker_diarization.itertracks(...) instead), and
            # it additionally downloads a second gated model
            # (pyannote/speaker-diarization-community-1) that nothing in
            # this codebase's setup instructions ever asks the user to
            # accept. Rather than chase that moving target, pin to the
            # last 3.x release (3.4.0 — the one that already fixed
            # num_speakers support for this exact -3.1 pipeline) and
            # actively downgrade if something newer is already installed.
            _PINNED_SPEC = "pyannote.audio==3.4.0"

            def _install_pinned():
                state.console.print(
                    f"\n[bold yellow]📦 Installing {_PINNED_SPEC} for speaker "
                    "diarization...[/bold yellow]"
                )
                try:
                    subprocess.check_call(
                        [sys.executable, "-m", "pip", "install", _PINNED_SPEC],
                        stdout=sys.stderr, stderr=sys.stderr,
                    )
                except subprocess.CalledProcessError as e:
                    raise RuntimeError(
                        "Failed to install pyannote.audio automatically — install it "
                        f"manually with: pip install {_PINNED_SPEC}"
                    ) from e

            import importlib.metadata as _ilm
            try:
                _installed = _ilm.version("pyannote.audio")
            except _ilm.PackageNotFoundError:
                _installed = None

            if _installed is None:
                _install_pinned()
            elif _installed.split(".")[0] != "3":
                state.console.print(
                    f"[bold yellow]📦 pyannote.audio {_installed} detected — "
                    f"downgrading to the version this feature was built "
                    "against...[/bold yellow]"
                )
                _install_pinned()

            # pyannote.audio 3.4.0 (pinned above) still targets torchaudio's
            # pre-2.9 API surface: pyannote/audio/core/io.py has a
            # module-level `-> torchaudio.AudioMetaData:` return-type
            # annotation that Python evaluates immediately on import (it's
            # not a string / deferred annotation), plus a
            # `torchaudio.list_audio_backends()` call reached whenever
            # pyannote probes a file's duration. Both symbols were dropped
            # from torchaudio starting with 2.9 as part of the same
            # torchcodec migration mentioned above, so a bare
            # `from pyannote.audio import Pipeline` raises
            # `AttributeError: module 'torchaudio' has no attribute
            # 'AudioMetaData'` before any of this app's own code even runs,
            # whenever torch/demucs/etc. already pulled in torchaudio >= 2.9.
            # Confirmed unfixed upstream on the 3.x branch — 4.x drops
            # torchaudio for torchcodec entirely, which is exactly the
            # rewrite this pin is avoiding (see
            # github.com/pyannote/pyannote-audio/issues/1952, closed
            # wontfix). Neither symbol is actually needed at runtime here —
            # AudioMetaData is only a type hint, and list_audio_backends is
            # a backend-listing helper — so shim them back onto the
            # torchaudio module right before import instead of pinning
            # torchaudio itself, which would risk fighting the
            # torch/demucs/faster-whisper stack's own transitive version.
            import torchaudio as _torchaudio

            if not hasattr(_torchaudio, "AudioMetaData"):
                from collections import namedtuple as _namedtuple
                _torchaudio.AudioMetaData = _namedtuple(
                    "AudioMetaData",
                    ["sample_rate", "num_frames", "num_channels",
                     "bits_per_sample", "encoding"],
                )
            if not hasattr(_torchaudio, "list_audio_backends"):
                # NOTE: must NOT be an empty list — pyannote/audio/core/io.py
                # does `backend = "soundfile" if "soundfile" in backends else
                # backends[0]`, so an empty list trades the AttributeError for
                # an IndexError ("list index out of range") on the very next
                # line. "soundfile" is a real, already-installed dependency
                # here (see audio_io.py), so report it truthfully rather than
                # a placeholder name pyannote would then fail to actually use.
                _torchaudio.list_audio_backends = lambda: ["soundfile"]

            # Third instance of the same underlying problem: pyannote.audio
            # 3.4.0's own from_pretrained()/download plumbing was written
            # against huggingface_hub's old convention and calls
            # hf_hub_download(..., use_auth_token=...) internally (see the
            # token/use_auth_token fallback a few lines below — that's the
            # exact call that reaches it). huggingface_hub 1.0 finished the
            # multi-release deprecation cycle and removed use_auth_token from
            # every method outright (token= is now the only accepted name),
            # so an unpinned huggingface_hub >= 1.0 turns that call into
            # `TypeError: hf_hub_download() got an unexpected keyword
            # argument 'use_auth_token'`. Same fix shape as the two shims
            # above: patch the function to translate the old kwarg rather
            # than pinning huggingface_hub, which faster-whisper/demucs also
            # depend on transitively. Patched in both places pyannote might
            # import it from (the top-level re-export and the submodule it's
            # actually defined in) since it isn't worth chasing which one
            # this particular pyannote release uses internally.
            import huggingface_hub as _huggingface_hub
            from huggingface_hub import file_download as _hf_file_download

            _orig_hf_hub_download = _huggingface_hub.hf_hub_download

            def _hf_hub_download_compat(*_args, **_kwargs):
                if "use_auth_token" in _kwargs:
                    _kwargs.setdefault("token", _kwargs.pop("use_auth_token"))
                return _orig_hf_hub_download(*_args, **_kwargs)

            _huggingface_hub.hf_hub_download = _hf_hub_download_compat
            _hf_file_download.hf_hub_download = _hf_hub_download_compat

            from pyannote.audio import Pipeline

            hf_token = state.CONF.get("HUGGINGFACE_TOKEN", "")
            if not hf_token:
                raise RuntimeError(
                    "Speaker diarization needs a Hugging Face access token. Create a "
                    "free account at huggingface.co, accept the terms on "
                    "huggingface.co/pyannote/speaker-diarization-3.1 and "
                    "huggingface.co/pyannote/segmentation-3.0, then create a token at "
                    "huggingface.co/settings/tokens and set HUGGINGFACE_TOKEN in "
                    "config.json."
                )

            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
            state.console.print("[bold yellow]📡 Loading speaker diarization model...[/bold yellow]")

            # Fourth instance of the same version-skew problem this function
            # keeps working around: PyTorch 2.6 flipped torch.load's
            # weights_only default from False to True, and pyannote.audio
            # 3.4.0's own checkpoint loading predates that flip, so
            # from_pretrained() now runs straight into the new unpickler's
            # allowlist. Confirmed on pyannote's own issue tracker
            # (github.com/pyannote/pyannote-audio/issues/1908) that this is
            # NOT a single-class fix: depending on load order it rejects
            # torch.torch_version.TorchVersion, then omegaconf.listconfig.
            # ListConfig, then pyannote.audio.core.task.Specifications, ...
            # — allowlisting one global via add_safe_globals just surfaces
            # the next one.
            #
            # The call doesn't reach torch.load directly either: pyannote's
            # Model.from_pretrained() goes through pytorch_lightning's
            # checkpoint loader, which bottoms out in lightning_fabric.
            # utilities.cloud_io._load(path, map_location, weights_only=None)
            # — and THAT function unconditionally forwards
            # `weights_only=weights_only` to torch.load, i.e. torch.load
            # always receives an explicit `weights_only=None` keyword, never
            # an absent one. So a plain `kwargs.setdefault("weights_only",
            # False)` is a no-op here — the key is already present, just
            # None-valued — which is why an earlier version of this patch
            # using setdefault silently did nothing. Forcing the key instead
            # of defaulting it is required. Scoping this to exactly this
            # torch.load call (rather than the global torch.load default)
            # sidesteps all the allowlist whack-a-mole at once; the
            # checkpoint is coming from the official gated pyannote HF repo,
            # downloaded under the user's own HUGGINGFACE_TOKEN above, not an
            # arbitrary file, so this is the same "trusted source" case the
            # error message itself carves out.
            _orig_torch_load = torch.load

            def _torch_load_trusted(*_args, **_kwargs):
                _kwargs["weights_only"] = False
                return _orig_torch_load(*_args, **_kwargs)

            torch.load = _torch_load_trusted
            try:
                # pyannote.audio 3.4.0 (pinned above) still takes
                # use_auth_token; newer releases renamed it to token. Try
                # the new name first and fall back to the old one anyway —
                # cheap insurance if this ever ends up running against a
                # different installed version.
                try:
                    pipeline = Pipeline.from_pretrained(
                        "pyannote/speaker-diarization-3.1", token=hf_token,
                    )
                except TypeError:
                    pipeline = Pipeline.from_pretrained(
                        "pyannote/speaker-diarization-3.1", use_auth_token=hf_token,
                    )
            finally:
                torch.load = _orig_torch_load
            if pipeline is None:
                raise RuntimeError(
                    "pyannote.audio returned no pipeline — usually means the "
                    "HUGGINGFACE_TOKEN is invalid or the gated model terms haven't "
                    "been accepted yet on huggingface.co."
                )
            pipeline.to(torch_device(device))
            state.DIARIZATION_PIPELINE = pipeline
            state.console.print("[bold green]✅ Diarization model ready[/bold green]")

    return state.DIARIZATION_PIPELINE


def torch_device(device_str):
    import torch
    return torch.device(device_str)


def diarize_audio(audio_path, worker_idx=0):
    """Run the pyannote pipeline and return a flat, time-sorted list of
    (start, end, speaker_label) turns.
    """
    pipeline = _load_diarization_pipeline()

    ui.update_ui(worker_idx, msg="Diarizing: Waiting for GPU...")
    if not state.gpu_lock.acquire(timeout=600):
        raise TimeoutError("gpu_lock wait exceeded 10 min during diarization")
    try:
        ui.update_ui(worker_idx, msg="Diarizing: Identifying speakers...")
        # Handing pyannote a bare file path makes it load the audio itself
        # via torchaudio, which (2.9+) refuses to decode anything unless the
        # separate torchcodec package is installed too — and torchcodec is
        # pinned tightly to a specific torch minor version, so an unpinned
        # install can easily grab a build that doesn't match whatever torch
        # this environment already has. Sidestep that whole chain: read the
        # WAV ourselves with the existing soundfile/ffmpeg-based loader
        # (already used for BPM/key analysis, no torchaudio involved) and
        # hand pyannote the pre-loaded {"waveform", "sample_rate"} dict it
        # explicitly supports as an alternative to a path.
        import torch
        from monster_archiver import audio_io
        samples, sample_rate = audio_io._librosa_load_safe(audio_path, sr=16000, mono=True)
        waveform = torch.from_numpy(samples).float().unsqueeze(0)  # (channel, time)
        diarization = pipeline({"waveform": waveform, "sample_rate": sample_rate})
    finally:
        state.gpu_lock.release()

    turns = [
        (turn.start, turn.end, speaker)
        for turn, _, speaker in diarization.itertracks(yield_label=True)
    ]
    turns.sort(key=lambda t: t[0])
    return turns


def assign_speakers(segments, turns):
    """Label each {start, end, text} segment with whichever diarization turn
    it overlaps most. A segment landing entirely inside a diarization gap
    (no overlap with any turn) falls back to the nearest turn by edge
    distance. pyannote's own SPEAKER_00-style ids carry no meaningful order,
    so they're remapped to "Speaker 1", "Speaker 2"... in order of first
    appearance in the transcript.
    """
    if not turns:
        for seg in segments:
            seg["speaker"] = "Speaker 1"
        return segments

    raw_to_display = {}
    next_num = 1

    for seg in segments:
        best_label, best_overlap = None, 0.0
        for t_start, t_end, label in turns:
            overlap = min(seg["end"], t_end) - max(seg["start"], t_start)
            if overlap > best_overlap:
                best_overlap, best_label = overlap, label

        if best_label is None:
            mid = (seg["start"] + seg["end"]) / 2

            def _edge_distance(turn):
                t_start, t_end, _ = turn
                if t_start <= mid <= t_end:
                    return 0.0
                return min(abs(mid - t_start), abs(mid - t_end))

            best_label = min(turns, key=_edge_distance)[2]

        if best_label not in raw_to_display:
            raw_to_display[best_label] = f"Speaker {next_num}"
            next_num += 1
        seg["speaker"] = raw_to_display[best_label]

    return segments


# ---------------------------------------------------------------------------
# 4. ASS subtitle rendering (one colour-coded Style per speaker)
# ---------------------------------------------------------------------------

# RGB hex; cycles with modulo if a video has more speakers than colours.
# Chosen for legibility burned into arbitrary video backgrounds with a black
# outline — high saturation, spread across hues so adjacent speakers are
# never confusable even for colourblind viewers relying on hue alone plus
# the on-screen speaker order.
SPEAKER_PALETTE = [
    "FFD400",  # amber
    "00E5FF",  # cyan
    "FF4FA3",  # pink
    "8CFF6B",  # green
    "FF6B4A",  # orange-red
    "B48CFF",  # violet
    "FFFFFF",  # white
    "6BD9FF",  # light blue
]


def _ass_color(hex_rgb, alpha="00"):
    """RRGGBB -> ASS's &HAABBGGRR& notation (ASS is BGR, not RGB)."""
    r, g, b = hex_rgb[0:2], hex_rgb[2:4], hex_rgb[4:6]
    return f"&H{alpha}{b}{g}{r}&".upper()


def _ass_timestamp(seconds):
    """seconds -> ASS's H:MM:SS.cc (centiseconds) timestamp format."""
    seconds = max(0.0, seconds)
    total_cs = round(seconds * 100)
    cs, total_s = total_cs % 100, total_cs // 100
    s, total_m = total_s % 60, total_s // 60
    m, h = total_m % 60, total_m // 60
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape_text(text):
    # Curly braces open an ASS override block — caption text should never
    # trigger one accidentally. Literal newlines become ASS's line-break tag.
    return text.replace("{", "(").replace("}", ")").replace("\r", "").replace("\n", "\\N")


def build_ass(segments, out_path, font_name="Tahoma", font_size=40):
    """Render {start, end, text, speaker} segments as an ASS file with one
    Style per speaker, ready for ffmpeg's `ass` filter. PlayResX/Y are a
    nominal 1280x720 — the `ass` filter scales to the video's actual frame
    size automatically, so the real resolution never needs probing (the
    bundled ffmpeg has no ffprobe binary to probe it with anyway).

    font_size is in that same 1280x720 script-coordinate space, same as
    everything else in a Style line. Default used to be a hardcoded 52,
    which reads as noticeably oversized once a 3-line wrapped caption is
    actually burned in (~7.2% of frame height *per line*); 40 is a more
    typical burned-subtitle size while staying easily readable.
    """
    font_size = max(16, min(96, int(font_size))) if font_size else 40

    speakers = []
    for seg in segments:
        if seg["speaker"] not in speakers:
            speakers.append(seg["speaker"])
    style_names = {sp: f"Speaker{i}" for i, sp in enumerate(speakers)}

    lines = [
        "[Script Info]",
        "Title: Monster Archiver Auto Captions",
        "ScriptType: v4.00+",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "PlayResX: 1280",
        "PlayResY: 720",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
    ]

    outline = _ass_color("000000")
    shadow  = _ass_color("000000", alpha="80")
    for i, sp in enumerate(speakers):
        colour = _ass_color(SPEAKER_PALETTE[i % len(SPEAKER_PALETTE)])
        lines.append(
            f"Style: {style_names[sp]},{font_name},{font_size},{colour},{colour},"
            f"{outline},{shadow},-1,0,0,0,100,100,0,0,1,3,1,2,60,60,40,1"
        )

    lines += [
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for seg in segments:
        style = style_names[seg["speaker"]]
        text = _ass_escape_text(seg["text"])
        lines.append(
            f"Dialogue: 0,{_ass_timestamp(seg['start'])},{_ass_timestamp(seg['end'])},"
            f"{style},,0,0,0,,{text}"
        )

    content = "\n".join(lines) + "\n"

    # Atomic write — same tempfile+os.replace pattern as the .lrc write in
    # whisper_transcribe.py. utf-8-sig (BOM) so libass unambiguously detects
    # UTF-8 rather than falling back to a system codepage, which matters for
    # RTL scripts like Persian; libass explicitly strips a leading BOM.
    _dir = os.path.dirname(out_path)
    fd, tmp = tempfile.mkstemp(dir=_dir, suffix=".ass.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig") as f:
            f.write(content)
        os.replace(tmp, out_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    return out_path


# ---------------------------------------------------------------------------
# 5. Burning (ffmpeg, libass)
# ---------------------------------------------------------------------------

_NVENC_AVAILABLE = None


def _has_nvenc():
    global _NVENC_AVAILABLE
    if _NVENC_AVAILABLE is None:
        try:
            result = subprocess.run(
                ["ffmpeg", "-hide_banner", "-encoders"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=15,
            )
            _NVENC_AVAILABLE = "h264_nvenc" in result.stdout
        except Exception:
            _NVENC_AVAILABLE = False
    return _NVENC_AVAILABLE


def _escape_ffmpeg_filter_path(path):
    r"""ffmpeg's filtergraph parser treats ':' as an option separator and '\\'
    as an escape char, which breaks the `ass=` filter argument on Windows
    paths like 'C:\\Users\\...\\subs.ass' unless escaped. Forward slashes are
    accepted by both ffmpeg and Windows itself, so swapping them in sidesteps
    the backslash half of the problem entirely.

    The drive-letter colon needs escaping too, but a single backslash is NOT
    enough — that was the actual bug behind the "Unable to parse option
    value ... as image size" / "Error applying option 'original_size'"
    failure. `-vf` strings go through two nested parsers: the outer
    filtergraph parser (which splits filters on `,`/`;` and consumes one
    layer of backslash-escaping) and then av_opt's own `:`-separated
    option-list parser for the filter's arguments. A single `\:` gets eaten
    by the outer layer before the inner one ever sees it, so the inner
    parser still splits on that bare colon — "C" becomes the filename
    (positional arg 0) and the rest of the path gets shoved into arg 1,
    which for the `ass` filter is `original_size` (an image-size option),
    hence it failing to parse a filesystem path as WxH. Verified by
    reproducing the exact reported error with one backslash and confirming
    it disappears with two: `\\:` survives the outer unescape as `\:`, which
    the inner parser then correctly reads as a literal colon.
    """
    p = path.replace("\\", "/")
    p = p.replace(":", r"\\:")
    return p


def burn_subtitles(video_path, ass_path, out_path, worker_idx=0):
    """Burn *ass_path* into *video_path*'s pixels via ffmpeg's libass-backed
    `ass` filter. Tries NVENC first (fast on an RTX-class GPU), falls back to
    libx264, and retries once with re-encoded AAC audio if the source audio
    codec can't be stream-copied into the output container.
    """
    ui.update_ui(worker_idx, msg="Burning subtitles...")
    filt = f"ass={_escape_ffmpeg_filter_path(ass_path)}"

    video_codec = (
        ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "19"]
        if _has_nvenc()
        else ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]
    )
    base_cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", video_path, "-vf", filt, *video_codec,
    ]

    last_err = None
    for audio_args in (["-c:a", "copy"], ["-c:a", "aac", "-b:a", "192k"]):
        cmd = base_cmd + audio_args + [out_path]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                return out_path
        except subprocess.CalledProcessError as e:
            last_err = e.stderr.decode(errors="replace")[-500:] if e.stderr else str(e)
            if state.CONF.get("DEBUG_MODE"):
                ui.log(f"Burn attempt failed ({audio_args}): {last_err}", "yellow")
            continue

    ui.update_ui(worker_idx, msg="[bold red]Subtitle burn failed[/bold red]")
    if state.CONF.get("DEBUG_MODE") and last_err:
        ui.log(f"ffmpeg burn error: {last_err}", "red")

    # Raise instead of returning None: the webui bridge (execRezakirJson in
    # pythonBridge.ts) only ever surfaces cli.py's own JSON {"error": ...}
    # string to the browser — it captures this process's stderr but never
    # forwards it anywhere, so the DEBUG_MODE ui.log() calls above are
    # invisible from the webapp no matter what DEBUG_MODE is set to. Putting
    # the real ffmpeg tail directly into the exception message is the only
    # way it actually reaches the "Burn Captions" error toast, since cli.py's
    # `except Exception as e: print(json.dumps({"error": str(e)}))` picks up
    # whatever this raises verbatim.
    detail = last_err.strip() if last_err else "no ffmpeg output was captured"
    raise RuntimeError(f"ffmpeg failed to burn subtitles: {detail}")


# ---------------------------------------------------------------------------
# 6. Orchestration — the two entry points cli.py's --webui-* flags call
# ---------------------------------------------------------------------------

def caption_video(video_path, vid, worker_idx=0, meta_dict=None, lang="auto"):
    """Full pipeline through diarization: extract audio -> transcribe
    (existing pipeline, skip_demucs=True since this isn't music) -> diarize
    -> merge. Returns {"segments": [...], "speakers": [...],
    "audioDuration": float} or None. Does NOT burn — burning happens in
    burn_video_subtitles() on the *reviewed* segments the web UI hands back,
    same two-phase shape as the Lyrics Studio's transcribe-then-edit flow.
    """
    from monster_archiver import whisper_transcribe, audio_io

    state.GLOBAL_AUDIO_LANG = lang or "auto"

    ui.update_ui(worker_idx, msg="Extracting audio track...")
    audio_path = extract_audio_track(video_path, vid)
    if not audio_path:
        return None

    try:
        audio_duration = audio_io.get_audio_duration(audio_path)

        lrc_path = whisper_transcribe.transcribe_audio(
            audio_path, vid, worker_idx, meta_dict=meta_dict,
            word_level_lrc=False, skip_demucs=True,
        )
        if not lrc_path:
            return None

        segments = _parse_lrc_segments(lrc_path, audio_duration=audio_duration)
        if not segments:
            return None

        turns = diarize_audio(audio_path, worker_idx=worker_idx)
        segments = assign_speakers(segments, turns)

        speakers = []
        for seg in segments:
            if seg["speaker"] not in speakers:
                speakers.append(seg["speaker"])

        return {"segments": segments, "speakers": speakers, "audioDuration": audio_duration}
    finally:
        try:
            os.remove(audio_path)
        except OSError:
            pass


def burn_video_subtitles(video_path, segments, vid, font_name="Tahoma", font_size=40):
    """Given corrected {start, end, text, speaker} segments (from the web
    UI's review pass), render + burn them into video_path. Returns the
    output video path. Raises RuntimeError (with the ffmpeg detail) if the
    burn itself fails — see burn_subtitles().
    """
    os.makedirs(paths.LOGS_DIR, exist_ok=True)
    ass_path = os.path.join(paths.LOGS_DIR, f"{vid}_subs.ass")
    build_ass(segments, ass_path, font_name=font_name, font_size=font_size)

    base, ext = os.path.splitext(video_path)
    out_path = f"{base}_captioned{ext or '.mp4'}"
    try:
        result = burn_subtitles(video_path, ass_path, out_path)
    finally:
        # try/finally rather than a bare call-then-remove: burn_subtitles now
        # raises on failure instead of returning None, and the temp .ass
        # file should still get cleaned up either way.
        try:
            os.remove(ass_path)
        except OSError:
            pass

    return result
