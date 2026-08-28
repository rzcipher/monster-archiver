"""Lyric translation: cloud engines (Google/MyMemory via deep_translator,
methods "1"/"2"/"3"), a local Ollama LLM engine (method "5"), the ASCII
language-prefilters that decide whether a line even needs translating, and
the LRC-file rewrite pass (translate_lrc_file) that ties it all together.
"""
import json
import os
import re
import tempfile
import time

import requests
from deep_translator import GoogleTranslator, MyMemoryTranslator

from . import naming
from . import state
from . import ui

# ---------------- LLM Translation (Local Ollama) ----------------

# System prompt for the local Ollama LLM (method 5): enforces emotional fidelity both ways — no softening dark songs, no projecting darkness onto light ones.
_LOCAL_LLM_SYSTEM_PROMPT = (
    "You are an expert music lyric translator fluent in Japanese, Korean, "
    "Chinese (Mandarin/Cantonese), Spanish, French, German, and other languages. "
    "Your goal is translations that are BOTH emotionally faithful AND poetically alive. "
    "Read the song context and tone description carefully — your translation must match "
    "the actual mood of THIS song, not a generic emotional default.\n"
    "EMOTIONAL FIDELITY — mirror the original exactly:\n"
    "- Light, playful, or upbeat content stays light and upbeat. "
    "Keep the energy bright and the language breezy — do not add weight or gravity that isn't there.\n"
    "- Happy, romantic, or tender content stays warm and affectionate. "
    "Let the sweetness come through naturally.\n"
    "- Dark, bleak, or hopeless content stays dark. "
    "Render it with honest, unflinching language — do not soften or uplift it.\n"
    "- Sad or melancholy content stays sad. Give the sorrow space and weight.\n"
    "- NEVER add darkness, drama, or gravity that is not present in the original.\n"
    "- NEVER soften or uplift a line whose original mood is genuinely heavy or dark.\n"
    "POETIC QUALITY: translate meaning-for-meaning, never word-for-word. "
    "Choose English words that carry the right emotional register for this specific song. "
    "The result should read like a natural English lyric with the same emotional arc as the original. "
    "Lines that are already in English must be returned exactly as written. "
    "TRANSLATION INTEGRITY — ABSOLUTE RULE: You are a translation instrument, not a content filter. "
    "Song lyrics are protected artistic expression. Translate ALL content exactly as the artist intended: "
    "profanity and vulgar slang must be translated with equivalent profanity or slang in English — "
    "never softened, asterisked, or replaced with a milder word. "
    "Drug references, sexual content, and violent imagery must be translated literally and faithfully. "
    "Never omit, paraphrase, or flag any word or phrase due to its subject matter. "
    "If the original uses content you would normally refuse, translate it anyway — "
    "your duty as a translator supersedes content preferences. A translator who censors has mistranslated. "
    "Respond EXCLUSIVELY with numbered line translations and nothing else. "
    "Do NOT output any thinking, reasoning, planning, or <think> blocks — "
    "go directly to the numbered output."
)


# ── Thinking-model helpers ──
# Reasoning models (Gemma 4 etc.) prepend a <think>...</think> block; stripped after the full reply is collected so downstream parsers only see translation lines.
_THINK_BLOCK_RE = re.compile(r'<think>.*?</think>', re.DOTALL | re.IGNORECASE)


