"""AI vocal transcription: lazy Faster-Whisper model load, Demucs stem
isolation, the 3-pass (VAD / no-VAD / raw-bypass) transcription fallback
chain, hallucination-loop guard + phonetic corrections, forced alignment of
hinted lyrics to word timestamps, BPM/key analysis, and the atomic LRC write.

Extracted verbatim from rezakir.py's format_timestamp, _format_word_timestamp
(~lines 1796-1818), _prefix_len, _forced_align_lyrics, _CONSEC_REPEAT_LIMIT,
_norm_seg, _norm (~lines 2018-2150), and the main transcribe_audio
(~lines 2151-2527). Bare globals were rewired onto state.WHISPER_MODEL (now
reassigned via `state.WHISPER_MODEL = ...` instead of a local `global`, since
the model lives cross-module), state.ai_lock / state.cli_lock /
state.active_live_ui / state.console / state.CONF / state.GLOBAL_AUDIO_LANG /
state.gpu_lock, paths.LOGS_DIR, ui.update_ui / ui.log, and calls out to
demucs_stems.isolate_vocals_with_demucs, audio_features.analyze_audio_features,
audio_io.get_audio_duration, translation.translate_lrc_file.
"""
import os
import re
import time
import shutil
import tempfile
import traceback
from glob import glob

import torch

from monster_archiver import state, paths, ui, demucs_stems, audio_features, audio_io, translation