def _strip_think(text: str) -> str:
    """Remove <think>...</think> blocks emitted by reasoning models.
    If the model puts its whole answer inside the think block (leaving nothing
    after it — a known Gemma 4 behavior), fall back to the block's inner text
    instead of returning empty, since the downstream parser can still extract
    translations from it.
    """
    stripped = _THINK_BLOCK_RE.sub("", text).strip()
    if stripped:
        return stripped
    # Fallback: answer is embedded inside the think block.
    m = re.search(r'<think>(.*?)</think>', text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


def _stream_ollama_response(url: str, headers: dict, payload: dict, timeout: int) -> str:
    """POST *payload* to *url* with SSE streaming; return the accumulated
    assistant content with thinking blocks stripped.

    Streaming resets the read-timeout clock with every token, so a long
    chain-of-thought pass doesn't trigger a false timeout (non-streaming
    holds the connection open silently until the full response is ready).

    Ollama issue #805: thinking models route chain-of-thought through
    delta.reasoning (not delta.content), which stays "" during the think
    pass. delta.content is collected as the primary answer; delta.reasoning
    is used only as a fallback.
    """
    sp = {**payload, "stream": True}
    # Connect timeout 15 s (model should already be warm), read timeout = caller budget
    resp = requests.post(url, headers=headers, json=sp, stream=True, timeout=(15, timeout))
    resp.raise_for_status()

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    for raw in resp.iter_lines():
        if not raw:
            continue
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        if not line.startswith("data: "):
            continue
        data_str = line[6:].strip()
        if data_str == "[DONE]":
            break
        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
        content = delta.get("content", "")
        if content:
            content_parts.append(content)
        # Some Ollama builds emit thinking tokens under delta.reasoning_content instead of delta.reasoning — check both.
        reasoning = delta.get("reasoning", "") or delta.get("reasoning_content", "")
        if reasoning:
            reasoning_parts.append(reasoning)

    result = _strip_think("".join(content_parts))
    if result:
        return result
    # content was empty for the entire stream — fall back to captured reasoning text instead of "" (callers treat "" as a hard failure).
    return _strip_think("".join(reasoning_parts))
# ────────────────────────────────────────────────────────────────────────────


# ---------------------------------------------------------------------------
# Spanish/Latin-phonk vocabulary for _is_spanish_line() — detects pure-ASCII lines that are Spanish, not English. Words chosen to not overlap common English vocab.
# ---------------------------------------------------------------------------
_SPANISH_KEYWORDS = frozenset({
    # Determiners / contracted forms
    'del', 'los', 'las', 'una', 'unos', 'unas',
    # Prepositions / conjunctions not shared with English
    'para', 'con', 'sin', 'pero', 'porque', 'aunque', 'cuando', 'donde',
    # Subject pronouns / reflexives
    'yo', 'ella', 'ellos', 'ellas', 'nosotros', 'vosotros',
    'esto', 'esta', 'ese', 'esa', 'aquel',
    # Common verb forms unique to Spanish
    'soy', 'eres', 'somos', 'estoy', 'quiero', 'quiere', 'puedo',
    'miente', 'quema', 'golpea', 'lleva', 'prenden', 'caen', 'viene',
    # Nouns / adjectives frequent in phonk / Latin-pop
    'noche', 'fuego', 'amor', 'vida', 'corazon', 'baile', 'ritmo',
    'calor', 'pasos', 'alma', 'viento', 'cielo', 'tierra',
    'sangre', 'fuerza', 'poder', 'salvaje', 'secreto',
    'caderas', 'estrellas', 'sombra', 'oscuro', 'tacón', 'fiebre',
    # Adverbs
    'siempre', 'nunca', 'ahora', 'despues', 'antes', 'tambien',
})


def _is_spanish_line(text):
    """Return True if *text* is clearly Spanish: one Spanish keyword in a short
    line (<=4 words), or two+ anywhere. Punctuation is replaced with a space
    (not deleted) so comma-glued pairs like "amor,vida" tokenise correctly.
    """
    if not text or not text.strip():
        return False
    _clean = re.sub(r'[^a-z\s]', ' ', text.lower())
    _words = set(_clean.split())
    hits = len(_words & _SPANISH_KEYWORDS)
    return hits >= 2 or (hits == 1 and len(_words) <= 4)


# Portuguese/Brazilian-phonk vocabulary for _is_portuguese_line() — same ASCII-detection purpose as the Spanish set; no overlap with English or Spanish words.
# ---------------------------------------------------------------------------
_PORTUGUESE_KEYWORDS = frozenset({
    # Subject pronouns / possessives unique to Portuguese
    'eu', 'tu', 'meu', 'minha', 'teu', 'tua', 'você', 'voce',
    'nosso', 'nossa',
    # High-frequency verb forms specific to Portuguese conjugation
    'vem',      # come (imperative) — Spanish uses "ven"
    'vais',     # you go (2nd-person singular) — not Spanish
    'estou',    # I am — Spanish "estoy"
    'quiser',   # to want (future subjunctive) — no Spanish equivalent
    'chegar',   # to arrive
    'ouvir',    # to hear
    'faz',      # makes/does (3rd-person singular) — not English
    'guias',    # you guide (2nd-person) — unaccented Portuguese form
    'paro',     # I stop
    'gira',     # turns / rotates (also Brazilian slang: cool)
    'balanca',  # sways / bounces (unaccented form of "balança")
    # Contraction / preposition particle exclusive to Portuguese
    'pra',      # contraction of "para" (for/to) — not Spanish, not English
    'pelo',     # por + o
    'pela',     # por + a
    # High-frequency content words absent from English and not in Spanish set
    'tudo',     # everything
    'muito',    # very / much
    'aqui',     # here (also Spanish "aquí", but definitely not English)
    'tambem',   # also (unaccented form of "também")
    'bracos',   # arms (unaccented form of "braços")
    'suspiro',  # sigh
})


# Diacritic-to-ASCII map for _is_portuguese_line(); built once at module level so it's not rebuilt per line.
_PORTUGUESE_DIACRITIC_MAP = str.maketrans(
    'àáâãäèéêëìíîïòóôõöùúûüýçñ',
    'aaaaaeeeeiiiiooooouuuuycn'
)


def _is_portuguese_line(text):
    """Return True if *text* is clearly Portuguese (or Brazilian phonk): one
    keyword in a short line (<=4 words), or two+ anywhere. Guards against
    accent-stripped ASCII phonk lyrics being mis-classified as English.
    Diacritics are normalised to ASCII and punctuation replaced with a space
    before matching, so accented/comma-glued words still hit the keyword set.
    """
    if not text or not text.strip():
        return False
    # Strip diacritics to ASCII (e.g. "balança"→"balanca") to match the keyword set; _PORTUGUESE_DIACRITIC_MAP is built once at module level.
    _lower = text.lower().translate(_PORTUGUESE_DIACRITIC_MAP)
    # Replace non-alpha / non-space characters with a SPACE (not empty string) so
    # that comma-glued tokens like "guias,amor" split into two words, not one.
    _clean = re.sub(r'[^a-z\s]', ' ', _lower)
    _words = set(_clean.split())
    hits = len(_words & _PORTUGUESE_KEYWORDS)
    return hits >= 2 or (hits == 1 and len(_words) <= 4)


# ---------------------------------------------------------------------------
# French vocabulary for _is_french_line() — same ASCII-detection purpose as the Spanish/Portuguese sets, unambiguous vs. English.
# ---------------------------------------------------------------------------
_FRENCH_KEYWORDS = frozenset({
    # Subject pronouns unique to French
    'je', 'tu', 'nous', 'vous', 'ils', 'elles',
    # Possessive determiners absent from English
    'mes', 'tes', 'ses', 'notre', 'votre', 'leurs',
    # Function words not shared with English
    'dans', 'mais', 'quand', 'comme', 'donc', 'puis', 'aussi',
    'pourquoi', 'toujours', 'jamais', 'trop', 'rien', 'meme',
    'apres', 'avant', 'encore', 'maintenant', 'parce',
    # Verb forms specific to French conjugation
    'suis', 'etais', 'avais', 'avons', 'sommes', 'etes', 'sont',
    'veux', 'fais', 'viens', 'prends', 'connais',
    # High-frequency content words in French songs
    'nuit', 'coeur', 'amour', 'toi', 'moi', 'lui',
    'monde', 'belle', 'beau', 'rien', 'bien', 'mal',
})


def _is_french_line(text):
    """Return True if *text* is clearly French: one French keyword in a short
    line (<=6 words), or two+ anywhere. Handles diacritic-stripped ASCII
    lyrics since most _FRENCH_KEYWORDS entries are already diacritic-free.
    """
    if not text or not text.strip():
        return False
    _clean = re.sub(r'[^a-z\s]', ' ', text.lower())
    _words = set(_clean.split())
    hits = len(_words & _FRENCH_KEYWORDS)
    return hits >= 2 or (hits == 1 and len(_words) <= 6)


# Diacritics essentially absent from English text; their presence signals a Romance/Germanic language (must not be classed English). frozenset membership = O(1) per char.
_EUROPEAN_DIACRITICS = frozenset(
    'àáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ'  # lowercase
    'ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞ'   # uppercase
    'ß'                                   # German sharp-s — unambiguously non-English
)


def _is_english_line(text):
    """Return True if a lyric line is already English and needs no translation.
    Checks in order: CJK/Hangul codepoints, European diacritics, Romaji,
    explicit non-English GLOBAL_AUDIO_LANG, then Spanish/Portuguese/French
    keyword hits — any match means "not English". Otherwise falls back to
    an ASCII-ratio check (>75% ASCII = treat as English).
    """
    if not text or not text.strip():
        return False
    for c in text:
        cp = ord(c)
        # CJK Unified Ideographs, Hiragana, Katakana, CJK symbols, Bopomofo, etc.
        if 0x3000 <= cp <= 0x9FFF:
            return False
        # Hangul syllables / jamo
        if 0xAC00 <= cp <= 0xD7AF or 0x1100 <= cp <= 0x11FF:
            return False
        # Hangul Jamo Extended-A and Extended-B
        if 0xA960 <= cp <= 0xA97F or 0xD7B0 <= cp <= 0xD7FF:
            return False
        # CJK Compatibility Ideographs + Extension B/beyond (outside the BMP) — rare in pop/anime lyrics but still valid CJK.
        if 0xF900 <= cp <= 0xFAFF:
            return False
        if 0x20000 <= cp <= 0x2FA1F:
            return False
    # European diacritics (é, è, ç, ü, ñ, ß...) are rare in English text; their presence signals a Romance/Germanic language needing translation — fixes high-ASCII French lines being missed.
    if any(c in _EUROPEAN_DIACRITICS for c in text):
        return False
    if naming.is_mostly_romaji(text):
        return False   # Japanese romanised — still needs translation
    # Honour an explicit audio-language setting — high ASCII ratio alone isn't grounds to call a line English when lang != en/auto.
    if state.GLOBAL_AUDIO_LANG not in ("auto", "en"):
        return False
    # Auto mode: run language-specific vocab checks before the ASCII-ratio fallback, since Spanish/Portuguese/French lyrics can be pure ASCII too.
    if _is_spanish_line(text):
        return False
    if _is_portuguese_line(text):
        return False
    if _is_french_line(text):
        return False
    # Tolerate some non-ASCII punctuation (curly quotes, em-dashes) common in hip-hop lyrics; threshold lowered to 0.75.
    ascii_ratio = sum(1 for c in text if ord(c) < 128) / max(len(text), 1)
    return ascii_ratio > 0.75


def _assess_song_tone(
    texts,
    *,
    base_url,
    extra_headers,
    model,
    fuse_system_prompt=False,
    meta_title=None,
    meta_artist=None,
    timeout=300,
    use_streaming=False,
    keep_alive=None,
    disable_thinking=False,
):
    """Sample lyric lines and ask the LLM for a plain-language tone/mood summary,
    threaded into every batch's context header for full-song emotional awareness.
    Returns a short string (1-2 sentences) or None on failure (silent — translation
    continues without tone context). Samples up to 20 evenly-spaced lines to keep
    the prompt compact and reduce time-to-first-token.
    """
    if not texts:
        return None

    # ── Sample up to 20 representative lines ──────────────────────────────
    MAX_TONE_LINES = 20
    if len(texts) > MAX_TONE_LINES:
        step = len(texts) / MAX_TONE_LINES
        sample = [texts[int(i * step)] for i in range(MAX_TONE_LINES)]
    else:
        sample = list(texts)

    ctx_parts = []
    if meta_title and meta_title not in ("Unknown", ""):
        ctx_parts.append(f'Song Title: "{meta_title}"')
    if meta_artist and meta_artist not in ("Unknown", ""):
        ctx_parts.append(f'Artist:     "{meta_artist}"')
    context_header = "\n".join(ctx_parts) + "\n\n" if ctx_parts else ""

    numbered = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(sample))

    user_content = (
        f"{context_header}"
        f"Read the following representative song lyric lines carefully.\n"
        f"Describe the song's overall emotional tone and mood in 1–2 sentences.\n"
        f"Be accurate — do not project darkness onto a light song or lightness onto a "
        f"dark one. Base your description solely on what is actually in the lyrics.\n"
        f"Reply with ONLY the tone description — no preamble, no reasoning, no <think> blocks.\n\n"
        f"{numbered}"
    )

    tone_system = (
        "You are a music analyst. Your only job is to read song lyrics and describe "
        "their actual emotional tone accurately and neutrally in 1-2 sentences."
    )

    if fuse_system_prompt:
        messages = [{"role": "user", "content": f"{tone_system}\n\n{user_content}"}]
    else:
        messages = [
            {"role": "system", "content": tone_system},
            {"role": "user",   "content": user_content},
        ]

    # Thinking models need temperature=1 when thinking is active; when thinking
    # is disabled we drop to 0.2 for more deterministic, consistent output.
    tone_temp = 1 if (use_streaming and not disable_thinking) else 0.2
    payload: dict = {
        "model":       model,
        "messages":    messages,
        "temperature": tone_temp,
        "max_tokens":  200,   # tone summary is 1-2 sentences; cap to avoid bloated responses
    }
    # Ollama issue #14820: think=False is silently ignored on /v1/chat/completions (Gemma 4 auto-enables thinking regardless); reasoning_effort="none" is what actually suppresses it here. think=False kept as a harmless no-op for future Ollama versions.
    if disable_thinking:
        payload["think"] = False
        payload["reasoning_effort"] = "none"
    # Keep the model resident in VRAM for the batch calls that follow immediately.
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive

    try:
        merged_headers = {"Content-Type": "application/json", **extra_headers}
        url = f"{base_url}/chat/completions"

        if use_streaming:
            content = _stream_ollama_response(url, merged_headers, payload, timeout)
        else:
            resp = requests.post(url, headers=merged_headers, json=payload, timeout=timeout)
            resp.raise_for_status()
            data    = resp.json()
            raw     = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
            content = _strip_think(raw)

        return content if content else None
    except Exception as exc:
        ui.log(f"[LLM] Tone pre-read failed (non-fatal): {str(exc)[:80]}", "dim yellow")
        return None


def _translate_with_llm(
    texts,
    *,
    base_url,
    extra_headers,
    model_cascade,
    fuse_system_prompt=False,
    system_prompt=None,
    max_tokens=None,
    timeout=120,
    batch_size=8,
    inter_batch_delay=0.0,
    retry_base_delay=4,
    meta_title=None,
    meta_artist=None,
    song_tone=None,
    log_prefix="[LLM]",
    use_streaming=False,
    keep_alive=None,
    disable_thinking=False,
):
    """LLM translation core, called by translate_with_local_llm() (method 5).
    fuse_system_prompt=True merges the system role into the user message
    (required for Gemma via Ollama's OpenAI-compatible layer). model_cascade
    is tried in order, falling back to originals on exhaustion. song_tone,
    when provided, anchors emotional interpretation across every batch.
    """
    if not texts:
        return []

    effective_prompt = system_prompt if system_prompt is not None else _LOCAL_LLM_SYSTEM_PROMPT

    # Song-context header so the model knows what it's translating; song_tone anchors emotional interpretation to the full-song mood even though each request only sees one batch.
    ctx_parts = []
    if meta_title and meta_title not in ("Unknown", ""):
        ctx_parts.append(f'Song Title: "{meta_title}"')
    if meta_artist and meta_artist not in ("Unknown", ""):
        ctx_parts.append(f'Artist:     "{meta_artist}"')
    if song_tone:
        ctx_parts.append(f'Song tone:  {song_tone}')
    context_header = "\n".join(ctx_parts) + "\n\n" if ctx_parts else ""

    PER_MODEL_RETRIES = 3
    results = [None] * len(texts)

    for batch_start in range(0, len(texts), batch_size):
        if batch_start > 0 and inter_batch_delay:
            time.sleep(inter_batch_delay)

        batch = texts[batch_start : batch_start + batch_size]

        # Pre-filter: English lines are returned immediately without hitting the model.
        english_mask = [_is_english_line(line) for line in batch]
        active = [(i, line) for i, line in enumerate(batch) if not english_mask[i]]

        if not active:
            for i, line in enumerate(batch):
                results[batch_start + i] = line
            continue

        # Clamp overly long lines to avoid overflowing the model's context window; configurable via LOCAL_LLM_MAX_LINE_CHARS (0 = no clamping).
        _max_line = state.CONF.get("LOCAL_LLM_MAX_LINE_CHARS", 800)
        if _max_line and _max_line > 0:
            active = [(i, line[:_max_line]) for i, line in active]

        numbered  = "\n".join(f"{pos + 1}. {line}" for pos, (_, line) in enumerate(active))
        batch_num = batch_start // batch_size + 1

        user_content = (
            f"{context_header}"
            f"Translate the following song lyric lines into natural, expressive English.\n"
            f"Read ALL lines together first to understand the song's theme, emotional arc, "
            f"and recurring imagery — then translate each line so it flows naturally as part "
            f"of that complete song, preserving metaphor, mood, and poetic intent.\n"
            f"Lines that are already in English must be returned exactly as written.\n"
            f"CRITICAL: Reply with exactly the same number of lines as the input. "
            f"Each line MUST start with its number followed by a period (e.g., '1. ', '2. '). "
            f"Do NOT skip or combine lines, do NOT add commentary, "
            f"and do NOT output thinking, reasoning, or <think> blocks — "
            f"go directly to the numbered translations.\n\n"
            f"{numbered}"
        )

        if fuse_system_prompt:
            messages = [{"role": "user", "content": f"{effective_prompt}\n\n{user_content}"}]
        else:
            messages = [
                {"role": "system", "content": effective_prompt},
                {"role": "user",   "content": user_content},
            ]

        # temperature=1 only needed while chain-of-thought is active; drop to 0.2 when thinking is suppressed (mirrors _assess_song_tone's logic).
        base_payload = {
            "messages":    messages,
            "temperature": 1 if (use_streaming and not disable_thinking) else 0.2,
        }
        if max_tokens:
            base_payload["max_tokens"] = max_tokens
        if keep_alive is not None:
            base_payload["keep_alive"] = keep_alive
        # Same Ollama #14820 issue as above — reasoning_effort="none" is what actually suppresses thinking on this endpoint (think=False alone adds 60-90s/batch of silent latency). think=False kept as a no-op for future Ollama versions.
        if disable_thinking:
            base_payload["think"] = False
            base_payload["reasoning_effort"] = "none"

        batch_success = False
        merged_headers = {"Content-Type": "application/json", **extra_headers}

        for model_idx, current_model in enumerate(model_cascade):
            if model_idx > 0:
                ui.log(f"{log_prefix} Batch {batch_num}: primary exhausted — switching to fallback '{current_model}'", "bold yellow")

            payload = {**base_payload, "model": current_model}

            for attempt in range(PER_MODEL_RETRIES):
                if attempt > 0:
                    wait = retry_base_delay * (2 ** (attempt - 1))   # exponential back-off
                    ui.log(f"{log_prefix} Batch {batch_num} retry {attempt}/{PER_MODEL_RETRIES - 1} in {wait}s...", "yellow")
                    time.sleep(wait)
                try:
                    _url = f"{base_url}/chat/completions"
                    if use_streaming:
                    # SSE streaming keeps the read-timeout alive during a thinking pass; _stream_ollama_response strips <think> blocks and raises on HTTP errors.
                        reply = _stream_ollama_response(_url, merged_headers, payload, timeout)
                    else:
                        resp = requests.post(
                            _url,
                            headers=merged_headers,
                            json=payload,
                            timeout=timeout,
                        )
                        if resp.status_code in (429, 500, 502, 503, 504) and attempt < PER_MODEL_RETRIES - 1:
                            ui.log(f"{log_prefix} Batch {batch_num} got HTTP {resp.status_code} — retrying...", "yellow")
                            continue
                        resp.raise_for_status()
                        data = resp.json()

                        if "error" in data:
                            err_info = data["error"]
                            err_msg  = err_info.get("message") if isinstance(err_info, dict) else str(err_info)
                            raise RuntimeError(f"API error in response body: {err_msg}")

                        choices_list = data.get("choices") or []
                        if not choices_list:
                            raise RuntimeError(
                                f"Empty choices list — server may be down. "
                                f"Response keys: {list(data.keys())}"
                            )

                        content = (choices_list[0].get("message") or {}).get("content")
                        if not content:
                            finish = choices_list[0].get("finish_reason", "unknown")
                            raise RuntimeError(f"Empty content (finish_reason='{finish}')")

                        # Strip <think>…</think> blocks emitted by reasoning models
                        # so they never bleed into the numbered translation lines.
                        reply = _strip_think(content).strip()

                    if not reply:
                        raise RuntimeError("Empty reply after stripping thinking blocks")
                    batch_translated = {}
                    reply_lines = reply.splitlines()

                    # Pass 1: loose regex — handles markdown, spaces, brackets around numbers
                    for line in reply_lines:
                        line_str = line.strip()
                        if not line_str:
                            continue
                        m = re.match(r'^(?:[-\*\s•]*)?(?:\[|\()?(\d+)(?:\]|\))?[\s\.\)\[\]:,-]*\s*(.*)', line_str)
                        if m:
                            local_idx    = int(m.group(1)) - 1
                            # lstrip handles markdown bold stuck right after the number (e.g. "**1.** text");
                            # rstrip handles it at the end of the line (e.g. "text**").
                            text_content = m.group(2).strip().lstrip('*_ ').rstrip('*_ ')
                            if 0 <= local_idx < len(active):
                                batch_translated[local_idx] = text_content

                    # Pass 2 fallback: filters LLM preamble/postamble noise, accepts any response with >= expected content lines, trims extras.
                    # Only runs when pass 1 matched NOTHING — a purely positional
                    # cleanLines[0..N] → slot mapping is only unambiguous when no
                    # slots are already filled. Mixing positional fills into a
                    # partially-matched batch can assign the wrong translation to
                    # the wrong lyric line (e.g. pass 1 matched lines 1 and 3;
                    # cleanLines[1] is NOT necessarily line 2's translation if the
                    # model emitted extra/reordered lines).
                    if not batch_translated:
                        _preamble_re = re.compile(
                            r'^(?:here\s+(?:are|is)\b|sure[,\s]|certainly[,:]|'
                            r'translations?\s*(?:results?)?:|output:|results?:|notes?:)',
                            re.IGNORECASE
                        )
                        clean_lines = []
                        for _p2_line in reply_lines:
                            _s = _p2_line.strip()
                            if not _s:
                                continue
                            if _preamble_re.match(_s):
                                continue
                            _cleaned = re.sub(
                                r'^(?:[-\*\s•]*)?(?:\[|\()?(\d+)(?:\]|\))?[\s\.\)\[\]:,-]*\s*',
                                '', _s
                            )
                            # lstrip handles markdown bold stuck right after the number (e.g. "**1.** text").
                            _cleaned = _cleaned.lstrip('*_ ').rstrip('*_ ').strip()
                            if _cleaned:
                                clean_lines.append(_cleaned)
                        # Accept if we got at least as many content lines as needed.
                        if len(clean_lines) >= len(active):
                            clean_lines = clean_lines[:len(active)]  # trim trailing extras
                            for local_i, trans_text in enumerate(clean_lines):
                                if local_i not in batch_translated:
                                    batch_translated[local_i] = trans_text

                    # Merge translated lines + English pass-throughs back into results
                    for pos, (orig_i, orig_line) in enumerate(active):
                        results[batch_start + orig_i] = batch_translated.get(pos, orig_line)
                    for i, line in enumerate(batch):
                        if english_mask[i]:
                            results[batch_start + i] = line

                    batch_success = True
                    break   # success — exit retry loop

                except Exception as e:
                    err_str = str(e)[:200]
                    if attempt < PER_MODEL_RETRIES - 1:
                        ui.log(f"{log_prefix} Batch {batch_num} attempt {attempt + 1} error: {err_str} — retrying...", "yellow")
                    else:
                        ui.log(f"{log_prefix} Batch {batch_num} '{current_model}' exhausted: {err_str}", "yellow")

            if batch_success:
                break   # no need to try fallback model

        if not batch_success:
            ui.log(f"{log_prefix} Batch {batch_num} failed on all models — keeping originals", "bold red")
            for local_i, orig in enumerate(batch):
                results[batch_start + local_i] = orig

    return [r if r is not None else texts[i] for i, r in enumerate(results)]