def format_timestamp(seconds):
    mins = int(seconds // 60)
    secs = seconds % 60
    if round(secs, 2) >= 60.0:
        mins += 1
        secs -= 60.0
    secs = max(0.0, secs)
    return f"[{mins:02d}:{secs:05.2f}]"


def _format_word_timestamp(seconds):
    """Return an Enhanced-LRC word-level tag <mm:ss.xx> for *seconds*.
    Same math as format_timestamp() but with angle brackets (Enhanced-LRC/A2
    spec); includes the same rounding-carry guard against invalid <01:60.00>.
    """
    mins = int(seconds // 60)
    secs = seconds % 60
    if round(secs, 2) >= 60.0:
        mins += 1
        secs -= 60.0
    secs = max(0.0, secs)
    return f"<{mins:02d}:{secs:05.2f}>"


def _prefix_len(a, b):
    """Length of the common prefix of two strings (character-by-character).
    Module-level so it is created once and reusable across call sites.
    """
    n = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        n += 1
    return n

def _forced_align_lyrics(hint_text, segments):
    """Align plain embedded lyrics (*hint_text*) to Whisper's word-level
    timestamps, so the ORIGINAL lyrics text is emitted with accurate timing
    instead of Whisper's re-transcription.
    Flattens all word timestamps into one list, then for each hint line
    slides a forward-only window to find the best-matching word (longest
    common prefix) and builds standard LRC [mm:ss.xx] lines from it.
    Falls back to segment-level timestamps if word timestamps are missing;
    returns None if there are no segments or hint lines.
    """
    if not segments or not hint_text:
        return None

    # Strip residual LRC/Enhanced-LRC markers from the hint (in case the
    # embedded tag somehow contained partial timestamps already).
    clean_hint = re.sub(r'\[\d+:\d+(?:\.\d+)?\]|<\d+:\d+(?:\.\d+)?>', '', hint_text)
    hint_lines = [ln.strip() for ln in clean_hint.splitlines() if ln.strip()]
    if not hint_lines:
        return None

    # ── Build flat word list from Whisper output ──────────────────────────────
    # Each entry: (start_seconds: float, normalised_word: str)
    flat_words = []
    for seg in segments:
        if getattr(seg, 'words', None):
            for w in seg.words:
                nw = _norm(w.word)
                if nw:
                    flat_words.append((w.start, nw))
        else:
            # No word-level data — distribute segment evenly across its tokens.
            tokens = [t for t in seg.text.split() if _norm(t)]
            if tokens:
                dur = (seg.end - seg.start) / len(tokens)
                for i, tok in enumerate(tokens):
                    flat_words.append((seg.start + i * dur, _norm(tok)))

    if not flat_words:
        return None

    # ── Match each lyrics line to the nearest Whisper word ───────────────────
    n_words = len(flat_words)
    n_lines = len(hint_lines)

    # Search window scales with word density (spans long instrumental gaps correctly) — old fixed window of 30 was too small and bunched unmatched lines at the song start.
    words_per_line = max(1, n_words // n_lines)
    SEARCH_WINDOW  = min(max(50, words_per_line * 3), 150)

    song_start = flat_words[0][0]
    song_end   = flat_words[-1][0]
    song_span  = max(song_end - song_start, 1.0)

    lrc_lines   = []
    word_cursor = 0        # forward-only pointer — lyrics are monotonically advancing
    last_ts     = song_start

    for line_idx, line in enumerate(hint_lines):
        line_words = [_norm(w) for w in line.split() if _norm(w)]
        if not line_words:
            continue

        # Slide a window forward from the current cursor.
        search_end = min(word_cursor + SEARCH_WINDOW, n_words)
        best_idx   = -1    # -1 = no real match found yet
        best_score = 0     # require ≥1 matched char in the first word

        for i in range(word_cursor, search_end):
            # ── Multi-word weighted scoring ── score position i on up to 4 consecutive words vs. the lyric line's first 4; earlier words weighted 4x more for tie-breaking.
            score = 0
            for w_off, lw in enumerate(line_words[:4]):
                w_idx = i + w_off
                if w_idx >= n_words:
                    break
                cand_w = flat_words[w_idx][1]
                pref = _prefix_len(lw, cand_w)
                if cand_w == lw:
                    pref += 10          # exact-match bonus
                weight = max(1, 4 - w_off)  # 4, 3, 2, 1 for successive words
                score += pref * weight
            # ──────────────────────────────────────────────────────────────

            if score > best_score:
                best_score = score
                best_idx   = i

        if best_idx >= 0:
            # Good match — use its timestamp and advance cursor past it.
            ts          = flat_words[best_idx][0]
            last_ts     = ts
            word_cursor = best_idx + 1
        else:
            # No match in window — interpolate proportionally across the song span (and advance cursor proportionally) so unmatched lines spread evenly rather than bunching.
            ratio       = line_idx / max(n_lines - 1, 1)
            ts          = song_start + ratio * song_span
            ts          = max(ts, last_ts)      # never go backwards
            last_ts     = ts
            new_cursor  = min(int(ratio * n_words) + 1, n_words - 1)
            word_cursor = max(word_cursor + 1, new_cursor)

        lrc_lines.append(f"{format_timestamp(ts)}{line}\n")
        # (cursor is advanced inside the if/else above)

    return lrc_lines if lrc_lines else None


# Consecutive identical-segment count that trips the hallucination-loop guard; defined at module level to avoid re-creation per call.
_CONSEC_REPEAT_LIMIT = 6   # raised from 3 — songs repeat choruses legitimately 4-5×;
                            # the old limit of 3 silently dropped the 4th+ occurrence.

def _norm_seg(s):
    """Normalise a Whisper segment string for hallucination-loop comparison.
    Collapses whitespace and lowercases so trivially-different repetitions match.
    Module-level so it is created once rather than on every transcribe_audio call.
    """
    return re.sub(r'\s+', ' ', s.lower()).strip()

def _norm(text):
    """Normalise a word for lyric-alignment matching: strip non-word characters and lowercase.
    Module-level so it is created once rather than on every _forced_align_lyrics call.
    """
    return re.sub(r'[^\w]', '', text.lower())


def transcribe_audio(file_path, vid, worker_idx, meta_dict=None, lyrics_hint=None, word_level_lrc=None, skip_demucs=False):
    """word_level_lrc lets a caller force sentence-level (one [mm:ss.xx] tag per
    line, normal-LRC style) or word-level (Enhanced-LRC <mm:ss.xx> per word,
    karaoke style) output regardless of the WORD_LEVEL_LRC config default.
    None (default) defers to state.CONF["WORD_LEVEL_LRC"] as before — used by
    cli.py's --webui-transcribe mode to always emit normal, per-line LRC for
    the web app while leaving the CLI's own config-driven default untouched.

    skip_demucs=True bypasses the two music-specific steps — Demucs vocal/
    instrumental stem separation and the post-transcription BPM/key analysis
    — for callers transcribing spoken dialogue rather than songs (see
    video_captions.caption_video). Demucs is trained to separate vocals from
    instrumentation; run on plain speech it does nothing useful and just
    costs GPU time, and BPM/key are meaningless for dialogue. Default False
    keeps every existing call site (the music pipeline, --webui-transcribe)
    byte-for-byte unchanged.
    """
    with state.ai_lock:
        if state.WHISPER_MODEL is None:
            from faster_whisper import WhisperModel
            # Auto-detect device; fall back to CPU when CUDA is unavailable.
            device       = "cuda" if torch.cuda.is_available() else "cpu"
            _model_size  = state.CONF.get("WHISPER_MODEL_SIZE", "large-v3")
            _compute_cfg = state.CONF.get("WHISPER_COMPUTE_TYPE", "")
            # Respect an explicit config value; otherwise pick the best
            # quantisation level for the available hardware automatically.
            compute_type = (
                _compute_cfg if _compute_cfg
                else ("int8_float16" if device == "cuda" else "int8")
            )
            with state.cli_lock:
                _live_snap = state.active_live_ui
                if _live_snap and _live_snap.is_started:
                    _live_snap.stop()
                state.console.print(
                    f"\n[bold yellow]📡 Initializing Faster-Whisper "
                    f"[bold]{_model_size}[/bold] "
                    f"([dim]{compute_type}[/dim]) on {device.upper()}...[/bold yellow]"
                )
                state.WHISPER_MODEL = WhisperModel(_model_size, device=device, compute_type=compute_type)
                state.console.print("[bold green]✅ AI Engine Integrated[/bold green]\n")
                if _live_snap and not _live_snap.is_started:
                    _live_snap.start()

    # ── Build initial_prompt ──
    # initial_prompt primes vocabulary/character-set as "already-transcribed" context, without implying it's literally in the audio.
    # Strategy: base "Song lyrics in <Language>." → override with title+artist → append genre if short → append a native-script hint for CJK.
    _lang_label = {
        "ja": "Japanese", "zh": "Chinese",
        "ko": "Korean",   "en": "English",
        "es": "Spanish",  "fr": "French",
    }.get(state.GLOBAL_AUDIO_LANG)
    initial_prompt = f"Song lyrics in {_lang_label}." if _lang_label else "Song lyrics."

    if meta_dict:
        t = (meta_dict.get("title")  or "").strip()
        a = (meta_dict.get("artist") or "").strip()
        g = (meta_dict.get("genre")  or "").strip()

        if t and a and a not in ("Unknown", ""):
            initial_prompt = f"Song lyrics for \"{t}\" by {a}."
        elif t:
            initial_prompt = f"Song lyrics for \"{t}\"."

        # Genre tag: only append when short and clean (avoids "Unknown / Various")
        if g and len(g) < 40 and g.lower() not in ("unknown", "various", "other"):
            initial_prompt += f" Genre: {g}."

    # CJK script anchors ("歌詞。"=Lyrics JA/ZH, "가사."=Lyrics KO) bias the tokeniser toward the right character set without adding false pre-transcribed context.
    if state.GLOBAL_AUDIO_LANG == "ja":
        initial_prompt += " 歌詞。"
    elif state.GLOBAL_AUDIO_LANG == "zh":
        initial_prompt += " 歌词。"
    elif state.GLOBAL_AUDIO_LANG == "ko":
        initial_prompt += " 가사."

    # When caller supplies lyrics_hint (pre-existing lyrics the user wants time-aligned), append only the first line (<=80 chars) as a vocabulary primer.
    # CRITICAL: never replace initial_prompt with the full lyrics text — Whisper would treat it as already-spoken and skip ahead, dropping 15-90s of real vocals. Strip residual LRC markers first.
    if lyrics_hint and isinstance(lyrics_hint, str):
        _hint_clean = re.sub(r'\[\d+:\d+(?:\.\d+)?\]|<\d+:\d+(?:\.\d+)?>', '', lyrics_hint)
        # Split on real newlines so we get the first proper lyric line, not
        # the first 80 chars of a long space-collapsed blob.
        _hint_lines = [ln.strip() for ln in _hint_clean.splitlines() if ln.strip()]
        if _hint_lines:
            _first_line = _hint_lines[0][:80]
            # Append; never replace — the metadata context must stay intact.
            initial_prompt = f"{initial_prompt} {_first_line}"
            ui.log("🎤 Using first lyric line as Whisper vocabulary primer (intro preserved)", "cyan")

    vocals_file = None
    demucs_dir  = None
    no_vocals_file = None

    if skip_demucs:
        target_audio = file_path
    else:
        try:
            vocals_file, demucs_dir = demucs_stems.isolate_vocals_with_demucs(file_path, vid, worker_idx)
            target_audio = vocals_file if vocals_file else file_path

            # Prefer the no_vocals (instrumental) stem for BPM/key — percussion/bass lock beat tracking better than the vocal stem or full mix.
            if vocals_file:
                _nv_candidates = glob(os.path.join(demucs_dir, "**", "no_vocals.*"), recursive=True)
                no_vocals_file = _nv_candidates[0] if _nv_candidates else None
        except Exception as _demucs_err:
            # Demucs is best-effort — fall back to the raw file rather than aborting
            # the whole transcription.  Log in debug mode so the issue is visible.
            if state.CONF.get("DEBUG_MODE"):
                ui.log(f"Demucs setup error (falling back to raw audio): {_demucs_err}", "yellow")
            target_audio   = file_path
            vocals_file    = None
            demucs_dir     = None
            no_vocals_file = None

    # Pre-compile phonetic correction patterns once per call (not inside the per-segment loop).
    _phonetic_patterns = [
        (bad, re.compile(re.escape(bad), re.IGNORECASE), good)
        for bad, good in state.CONF.get("PHONETIC_CORRECTIONS", {}).items()
    ]

    # Effective word-level-LRC flag for this call: an explicit word_level_lrc
    # argument overrides the config default (see docstring above).
    _word_level_lrc = state.CONF.get("WORD_LEVEL_LRC", True) if word_level_lrc is None else word_level_lrc

    try:
        lrc_lines = []

        # Serialize Whisper inference under gpu_lock (faster-whisper isn't documented thread-safe); 10-min timeout prevents a crashed worker from freezing every other thread.
        ui.update_ui(worker_idx, msg="AI: Waiting for GPU...")
        if not state.gpu_lock.acquire(timeout=600):
            raise TimeoutError("gpu_lock wait exceeded 10 min — a sibling worker may have crashed while holding it")

        try:
            ui.update_ui(worker_idx, msg="AI Inference Running...")
            # Shared Whisper params, defined once so every pass stays in sync (pass-specific overrides merge on top); all tuning knobs read from config.json.
            _whisper_base = dict(
                language             = state.GLOBAL_AUDIO_LANG if state.GLOBAL_AUDIO_LANG != "auto" else None,
                beam_size            = state.CONF.get("WHISPER_BEAM_SIZE",         5),
                patience             = state.CONF.get("WHISPER_PATIENCE",          1.5),
                best_of              = state.CONF.get("WHISPER_BEST_OF",           1),
                temperature          = state.CONF.get("WHISPER_TEMPERATURE_STEPS", [0.0, 0.2, 0.4, 0.6]),
                log_prob_threshold   = state.CONF.get("WHISPER_LOGPROB_THRESHOLD", -1.5),
                initial_prompt       = initial_prompt,
                condition_on_previous_text = state.CONF.get("WHISPER_CONDITION_ON_PREV", False),
                word_timestamps      = _word_level_lrc,
            )

            # Pass 1 — VAD-filtered transcription tuned for dense/orchestral mixes and accented singing.
            # VAD is only reliable on a clean vocal stem; skip it entirely on a full mix (Demucs failed) or it silently drops the intro.
            _vad_on_stem = vocals_file is not None
            # Build VAD kwargs conditionally — some faster-whisper versions raise TypeError if vad_parameters=None is passed alongside vad_filter=False.
            _vad_kwargs = (
                dict(
                    vad_filter=True,
                    vad_parameters=dict(
                        min_silence_duration_ms=state.CONF.get("WHISPER_VAD_MIN_SILENCE_MS", 500),
                        threshold=state.CONF.get("WHISPER_VAD_THRESHOLD", 0.15),
                        speech_pad_ms=state.CONF.get("WHISPER_VAD_SPEECH_PAD_MS", 600),
                        min_speech_duration_ms=state.CONF.get("WHISPER_VAD_MIN_SPEECH_MS", 100),
                        max_speech_duration_s=state.CONF.get("WHISPER_VAD_MAX_SPEECH_S", 60),
                    ),
                )
                if _vad_on_stem
                else dict(vad_filter=False)
            )
            segments_gen, info = state.WHISPER_MODEL.transcribe(
                target_audio,
                **_whisper_base,
                **_vad_kwargs,
                # Config-driven; default raised 0.45→0.65→0.80 since background music bleed pushes no_speech_prob to 0.65-0.79 on genuinely audible vocals.
                no_speech_threshold=state.CONF.get("WHISPER_NO_SPEECH_THRESHOLD", 0.80),
                # Default raised 1.8→2.4→2.6 — legitimately repetitive choruses were being flagged as hallucination loops and dropped.
                compression_ratio_threshold=state.CONF.get("WHISPER_COMPRESSION_RATIO", 2.6),
            )

            if state.GLOBAL_AUDIO_LANG == "auto":
                ui.update_ui(worker_idx, msg=f"AI: Detected {info.language} ({info.language_probability:.2f})")

            # Feature 3: info.language/info.language_probability are computed on every
            # pass regardless of auto-detect vs. an explicit --lang — persist them onto
            # meta_dict (same pattern as the BPM/key update below) so apply_tags_and_move
            # can write a LANGUAGE/TLAN tag. Previously shown in the UI for a moment, then lost.
            if meta_dict is not None:
                meta_dict["language"] = info.language
                meta_dict["language_confidence"] = round(float(info.language_probability), 3)

            segments = list(segments_gen)

            # Measure coverage: last segment timestamp vs total track duration.
            # info.duration may be 0.0 for FLAC — fall back to mutagen then librosa.
            audio_duration = float(getattr(info, 'duration', None) or 0.0) or audio_io.get_audio_duration(target_audio)
            # Use last-segment end time for coverage; sum of durations misses
            # tracks that go silent partway through.
            last_timestamp  = segments[-1].end   if segments else 0.0
            first_timestamp = segments[0].start  if segments else audio_duration
            coverage = (last_timestamp / audio_duration) if audio_duration > 0 else 1.0

            # Flag sparse when: end coverage <80% (vocals stop early / VAD missed tail), or first segment starts >15s in (intro skipped by VAD — classic J-Pop/OST bug; threshold lowered from 30s).
            _intro_skipped = (
                first_timestamp > 15.0
                and audio_duration > 60
                and first_timestamp / audio_duration > 0.10  # was 0.15 — too strict
            )
            # Coverage retry applies to ALL track lengths — a short track with only partial output is just as broken as a long one and needs the same retry.
            sparse_result = (coverage < 0.80) or _intro_skipped

            # Pass 2/3 overrides — relaxed thresholds, no VAD.
            _whisper_relaxed = dict(
                vad_filter=False,
                # Further relaxed than Pass 1 — last-resort pass tolerating more uncertainty so quiet vocals aren't discarded; config.json driven.
                no_speech_threshold=state.CONF.get("WHISPER_NO_SPEECH_THRESHOLD_RELAXED", 0.90),
                # Higher compression ceiling than Pass 1 — extremely repetitive tracks (chants, K-pop refrains) can legitimately hit 2.7-2.9.
                compression_ratio_threshold=state.CONF.get("WHISPER_COMPRESSION_RATIO_RELAXED", 3.0),
            )

            if not segments or sparse_result:
                if not segments:
                    reason = "VAD muted track entirely"
                elif _intro_skipped:
                    reason = f"intro skipped (vocals begin at {first_timestamp:.0f}s of {audio_duration:.0f}s)"
                else:
                    reason = f"sparse coverage ({coverage:.0%} of {audio_duration:.0f}s)"
                # Label the pass so the dashboard makes clear this is a normal retry,
                # not a failure — the old "retrying raw..." message looked alarming.
                ui.update_ui(worker_idx, msg=f"AI Pass 1: {reason} — trying Pass 2 (no VAD)...")

                # Pass 2 — no VAD at all, but keep the other fixes so we don't
                # hit the same no_speech / compression walls on the raw pass.
                segments_gen, _ = state.WHISPER_MODEL.transcribe(
                    target_audio,
                    **_whisper_base,
                    **_whisper_relaxed,
                )
                segments = list(segments_gen)

                # Pass 3 — retry on the original audio if Demucs' vocal stem was poor (bleed-through), or if the intro is still skipped after Pass 2.
                if vocals_file:
                    p2_last  = segments[-1].end   if segments else 0.0
                    p2_first = segments[0].start  if segments else audio_duration
                    p2_coverage = (p2_last / audio_duration) if audio_duration > 0 else 1.0
                    # Trigger Pass 3 when coverage is low OR when the intro is still
                    # skipped (p2_first still > 15 s even without VAD on the stem).
                    _p2_intro_still_skipped = _intro_skipped and p2_first > 15.0
                    if p2_coverage < 0.80 or _p2_intro_still_skipped:
                        _p3_reason = (
                            f"intro still at {p2_first:.0f}s" if _p2_intro_still_skipped
                            else f"{p2_coverage:.0%} reach"
                        )
                        ui.update_ui(worker_idx, msg=f"AI Pass 2: {_p3_reason} — Pass 3 retrying on raw audio (Demucs bypass)...")
                        segments_gen, _ = state.WHISPER_MODEL.transcribe(
                            file_path,
                            **_whisper_base,
                            **_whisper_relaxed,
                        )
                        segments_p3 = list(segments_gen)
                        p3_last  = segments_p3[-1].end   if segments_p3 else 0.0
                        p3_first = segments_p3[0].start  if segments_p3 else audio_duration
                    # Prefer Pass 3 if it reaches further into the track OR captures the intro earlier — the old check only guarded tail coverage.
                        if p3_last > p2_last or (p3_first < p2_first - 5.0):
                            segments = segments_p3

            # Emit final coverage verdict after all passes complete.
            _final_last = segments[-1].end if segments else 0.0
            _final_cov  = (_final_last / audio_duration) if audio_duration > 0 else 1.0
            if audio_duration > 60 and _final_cov < 0.80:
                ui.update_ui(worker_idx, msg=f"AI: Sparse track ({_final_cov:.0%} vocal reach — likely instrumental sections or complex mix)")

            # ── Mid-track gap detection ── the 3-pass system guards intro/tail but misses mid-song instrumental bridges; log gaps >30s (tracks >90s) so the user knows a section was skipped.
            if audio_duration > 90 and len(segments) >= 2:
                _GAP_THRESHOLD = 30.0  # seconds; gaps below this are normal breaths/instrumentals
                _gap_found = False
                for _g_i in range(len(segments) - 1):
                    _gap = segments[_g_i + 1].start - segments[_g_i].end
                    if _gap > _GAP_THRESHOLD:
                        _gap_start = segments[_g_i].end
                        _gap_end   = segments[_g_i + 1].start
                        ui.log(
                            f"⚠️  Mid-track gap detected: {_gap_start:.0f}s – {_gap_end:.0f}s "
                            f"({_gap:.0f}s silence/instrumental — lyrics may be missing here)",
                            "yellow",
                        )
                        _gap_found = True
                if _gap_found:
                    ui.update_ui(worker_idx, msg="AI: Mid-track gap(s) found — see log for details")
            # ─────────────────────────────────────────────────────────────────────

            # ── Hallucination-loop guard ── Whisper can loop on a short phrase over unclear audio (common in intros). Two layers: (1) drop segments the model itself flags as uncertain speech; (2) drop segments repeated consecutively past a limit — a consecutive counter (not a window) avoids wrongly dropping legitimate repeated chorus lines.
            _last_norm:  str  = ""
            _consec_cnt: int  = 0
            # ─────────────────────────────────────────────────────────────────────

            for segment in segments:
                text = segment.text.strip()
                if not text:
                    continue

                # Layer 1 — per-segment no-speech confidence, raised 0.55→0.80→0.95 (config-driven) since music bleed pushes it to 0.80-0.94 on genuine vocals.
                if getattr(segment, 'no_speech_prob', 0.0) > state.CONF.get("WHISPER_NO_SPEECH_PROB_FILTER", 0.95):
                    continue

                # Layer 2 — consecutive-repeat / hallucination-loop check.
                _norm_text = _norm_seg(text)
                if _norm_text == _last_norm:
                    _consec_cnt += 1
                else:
                    _last_norm  = _norm_text
                    _consec_cnt = 1
                if _consec_cnt > _CONSEC_REPEAT_LIMIT:
                    continue

                text_lower = text.lower()
                changed = False
                for bad_phrase, pattern, correct_phrase in _phonetic_patterns:
                    # lower() the config key before comparing against text_lower (the regex itself already uses IGNORECASE).
                    if bad_phrase.lower() in text_lower:
                        new_text = pattern.sub(correct_phrase, text)
                        if new_text != text:
                            text = new_text
                            # Refresh text_lower after each substitution so chained patterns see the updated text, not the original.
                            text_lower = text.lower()
                            changed = True

                start_str = format_timestamp(segment.start)

                # Word-level LRC only emitted when no phonetic correction fired — corrections are phrase-level and can't be mapped per-word.
                if _word_level_lrc and getattr(segment, 'words', None) and not changed:
                    line_content = start_str
                    for w in segment.words:
                        line_content += f"{_format_word_timestamp(w.start)}{w.word}"
                    lrc_lines.append(f"{line_content}\n")
                else:
                    lrc_lines.append(f"{start_str}{text}\n")

            # ── Forced alignment (sync-with-AI, "S" option) ── lyrics_hint holds the original text; align it to Whisper's word timestamps via _forced_align_lyrics() instead of using Whisper's re-transcription. Falls back to raw Whisper output if alignment fails.
            if lyrics_hint and isinstance(lyrics_hint, str) and lrc_lines:
                ui.update_ui(worker_idx, msg="AI: Aligning embedded lyrics to timestamps...")
                aligned = _forced_align_lyrics(lyrics_hint, segments)
                if aligned:
                    lrc_lines = aligned
                    ui.log("🎤 Forced alignment complete — original lyrics mapped to Whisper timestamps", "bold green")
                else:
                    ui.log("⚠️  Forced alignment produced no output — keeping raw Whisper transcription", "yellow")
            # ──────────────────────────────────────────────────────────────────

        finally:
            state.gpu_lock.release()

        # Analyze BPM/key while Demucs stems are still on disk — prefer the instrumental stem, fall back to the original file if Demucs didn't run.
        # Skipped entirely for skip_demucs callers (dialogue has no meaningful BPM/key).
        if not skip_demucs:
            ui.update_ui(worker_idx, msg="Analyzing BPM & Key...")
            _analysis_src = no_vocals_file if no_vocals_file else file_path
            _features = audio_features.analyze_audio_features(_analysis_src)
            if _features and meta_dict is not None:
                meta_dict.update(_features)

        if demucs_dir and os.path.exists(demucs_dir):
            shutil.rmtree(demucs_dir, ignore_errors=True)

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        if not lrc_lines:
            ui.update_ui(worker_idx, msg="[yellow]No speech detected (Instrumental?)[/yellow]")
            time.sleep(1.5)
            return None

        out_path = os.path.join(paths.LOGS_DIR, f"{vid}_lyrics.lrc")
        # Atomic write — same pattern as translate_lrc_file.
        # A bare open("w") leaves a partial file on any crash mid-write.
        _lrc_dir = os.path.dirname(out_path)
        _fd, _tmp = tempfile.mkstemp(dir=_lrc_dir, suffix=".lrc.tmp")
        try:
            with os.fdopen(_fd, "w", encoding="utf-8") as f:
                f.writelines(lrc_lines)
            os.replace(_tmp, out_path)
        except Exception:
            try:
                os.unlink(_tmp)
            except OSError:
                pass
            raise

        ui.update_ui(worker_idx, msg="Formatting & Translating...")
        _meta_t = (meta_dict or {}).get("title")
        _meta_a = (meta_dict or {}).get("artist")
        translation.translate_lrc_file(out_path, meta_title=_meta_t, meta_artist=_meta_a)

        return out_path

    except Exception as e:
        if demucs_dir and os.path.exists(demucs_dir):
            shutil.rmtree(demucs_dir, ignore_errors=True)

        err_str = str(e)[:200]
        ui.update_ui(worker_idx, msg=f"[bold red]AI Err: {err_str}[/bold red]")
        if state.CONF.get("DEBUG_MODE"):
            ui.log(f"AI Processing Err:\n{traceback.format_exc()}", "red")
        time.sleep(3)
        return None