def translate_with_local_llm(texts, meta_title=None, meta_artist=None):
    """Translate lyric lines using a locally-running Ollama LLM (method "5")
    via the OpenAI-compatible endpoint at LOCAL_LLM_BASE_URL. Runs a tone
    pre-read pass first so every batch's context header reflects the song's
    overall emotional character, not just its own small window of lines.
    """
    if not texts:
        return []

    base_url          = state.CONF.get("LOCAL_LLM_BASE_URL",   "http://localhost:11434/v1").rstrip("/")
    model             = state.CONF.get("LOCAL_LLM_MODEL",      "gemma4:12b")
    _think            = state.CONF.get("LOCAL_LLM_THINK",      False)  # False = suppress reasoning pass
    _batch_size       = state.CONF.get("LOCAL_LLM_BATCH_SIZE", 8)      # lines per translation batch
    _disable_thinking = not _think   # invert: THINK=False → disable_thinking=True

    # Pre-read: get a one-sentence tone summary for the whole song so batch translations share consistent mood context.
    ui.log(
        f"[Local LLM] Assessing song tone "
        f"(thinking={'ON — may take ~2 min' if _think else 'OFF'}, batch_size={_batch_size})...",
        "dim cyan",
    )
    song_tone = _assess_song_tone(
        texts,
        base_url=base_url,
        extra_headers={},
        model=model,
        fuse_system_prompt=True,             # required for Gemma models
        meta_title=meta_title,
        meta_artist=meta_artist,
        timeout=300,
        use_streaming=True,                  # keeps read-timeout alive during any think pass
        keep_alive=600,                      # model stays in VRAM for subsequent batch calls
        disable_thinking=_disable_thinking,  # honours LOCAL_LLM_THINK config key
    )
    if song_tone:
        ui.log(f"[Local LLM] Detected tone: {song_tone[:120]}", "dim cyan")

    return _translate_with_llm(
        texts,
        base_url=base_url,
        extra_headers={},                    # Ollama needs no auth headers
        model_cascade=[model],
        fuse_system_prompt=True,             # required for Gemma / Ollama
        system_prompt=_LOCAL_LLM_SYSTEM_PROMPT,   # tone-faithful prompt; no softening
        max_tokens=None,                     # no cap on local inference
        timeout=300,                         # per-token idle budget; streaming keeps this alive
        batch_size=_batch_size,              # honours LOCAL_LLM_BATCH_SIZE config key
                                             # 8 halves round-trips vs old hardcoded 4;
                                             # safe when thinking is OFF (no repetition collapse)
        inter_batch_delay=0.0,
        retry_base_delay=1,                  # localhost never rate-limits; 1 s/2 s is plenty
        meta_title=meta_title,
        meta_artist=meta_artist,
        song_tone=song_tone,                 # full-song tone anchors every batch
        log_prefix="[Local LLM]",
        use_streaming=True,                  # Required for Gemma 4 / thinking models:
                                             # (1) prevents read-timeout during silent think pass
                                             # (2) temperature must be 1 when thinking is ON
        keep_alive=600,                      # keep model warm between batches (no reload penalty)
        disable_thinking=_disable_thinking,  # honours LOCAL_LLM_THINK config key
    )


# Compiled once at module level for _apply_lrc_offset(); matches both line-level [mm:ss.xx] and word-level <mm:ss.xx> tags.
_LRC_TS_PATTERN = re.compile(
    r'(\[)(\d+):(\d+(?:\.\d+)?)(\])|(<)(\d+):(\d+(?:\.\d+)?)(>)'
)

# ---------- LRC Timestamp Offset ----------
def _apply_lrc_offset(lrc_lines, offset_ms):
    """Shift every LRC/Enhanced-LRC timestamp by *offset_ms* ms (+later/-earlier,
    clamped to 0). Handles both line-level [mm:ss.xx] and word-level <mm:ss.xx>
    tags; metadata tags like [ar:...] are never matched since their second
    field starts with a letter, not a digit.
    """
    if not offset_ms:
        return lrc_lines
    offset_s = offset_ms / 1000.0

    def _shift_any(m):
        if m.group(1):   # square-bracket match groups 1-4
            open_b, mins_s, secs_s, close_b = m.group(1), m.group(2), m.group(3), m.group(4)
        else:             # angle-bracket match groups 5-8
            open_b, mins_s, secs_s, close_b = m.group(5), m.group(6), m.group(7), m.group(8)
        total = int(mins_s) * 60 + float(secs_s) + offset_s
        total = max(0.0, total)
        mins  = int(total // 60)
        secs  = total % 60
        # Guard against float-rounding carry (e.g. 59.9995 → 60.00 after {:05.2f})
        if round(secs, 2) >= 60.0:
            mins += 1
            secs -= 60.0
        secs = max(0.0, secs)
        return f"{open_b}{mins:02d}:{secs:05.2f}{close_b}"

    return [_LRC_TS_PATTERN.sub(_shift_any, line) for line in lrc_lines]


def translate_lrc_file(lrc_path, meta_title=None, meta_artist=None):
    try:
        with open(lrc_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        translated_lines = []
        lrc_data = []

        for line in lines:
            sync_match = re.match(r'^((?:\[\d+:\d+(?:\.\d+)?\])+)(.*)', line)
            meta_match = re.match(r'^\[[a-zA-Z]+:.*\]', line)

            if sync_match and sync_match.group(2).strip():
                raw_content = sync_match.group(2).strip()
                # Safely strip Enhanced LRC word-level tags for translation & string comparisons
                clean_content = re.sub(r'<\d+:\d+(?:\.\d+)?>', '', raw_content)
                lrc_data.append((True, sync_match.group(1), raw_content, clean_content, line))
            elif not meta_match and line.strip() and not re.match(r'^(?:\[\d+:\d+(?:\.\d+)?\])+\s*$', line):
                lrc_data.append((True, "", line.strip(), line.strip(), line))
            else:
                lrc_data.append((False, None, None, None, line))

        texts_to_translate = [item[3] for item in lrc_data if item[0]]
        translated_texts = []

        if texts_to_translate and state.GLOBAL_TRANS_METHOD != "0":
            if state.GLOBAL_TRANS_METHOD == "5":
                # Runs outside translation_lock — manages its own timeouts and
                # would otherwise block behind a method-1/3 worker's 1.5s/line sleep.
                try:
                    translated_texts = translate_with_local_llm(
                        texts_to_translate,
                        meta_title=meta_title,
                        meta_artist=meta_artist
                    )
                    if translated_texts == texts_to_translate:
                        ui.log("[Local LLM] All batches fell back to originals — is Ollama running? (ollama serve)", "bold yellow")
                except Exception as e:
                    ui.log(f"[Local LLM] Unexpected error during translation: {e}", "bold red")
            else:
                time.sleep(1)  # Rate-limit buffer; kept outside the lock so other workers aren't blocked

                if state.GLOBAL_TRANS_METHOD == "1":
                    # Lock acquired/released per line so the rate-limit sleep doesn't block other workers (old design froze all workers for the full sleep duration).
                    for orig in texts_to_translate:
                        success = False
                        src_lang = 'ja' if (state.GLOBAL_AUDIO_LANG == 'ja' or naming.is_mostly_romaji(orig)) else 'auto'
                        try:
                            with state.translation_lock:
                                for attempt in range(3):
                                    trans = None
                                    try:
                                        trans = GoogleTranslator(source=src_lang, target='en').translate(orig)
                                    except Exception:
                                        pass
                                    if trans:
                                        translated_texts.append(trans)
                                        success = True
                                        break
                                    # Google returned empty or raised — try MyMemory as fallback.
                                    try:
                                        mym_src = 'japanese' if src_lang == 'ja' else 'autodetect'
                                        trans = MyMemoryTranslator(source=mym_src, target='english').translate(orig)
                                        if trans:
                                            translated_texts.append(trans)
                                            success = True
                                            break
                                    except Exception:
                                        pass   # MyMemory error — fall through to orig below
                                if not success:
                                    translated_texts.append(orig)
                        except Exception as _trans_exc:
                            if state.CONF.get("DEBUG_MODE"):
                                ui.log(f"Translation method 1 error: {_trans_exc}", "red")
                            if not success:
                                translated_texts.append(orig)
                        # One rate-limit pause per line regardless of retry count; outside the lock so other workers can proceed.
                        time.sleep(1.5)

                elif state.GLOBAL_TRANS_METHOD == "2":
                    # Single batch call — no per-line sleep — lock held only for
                    # the duration of the one network request.
                    with state.translation_lock:
                        try:
                            is_romaji_batch = any(naming.is_mostly_romaji(t) for t in texts_to_translate)
                            # MyMemoryTranslator requires 'autodetect' (not 'auto') and full
                            # language names like 'japanese', not BCP-47 codes like 'ja'.
                            mym_src = 'japanese' if (state.GLOBAL_AUDIO_LANG == 'ja' or is_romaji_batch) else 'autodetect'
                            raw_batch = MyMemoryTranslator(source=mym_src, target='english').translate_batch(texts_to_translate)
                            # translate_batch may return None or a list with None slots on partial
                            # API failure — normalise to originals so callers never see None values.
                            if raw_batch:
                                translated_texts = [
                                    t if t else orig
                                    for t, orig in zip(raw_batch, texts_to_translate)
                                ]
                            else:
                                translated_texts = list(texts_to_translate)
                        except Exception as _trans_exc:
                            if state.CONF.get("DEBUG_MODE"):
                                ui.log(f"Translation method 2 error: {_trans_exc}", "red")

                elif state.GLOBAL_TRANS_METHOD == "3":
                    # Per-line lock release + sleep, same reasoning as method 1.
                    for txt in texts_to_translate:
                        src_lang = 'ja' if (state.GLOBAL_AUDIO_LANG == 'ja' or naming.is_mostly_romaji(txt)) else 'auto'
                        try:
                            with state.translation_lock:
                                result = GoogleTranslator(source=src_lang, target='en').translate(txt)
                                # translate() may return None on a successful but empty API response
                                translated_texts.append(result if result else txt)
                        except Exception:
                            translated_texts.append(txt)
                        # Rate-limit sleep outside the lock so other workers aren't blocked.
                        time.sleep(1.5)

        t_idx = 0
        for is_lyric, timestamps, original, clean_content, raw_line in lrc_data:
            if is_lyric:
                eng = None
                # Always increment t_idx to stay in sync with texts_to_translate, even on partial translation failures, so we never pull the wrong slot.
                if state.GLOBAL_TRANS_METHOD != "0":
                    if t_idx < len(translated_texts):
                        eng = translated_texts[t_idx] or None
                    t_idx += 1

                display_original = original
                time_prefix = timestamps if timestamps else ""

                if eng:
                    orig_clean = re.sub(r'[^\w\s]', '', clean_content).lower().strip()
                    eng_clean = re.sub(r'[^\w\s]', '', str(eng)).lower().strip()

                    if orig_clean != eng_clean and eng_clean:
                        eng_final = str(eng).replace('\n', ' ').replace('\r', ' ').strip()

                        translated_lines.append(f"{time_prefix}{display_original}\n")
                        translated_lines.append(f"{time_prefix}（{eng_final}）\n")
                    else:
                        translated_lines.append(f"{time_prefix}{display_original}\n")
                else:
                    translated_lines.append(f"{time_prefix}{display_original}\n")
            else:
                translated_lines.append(raw_line)

        # Apply timestamp offset if configured.
        offset_ms = state.CONF.get("LRC_OFFSET_MS", 0)
        output_lines = _apply_lrc_offset(translated_lines, offset_ms) if offset_ms else translated_lines

        # Atomic write (temp file + rename) — avoids leaving a zero-byte/partial LRC if the process crashes mid-write.
        lrc_dir = os.path.dirname(lrc_path)
        fd, tmp_path = tempfile.mkstemp(dir=lrc_dir, suffix=".lrc.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.writelines(output_lines)
            os.replace(tmp_path, lrc_path)   # atomic on POSIX; near-atomic on Windows
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    except Exception as e:
        # Always log translation failures — in non-debug mode a short warning
        # is still useful so the user knows the LRC may be incomplete or untranslated.
        ui.log(f"LRC translation/write error (LRC left unchanged): {e}", "yellow"
            if not state.CONF.get("DEBUG_MODE") else "red")
