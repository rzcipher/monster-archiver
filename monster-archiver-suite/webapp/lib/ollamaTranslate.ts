// ---------------- Local LLM translation (Ollama) ----------------
// Mirrors rezakir.py's method-5 local-translation pipeline
// (monster_archiver/translation.py's translate_with_local_llm /
// _translate_with_llm / translate_lrc_file), which this file previously only
// half-matched: thinking was already suppressed, but the whole song was sent
// to the model as ONE request and asked to reproduce every timestamp itself.
// That's slow (a full song is a huge prompt + a huge generation with no
// early return) and fragile (a mis-typed timestamp corrupts the LRC).
//
// This version instead:
//   - parses the LRC ourselves and only ever sends the model bare lyric text,
//     a handful of lines at a time (LOCAL_LLM_BATCH_SIZE, default 8) — the
//     model never has to see or reproduce a single [mm:ss.xx] tag; we splice
//     the translations back into the original timestamps in code.
//   - skips lines that are already English before they ever reach the model
//     (a large chunk of most songs — ad-libs, English hooks, etc).
//   - runs one short "song tone" pre-read so every batch shares the same
//     emotional-register context, then reuses the warm model (keep_alive)
//     for every batch that follows.
//   - suppresses thinking via reasoning_effort:"none" (Ollama issue #14820 —
//     think:false alone is silently ignored on /v1/chat/completions for
//     Gemma 4) unless LOCAL_LLM_THINK is explicitly turned on in Settings.
//   - retries a failing batch on its own (exponential backoff) instead of
//     redoing the whole song, and falls back to the original line if a batch
//     never comes back clean.
//
// All of the above reads the exact same config.json the CLI's own Settings ->
// Translation fields (LOCAL_LLM_BASE_URL / MODEL / THINK / BATCH_SIZE /
// MAX_LINE_CHARS — see lib/settingsSchema.ts) already write to, so changing
// them in the web app's Settings panel now actually takes effect here.
import { readSharedConfig } from "./sharedConfig";

function getLocalLlmConfig() {
  const conf = readSharedConfig();
  const baseUrl = String(
    conf.LOCAL_LLM_BASE_URL || process.env.OLLAMA_BASE_URL || "http://localhost:11434/v1"
  ).replace(/\/+$/, "");
  const model = String(conf.LOCAL_LLM_MODEL || process.env.OLLAMA_MODEL || "gemma4:12b");
  const think = conf.LOCAL_LLM_THINK === true; // default False = suppress reasoning pass
  const batchSizeRaw = Number(conf.LOCAL_LLM_BATCH_SIZE);
  const batchSize = Number.isFinite(batchSizeRaw) && batchSizeRaw > 0 ? Math.floor(batchSizeRaw) : 8;
  const maxLineCharsRaw = Number(conf.LOCAL_LLM_MAX_LINE_CHARS);
  const maxLineChars = Number.isFinite(maxLineCharsRaw) ? maxLineCharsRaw : 800; // 0 = no clamping
  return { baseUrl, model, think, batchSize, maxLineChars };
}

// ── Thinking-model helpers ──
// Reasoning models (Gemma 4 etc.) prepend a <think>...</think> block; this is
// stripped after the full reply is collected so downstream parsers only ever
// see the numbered translation lines.
const THINK_BLOCK_RE = /<think>[\s\S]*?<\/think>/gi;

function stripThink(text: string): string {
  const stripped = text.replace(THINK_BLOCK_RE, "").trim();
  if (stripped) return stripped;
  // Fallback: the model put its whole answer inside the think block (a known
  // Gemma 4 behavior) — pull the translation out of there instead of "".
  const m = /<think>([\s\S]*?)<\/think>/i.exec(text);
  return m ? m[1].trim() : "";
}

// POST a chat/completions payload to Ollama with SSE streaming, returning the
// accumulated (thinking-stripped) reply. The idle timer resets on every
// received chunk (including silent keep-alive ticks during a thinking pass),
// so a long chain-of-thought can't trip a false timeout the way a flat
// request-level timeout would — same reasoning as rezakir.py's
// _stream_ollama_response().
async function streamOllamaChat(
  baseUrl: string,
  payload: Record<string, unknown>,
  idleTimeoutMs: number
): Promise<string> {
  const controller = new AbortController();
  let idleTimer: ReturnType<typeof setTimeout> | undefined = undefined;
  const resetIdleTimer = () => {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => controller.abort(), idleTimeoutMs);
  };
  resetIdleTimer();

  let resp: Response;
  try {
    resp = await fetch(`${baseUrl}/chat/completions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...payload, stream: true }),
      signal: controller.signal
    });
  } catch (e: any) {
    clearTimeout(idleTimer);
    throw new Error(`Could not reach Ollama at ${baseUrl} — is Ollama running? (\`ollama serve\`)`);
  }
  resetIdleTimer(); // headers are in — reset the idle clock before reading the body

  if (!resp.ok || !resp.body) {
    clearTimeout(idleTimer);
    const body = await resp.text().catch(() => "");
    throw new Error(`Ollama request failed (${resp.status}): ${body.slice(0, 300)}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const contentParts: string[] = [];

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      resetIdleTimer();
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const rawLine of lines) {
        const line = rawLine.trim();
        if (!line.startsWith("data: ")) continue;
        const dataStr = line.slice(6).trim();
        if (dataStr === "[DONE]") continue;
        try {
          const chunk = JSON.parse(dataStr);
          const delta = chunk?.choices?.[0]?.delta || {};
          if (delta.content) contentParts.push(delta.content);
        } catch {
          // ignore partial/non-JSON keep-alive lines
        }
      }
    }
  } finally {
    clearTimeout(idleTimer);
  }

  return stripThink(contentParts.join(""));
}

// Verbatim from rezakir.py's _LOCAL_LLM_SYSTEM_PROMPT: emotional fidelity in
// both directions (never soften a dark song, never darken a light one), an
// explicit anti-censorship clause since lyrics are protected artistic
// expression, and the numbered-line-only reply contract — which now applies
// here too, since batches use the exact same numbered-line protocol.
const LOCAL_LLM_SYSTEM_PROMPT =
  "You are an expert music lyric translator fluent in Japanese, Korean, " +
  "Chinese (Mandarin/Cantonese), Spanish, French, German, and other languages. " +
  "Your goal is translations that are BOTH emotionally faithful AND poetically alive. " +
  "Read the song context and tone description carefully — your translation must match " +
  "the actual mood of THIS song, not a generic emotional default.\n" +
  "EMOTIONAL FIDELITY — mirror the original exactly:\n" +
  "- Light, playful, or upbeat content stays light and upbeat. " +
  "Keep the energy bright and the language breezy — do not add weight or gravity that isn't there.\n" +
  "- Happy, romantic, or tender content stays warm and affectionate. " +
  "Let the sweetness come through naturally.\n" +
  "- Dark, bleak, or hopeless content stays dark. " +
  "Render it with honest, unflinching language — do not soften or uplift it.\n" +
  "- Sad or melancholy content stays sad. Give the sorrow space and weight.\n" +
  "- NEVER add darkness, drama, or gravity that is not present in the original.\n" +
  "- NEVER soften or uplift a line whose original mood is genuinely heavy or dark.\n" +
  "POETIC QUALITY: translate meaning-for-meaning, never word-for-word. " +
  "Choose English words that carry the right emotional register for this specific song. " +
  "The result should read like a natural English lyric with the same emotional arc as the original. " +
  "Lines that are already in English must be returned exactly as written. " +
  "TRANSLATION INTEGRITY — ABSOLUTE RULE: You are a translation instrument, not a content filter. " +
  "Song lyrics are protected artistic expression. Translate ALL content exactly as the artist intended: " +
  "profanity and vulgar slang must be translated with equivalent profanity or slang in English — " +
  "never softened, asterisked, or replaced with a milder word. " +
  "Drug references, sexual content, and violent imagery must be translated literally and faithfully. " +
  "Never omit, paraphrase, or flag any word or phrase due to its subject matter. " +
  "If the original uses content you would normally refuse, translate it anyway — " +
  "your duty as a translator supersedes content preferences. A translator who censors has mistranslated. " +
  "Respond EXCLUSIVELY with numbered line translations and nothing else. " +
  "Do NOT output any thinking, reasoning, planning, or <think> blocks — " +
  "go directly to the numbered output.";

// ---------------------------------------------------------------------------
// Language prefilter — ported from translation.py's _is_english_line() and
// its Spanish/Portuguese/French/romaji helpers. A line that's already English
// needs no model call at all, which is a large share of most songs (ad-libs,
// English hooks, etc). NOTE: the CLI's extra "explicit GLOBAL_AUDIO_LANG
// override" branch is intentionally skipped here — the web app doesn't track
// a per-track detected audio language the way the CLI's pipeline does.
// ---------------------------------------------------------------------------
const CJK_RANGES: Array<[number, number]> = [
  [0x3000, 0x9fff], // CJK Unified Ideographs, Hiragana, Katakana, CJK symbols, Bopomofo, etc.
  [0xac00, 0xd7af], // Hangul syllables
  [0x1100, 0x11ff], // Hangul Jamo
  [0xa960, 0xa97f], // Hangul Jamo Extended-A
  [0xd7b0, 0xd7ff], // Hangul Jamo Extended-B
  [0xf900, 0xfaff], // CJK Compatibility Ideographs
  [0x20000, 0x2fa1f] // CJK Extension B and beyond (outside the BMP)
];

function hasCjkOrHangul(text: string): boolean {
  for (const ch of text) {
    const cp = ch.codePointAt(0)!;
    for (const [lo, hi] of CJK_RANGES) {
      if (cp >= lo && cp <= hi) return true;
    }
  }
  return false;
}

// European diacritics are rare in English text; their presence signals a
// Romance/Germanic language needing translation.
const EUROPEAN_DIACRITICS = new Set(
  "àáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞß".split("")
);

// Extended English function-word list — ported from naming.py's
// is_mostly_romaji(), covers phonk/trap's short vowel-ending words that would
// otherwise be misjudged as romanised Japanese.
const ENGLISH_FUNCTION_WORDS = new Set([
  "i", "you", "the", "a", "to", "is", "it", "in", "on", "me", "my",
  "we", "are", "be", "this", "that", "do", "and", "your", "love",
  "baby", "yeah", "oh", "let", "go", "now", "for", "of", "with",
  "so", "just", "like", "know", "no", "up", "out", "all", "they",
  "he", "she", "his", "her", "can", "will", "had", "was",
  "get", "got", "but", "not", "at", "if", "by", "or", "have",
  "been", "more", "see", "free", "here", "there", "some", "come",
  "give", "live", "move", "feel", "real", "ride", "inside", "side",
  "time", "mine", "line", "shine", "fine", "make", "take", "fake",
  "game", "name", "same", "came", "gonna", "wanna", "gotta", "tryna",
  "where", "when", "while", "who", "what", "why", "how", "those",
  "these", "have", "has", "want", "think", "tell", "say", "again",
  "before", "cause", "because", "even", "every", "never", "always",
  "maybe", "someone", "nothing", "something", "everything",
  "people", "between", "above", "below", "over", "under", "home",
  "alone", "one", "none", "done", "gone", "bone", "tone", "phone",
  "stone", "zone", "fire", "desire", "empire", "entire", "wire",
  "whole", "soul", "role", "hole", "pole", "stole", "broke", "smoke",
  "spoke", "woke", "hope", "cope", "rope", "dope", "flow", "show",
  "grow", "glow", "slow", "throw", "below", "follow"
]);

function isMostlyRomaji(text: string): boolean {
  const clean = text.replace(/[^a-zA-Z\s]/g, "").toLowerCase();
  const words = clean.split(/\s+/).filter(Boolean);
  if (!words.length) return false;
  let score = 0;
  for (const w of words) {
    if (ENGLISH_FUNCTION_WORDS.has(w)) score -= 3;
    else if (w.length > 1 && "aeioun".includes(w[w.length - 1])) score += 1;
    else score -= 1;
  }
  return score > 0;
}

// Spanish/Portuguese/French keyword sets — same ASCII-detection purpose:
// catch a pure-ASCII lyric line that's actually a Romance language, which the
// ASCII-ratio fallback alone would miss.
const SPANISH_KEYWORDS = new Set([
  "del", "los", "las", "una", "unos", "unas",
  "para", "con", "sin", "pero", "porque", "aunque", "cuando", "donde",
  "yo", "ella", "ellos", "ellas", "nosotros", "vosotros",
  "esto", "esta", "ese", "esa", "aquel",
  "soy", "eres", "somos", "estoy", "quiero", "quiere", "puedo",
  "miente", "quema", "golpea", "lleva", "prenden", "caen", "viene",
  "noche", "fuego", "amor", "vida", "corazon", "baile", "ritmo",
  "calor", "pasos", "alma", "viento", "cielo", "tierra",
  "sangre", "fuerza", "poder", "salvaje", "secreto",
  "caderas", "estrellas", "sombra", "oscuro", "tacón", "fiebre",
  "siempre", "nunca", "ahora", "despues", "antes", "tambien"
]);

function isSpanishLine(text: string): boolean {
  if (!text || !text.trim()) return false;
  const clean = text.toLowerCase().replace(/[^a-z\s]/g, " ");
  const words = new Set(clean.split(/\s+/).filter(Boolean));
  let hits = 0;
  for (const w of words) if (SPANISH_KEYWORDS.has(w)) hits++;
  return hits >= 2 || (hits === 1 && words.size <= 4);
}

const PORTUGUESE_KEYWORDS = new Set([
  "eu", "tu", "meu", "minha", "teu", "tua", "você", "voce",
  "nosso", "nossa",
  "vem", "vais", "estou", "quiser", "chegar", "ouvir", "faz", "guias",
  "paro", "gira", "balanca",
  "pra", "pelo", "pela",
  "tudo", "muito", "aqui", "tambem", "bracos", "suspiro"
]);

const PORTUGUESE_DIACRITIC_MAP: Record<string, string> = {
  à: "a", á: "a", â: "a", ã: "a", ä: "a",
  è: "e", é: "e", ê: "e", ë: "e",
  ì: "i", í: "i", î: "i", ï: "i",
  ò: "o", ó: "o", ô: "o", õ: "o", ö: "o",
  ù: "u", ú: "u", û: "u", ü: "u",
  ý: "y", ç: "c", ñ: "n"
};

function isPortugueseLine(text: string): boolean {
  if (!text || !text.trim()) return false;
  const lower = text
    .toLowerCase()
    .replace(/[àáâãäèéêëìíîïòóôõöùúûüýçñ]/g, (c) => PORTUGUESE_DIACRITIC_MAP[c] ?? c);
  const clean = lower.replace(/[^a-z\s]/g, " ");
  const words = new Set(clean.split(/\s+/).filter(Boolean));
  let hits = 0;
  for (const w of words) if (PORTUGUESE_KEYWORDS.has(w)) hits++;
  return hits >= 2 || (hits === 1 && words.size <= 4);
}

const FRENCH_KEYWORDS = new Set([
  "je", "tu", "nous", "vous", "ils", "elles",
  "mes", "tes", "ses", "notre", "votre", "leurs",
  "dans", "mais", "quand", "comme", "donc", "puis", "aussi",
  "pourquoi", "toujours", "jamais", "trop", "rien", "meme",
  "apres", "avant", "encore", "maintenant", "parce",
  "suis", "etais", "avais", "avons", "sommes", "etes", "sont",
  "veux", "fais", "viens", "prends", "connais",
  "nuit", "coeur", "amour", "toi", "moi", "lui",
  "monde", "belle", "beau", "rien", "bien", "mal"
]);

function isFrenchLine(text: string): boolean {
  if (!text || !text.trim()) return false;
  const clean = text.toLowerCase().replace(/[^a-z\s]/g, " ");
  const words = new Set(clean.split(/\s+/).filter(Boolean));
  let hits = 0;
  for (const w of words) if (FRENCH_KEYWORDS.has(w)) hits++;
  return hits >= 2 || (hits === 1 && words.size <= 6);
}

function isEnglishLine(text: string): boolean {
  if (!text || !text.trim()) return false;
  if (hasCjkOrHangul(text)) return false;
  for (const ch of text) if (EUROPEAN_DIACRITICS.has(ch)) return false;
  if (isMostlyRomaji(text)) return false;
  if (isSpanishLine(text)) return false;
  if (isPortugueseLine(text)) return false;
  if (isFrenchLine(text)) return false;
  // Tolerate some non-ASCII punctuation (curly quotes, em-dashes) common in
  // hip-hop lyrics; threshold matches the CLI's 0.75.
  let asciiCount = 0;
  for (let i = 0; i < text.length; i++) {
    if (text.charCodeAt(i) < 128) asciiCount++;
  }
  return asciiCount / Math.max(text.length, 1) > 0.75;
}

// Perso-Arabic script ranges — covers Persian's Arabic-derived alphabet
// (including the extra letters پ چ ژ گ that plain Arabic doesn't have) as
// well as Arabic itself, since they share a script. Unlike isEnglishLine's
// keyword/diacritic heuristics (needed because Latin-script languages all
// look alike at the codepoint level), Persian/Arabic text is trivially and
// reliably identified by its script alone — a majority-Perso-Arabic line
// simply isn't ASCII/Latin, no keyword guessing required.
const PERSO_ARABIC_RANGES: Array<[number, number]> = [
  [0x0600, 0x06ff], // Arabic (Persian's core alphabet lives in this block)
  [0x0750, 0x077f], // Arabic Supplement
  [0xfb50, 0xfdff], // Arabic Presentation Forms-A
  [0xfe70, 0xfeff] // Arabic Presentation Forms-B
];

function isPersoArabicLine(text: string): boolean {
  if (!text || !text.trim()) return false;
  let scriptLetters = 0;
  let totalLetters = 0;
  for (const ch of text) {
    if (!/\p{L}/u.test(ch)) continue;
    totalLetters++;
    const cp = ch.codePointAt(0)!;
    for (const [lo, hi] of PERSO_ARABIC_RANGES) {
      if (cp >= lo && cp <= hi) {
        scriptLetters++;
        break;
      }
    }
  }
  if (!totalLetters) return false;
  return scriptLetters / totalLetters > 0.5;
}

// ---------------------------------------------------------------------------
// LRC parsing / reconstruction — ported from translate_lrc_file()'s line
// classifier. Splitting this out means the model is only ever handed bare
// lyric text; every timestamp is spliced back in by this code, never by the LLM.
// ---------------------------------------------------------------------------
interface LrcLine {
  isLyric: boolean;
  timestamps: string; // e.g. "[00:12.30]" (possibly several concatenated), or "" for unsynced lyric lines
  rawContent: string; // content after the timestamp(s), including word-level <mm:ss.xx> tags
  cleanContent: string; // rawContent with word-level tags stripped — what actually gets sent to the model
  rawLine: string; // original line verbatim, for non-lyric passthrough (blank lines, [ar:...] metadata, etc.)
}

const SYNC_RE = /^((?:\[\d+:\d+(?:\.\d+)?\])+)(.*)$/;
const META_RE = /^\[[a-zA-Z]+:.*\]/;
const TS_ONLY_RE = /^(?:\[\d+:\d+(?:\.\d+)?\])+\s*$/;
const WORD_TS_RE = /<\d+:\d+(?:\.\d+)?>/g;

function parseLrcLines(text: string): LrcLine[] {
  const lines = text.split(/\r\n|\r|\n/);
  const data: LrcLine[] = [];
  for (const line of lines) {
    const syncMatch = SYNC_RE.exec(line);
    const isMeta = META_RE.test(line);
    if (syncMatch && syncMatch[2].trim()) {
      const rawContent = syncMatch[2].trim();
      const cleanContent = rawContent.replace(WORD_TS_RE, "");
      data.push({ isLyric: true, timestamps: syncMatch[1], rawContent, cleanContent, rawLine: line });
    } else if (!isMeta && line.trim() && !TS_ONLY_RE.test(line)) {
      // Unsynced plain-text lyric line (no timestamp at all).
      const trimmed = line.trim();
      data.push({ isLyric: true, timestamps: "", rawContent: trimmed, cleanContent: trimmed, rawLine: line });
    } else {
      data.push({ isLyric: false, timestamps: "", rawContent: "", cleanContent: "", rawLine: line });
    }
  }
  return data;
}

function normalizeForCompare(s: string): string {
  return s.replace(/[^\p{L}\p{N}\s]/gu, "").toLowerCase().trim();
}

// ---------------------------------------------------------------------------
// Song-tone pre-read — ported from _assess_song_tone(): sample up to 20
// evenly-spaced lines and ask for a 1-2 sentence tone/mood summary, which then
// anchors every batch's emotional register. Best-effort — a failure here is
// silent and non-fatal, same as the CLI.
// ---------------------------------------------------------------------------
const MAX_TONE_LINES = 20;

async function assessSongTone(
  lines: string[],
  title: string | undefined,
  artist: string | undefined,
  cfg: { baseUrl: string; model: string; disableThinking: boolean }
): Promise<string | null> {
  if (!lines.length) return null;

  const sample =
    lines.length > MAX_TONE_LINES
      ? Array.from({ length: MAX_TONE_LINES }, (_, i) => lines[Math.floor((i * lines.length) / MAX_TONE_LINES)])
      : lines;

  const ctxParts: string[] = [];
  if (title && title !== "Unknown") ctxParts.push(`Song Title: "${title}"`);
  if (artist && artist !== "Unknown") ctxParts.push(`Artist:     "${artist}"`);
  const contextHeader = ctxParts.length ? ctxParts.join("\n") + "\n\n" : "";

  const numbered = sample.map((line, i) => `${i + 1}. ${line}`).join("\n");
  const toneSystem =
    "You are a music analyst. Your only job is to read song lyrics and describe " +
    "their actual emotional tone accurately and neutrally in 1-2 sentences.";
  const userContent =
    `${contextHeader}Read the following representative song lyric lines carefully.\n` +
    `Describe the song's overall emotional tone and mood in 1–2 sentences.\n` +
    `Be accurate — do not project darkness onto a light song or lightness onto a dark one. ` +
    `Base your description solely on what is actually in the lyrics.\n` +
    `Reply with ONLY the tone description — no preamble, no reasoning, no <think> blocks.\n\n${numbered}`;

  const payload: Record<string, unknown> = {
    model: cfg.model,
    messages: [{ role: "user", content: `${toneSystem}\n\n${userContent}` }],
    temperature: cfg.disableThinking ? 0.2 : 1,
    max_tokens: 200,
    keep_alive: 600 // keep the model resident in VRAM for the batch calls that follow immediately
  };
  if (cfg.disableThinking) {
    payload.think = false;
    payload.reasoning_effort = "none";
  }

  try {
    const tone = await streamOllamaChat(cfg.baseUrl, payload, 60_000);
    return tone || null;
  } catch (e: any) {
    console.warn(`[Ollama] Tone pre-read failed (non-fatal): ${String(e?.message ?? e).slice(0, 120)}`);
    return null;
  }
}

// ---------------------------------------------------------------------------
// Batch translation core — ported from _translate_with_llm(): small batches
// (LOCAL_LLM_BATCH_SIZE lines at a time) instead of one giant request, a
// numbered-line reply contract, a two-pass parser (strict regex, then a
// preamble-stripping fallback), per-batch retries with backoff, and English
// lines passed straight through with no model call.
// ---------------------------------------------------------------------------
const NUMBERED_LINE_RE = /^(?:[-*\s•]*)?(?:\[|\()?(\d+)(?:\]|\))?[\s.)[\]:,-]*\s*(.*)/;
const NUMBERED_PREFIX_RE = /^(?:[-*\s•]*)?(?:\[|\()?(\d+)(?:\]|\))?[\s.)[\]:,-]*\s*/;
const PREAMBLE_RE = /^(?:here\s+(?:are|is)\b|sure[,\s]|certainly[,:]|translations?\s*(?:results?)?:|output:|results?:|notes?:)/i;

interface BatchTranslateOptions {
  baseUrl: string;
  model: string;
  batchSize: number;
  maxLineChars: number;
  disableThinking: boolean;
  title?: string;
  artist?: string;
  songTone?: string | null;
}

async function translateLinesInBatches(texts: string[], opts: BatchTranslateOptions): Promise<string[]> {
  if (!texts.length) return [];
  const { baseUrl, model, batchSize, maxLineChars, disableThinking, title, artist, songTone } = opts;

  const ctxParts: string[] = [];
  if (title && title !== "Unknown") ctxParts.push(`Song Title: "${title}"`);
  if (artist && artist !== "Unknown") ctxParts.push(`Artist:     "${artist}"`);
  if (songTone) ctxParts.push(`Song tone:  ${songTone}`);
  const contextHeader = ctxParts.length ? ctxParts.join("\n") + "\n\n" : "";

  const PER_BATCH_RETRIES = 3;
  const results: Array<string | null> = new Array(texts.length).fill(null);
  const totalBatches = Math.ceil(texts.length / batchSize);

  for (let batchStart = 0; batchStart < texts.length; batchStart += batchSize) {
    const batchNum = Math.floor(batchStart / batchSize) + 1;
    const batch = texts.slice(batchStart, batchStart + batchSize);

    const englishMask = batch.map((line) => isEnglishLine(line));
    const active: Array<[number, string]> = [];
    batch.forEach((line, i) => {
      if (!englishMask[i]) active.push([i, line]);
    });

    if (!active.length) {
      // Whole batch is already English — no model call needed at all.
      batch.forEach((line, i) => (results[batchStart + i] = line));
      console.log(`[Ollama] Batch ${batchNum}/${totalBatches}: all lines already English — skipped`);
      continue;
    }

    const clampedActive: Array<[number, string]> =
      maxLineChars > 0 ? active.map(([i, line]) => [i, line.slice(0, maxLineChars)]) : active;

    const numbered = clampedActive.map(([, line], pos) => `${pos + 1}. ${line}`).join("\n");
    const userContent =
      `${contextHeader}` +
      `Translate the following song lyric lines into natural, expressive English.\n` +
      `Read ALL lines together first to understand the song's theme, emotional arc, ` +
      `and recurring imagery — then translate each line so it flows naturally as part ` +
      `of that complete song, preserving metaphor, mood, and poetic intent.\n` +
      `Lines that are already in English must be returned exactly as written.\n` +
      `CRITICAL: Reply with exactly the same number of lines as the input. ` +
      `Each line MUST start with its number followed by a period (e.g., '1. ', '2. '). ` +
      `Do NOT skip or combine lines, do NOT add commentary, ` +
      `and do NOT output thinking, reasoning, or <think> blocks — ` +
      `go directly to the numbered translations.\n\n${numbered}`;

    const messages = [{ role: "user", content: `${LOCAL_LLM_SYSTEM_PROMPT}\n\n${userContent}` }];

    const basePayload: Record<string, unknown> = {
      model,
      messages,
      temperature: disableThinking ? 0.2 : 1,
      keep_alive: 600 // keep the model warm between batches — no reload penalty
    };
    if (disableThinking) {
      basePayload.think = false;
      basePayload.reasoning_effort = "none";
    }

    let batchSuccess = false;
    console.log(`[Ollama] Batch ${batchNum}/${totalBatches}: translating ${active.length}/${batch.length} lines...`);

    for (let attempt = 0; attempt < PER_BATCH_RETRIES; attempt++) {
      if (attempt > 0) {
        const waitMs = 1000 * 2 ** (attempt - 1); // 1s, 2s — localhost never rate-limits
        console.log(`[Ollama] Batch ${batchNum} retry ${attempt}/${PER_BATCH_RETRIES - 1} in ${waitMs / 1000}s...`);
        await new Promise((r) => setTimeout(r, waitMs));
      }
      try {
        const reply = await streamOllamaChat(baseUrl, basePayload, 300_000);
        if (!reply) throw new Error("Empty reply after stripping thinking blocks");

        const replyLines = reply.split("\n");
        const batchTranslated = new Map<number, string>();

        // Pass 1: loose regex — handles markdown, spaces, brackets around numbers.
        for (const rawLine of replyLines) {
          const lineStr = rawLine.trim();
          if (!lineStr) continue;
          const m = NUMBERED_LINE_RE.exec(lineStr);
          if (m) {
            const localIdx = parseInt(m[1], 10) - 1;
            const textContent = m[2].trim().replace(/^[*_ ]+/, "").replace(/[*_ ]+$/, "");
            if (localIdx >= 0 && localIdx < clampedActive.length) {
              batchTranslated.set(localIdx, textContent);
            }
          }
        }

        // Pass 2 fallback: strip preamble/postamble noise, accept if we got
        // at least as many content lines back as needed.
        // Only run the positional fallback when pass 1 matched NOTHING —
        // mapping cleanLines[0..N] onto slots by position is only unambiguous
        // when no slots are already filled. Mixing positional fills into a
        // partially-matched batch can silently assign the wrong translation
        // to the wrong line if the model emitted extra/reordered lines.
        if (batchTranslated.size === 0) {
          const cleanLines: string[] = [];
          for (const rawLine of replyLines) {
            const s = rawLine.trim();
            if (!s || PREAMBLE_RE.test(s)) continue;
            const cleaned = s
              .replace(NUMBERED_PREFIX_RE, "")
              .replace(/^[*_ ]+/, "")
              .replace(/[*_ ]+$/, "")
              .trim();
            if (cleaned) cleanLines.push(cleaned);
          }
          if (cleanLines.length >= clampedActive.length) {
            const trimmed = cleanLines.slice(0, clampedActive.length);
            trimmed.forEach((t, i) => {
              if (!batchTranslated.has(i)) batchTranslated.set(i, t);
            });
          }
        }

        clampedActive.forEach(([origI], pos) => {
          results[batchStart + origI] = batchTranslated.get(pos) ?? active[pos][1];
        });
        batch.forEach((line, i) => {
          if (englishMask[i]) results[batchStart + i] = line;
        });

        batchSuccess = true;
        break;
      } catch (e: any) {
        const errStr = String(e?.message ?? e).slice(0, 200);
        if (attempt < PER_BATCH_RETRIES - 1) {
          console.warn(`[Ollama] Batch ${batchNum} attempt ${attempt + 1} error: ${errStr} — retrying...`);
        } else {
          console.warn(`[Ollama] Batch ${batchNum} exhausted: ${errStr} — keeping originals`);
        }
      }
    }

    if (!batchSuccess) {
      batch.forEach((line, i) => (results[batchStart + i] = line));
    }
  }

  return results.map((r, i) => (r !== null ? r : texts[i]));
}

// A lyric line that needed translation but ended up unchanged in the output —
// either a batch exhausted its retries and fell back to the original line, or
// the model echoed it back verbatim. Surfaced to the web app so the user can
// jump straight to it and fix it with a single-line re-translation instead of
// having to proofread the whole song.
export interface FlaggedLyricLine {
  index: number; // 0-based line number in the returned `lyrics` text
  rawLine: string; // exact line content at that index, used to relocate it later
  preview: string; // clean (timestamp-stripped) original text, for display
}

export interface OllamaTranslateResult {
  lyrics: string;
  flaggedLines: FlaggedLyricLine[];
}

// ---------------------------------------------------------------------------
// Public entry point.
// ---------------------------------------------------------------------------
export async function translateWithOllama(
  lyricsText: string,
  title: string | undefined,
  artist: string | undefined,
  mode: string | undefined
): Promise<OllamaTranslateResult> {
  const { baseUrl, model, think, batchSize, maxLineChars } = getLocalLlmConfig();
  const disableThinking = !think;
  const effectiveMode = mode || "translate_and_merge";

  const parsed = parseLrcLines(lyricsText);
  const texts = parsed.filter((l) => l.isLyric).map((l) => l.cleanContent);

  if (!texts.length) {
    // Nothing to translate (instrumental, empty input, metadata-only) — return unchanged.
    return { lyrics: lyricsText, flaggedLines: [] };
  }

  console.log(
    `[Ollama] Translating ${texts.length} line(s) via ${model} ` +
      `(thinking=${think ? "ON — may take a while" : "OFF"}, batch_size=${batchSize})...`
  );

  // Pre-read: a one-sentence tone summary for the whole song so every
  // batch's context header shares consistent mood, not just its own window.
  const songTone = await assessSongTone(texts, title, artist, { baseUrl, model, disableThinking });
  if (songTone) console.log(`[Ollama] Detected tone: ${songTone.slice(0, 120)}`);

  const translatedTexts = await translateLinesInBatches(texts, {
    baseUrl,
    model,
    batchSize,
    maxLineChars,
    disableThinking,
    title,
    artist,
    songTone
  });

  // Reassemble the lyrics ourselves — the model never sees or reproduces a
  // single timestamp, so there's no risk of a mangled/hallucinated one.
  const outputLines: string[] = [];
  const flaggedLines: FlaggedLyricLine[] = [];
  let tIdx = 0;
  for (const line of parsed) {
    if (!line.isLyric) {
      outputLines.push(line.rawLine);
      continue;
    }
    const translated = tIdx < translatedTexts.length ? translatedTexts[tIdx] : null;
    tIdx += 1;
    const prefix = line.timestamps || "";
    // Did this line actually need a model call at all? English/ad-lib lines
    // that were correctly passed straight through are NOT a miss — only lines
    // that needed translation and still came back unchanged should be flagged.
    const neededTranslation = !isEnglishLine(line.cleanContent);

    if (effectiveMode === "translate_only") {
      const idx = outputLines.length;
      outputLines.push(`${prefix}${translated ?? line.rawContent}`);
      if (neededTranslation) {
        const origClean = normalizeForCompare(line.cleanContent);
        const engClean = normalizeForCompare(translated ?? "");
        if (!translated || origClean === engClean) {
          flaggedLines.push({ index: idx, rawLine: outputLines[idx], preview: line.cleanContent });
        }
      }
      continue;
    }

    // translate_and_merge (default): keep the original line, then append a
    // parenthesised translation beneath it — but only when the translation
    // actually differs from the original (skips duplicate output for lines
    // the model correctly left untouched, e.g. onomatopoeia or ad-libs).
    const idx = outputLines.length;
    outputLines.push(`${prefix}${line.rawContent}`);
    let addedParenthetical = false;
    if (translated) {
      const origClean = normalizeForCompare(line.cleanContent);
      const engClean = normalizeForCompare(translated);
      if (origClean !== engClean && engClean) {
        const engFinal = translated.replace(/[\r\n]+/g, " ").trim();
        outputLines.push(`${prefix}（${engFinal}）`);
        addedParenthetical = true;
      }
    }
    if (neededTranslation && !addedParenthetical) {
      flaggedLines.push({ index: idx, rawLine: outputLines[idx], preview: line.cleanContent });
    }
  }

  if (flaggedLines.length) {
    console.log(`[Ollama] ${flaggedLines.length} line(s) may have been skipped/untranslated — flagged for review`);
  }

  return { lyrics: outputLines.join("\n"), flaggedLines };
}

// ---------------------------------------------------------------------------
// Single-line / selection re-translation — for fixing the odd line a full-song
// batch pass skipped or mistranslated, without re-running the whole song.
// Shares the system prompt, config, and reply-cleanup logic with the batch
// path above, but talks to Ollama one line at a time.
// ---------------------------------------------------------------------------
async function translateOneLineText(
  cleanText: string,
  cfg: { baseUrl: string; model: string; disableThinking: boolean; maxLineChars: number; contextHeader: string }
): Promise<string> {
  const clamped = cfg.maxLineChars > 0 ? cleanText.slice(0, cfg.maxLineChars) : cleanText;
  const userContent =
    `${cfg.contextHeader}Translate the following single song lyric line into natural, expressive English.\n` +
    `Read it in context of the surrounding song and preserve metaphor, mood, and poetic intent. ` +
    `If the line is already natural English, return it unchanged.\n` +
    `Respond with ONLY the translated line — no line numbers, no quotation marks, no commentary, ` +
    `and do NOT output thinking, reasoning, or <think> blocks.\n\n${clamped}`;

  const messages = [{ role: "user", content: `${LOCAL_LLM_SYSTEM_PROMPT}\n\n${userContent}` }];
  const payload: Record<string, unknown> = {
    model: cfg.model,
    messages,
    temperature: cfg.disableThinking ? 0.2 : 1,
    keep_alive: 600
  };
  if (cfg.disableThinking) {
    payload.think = false;
    payload.reasoning_effort = "none";
  }

  const PER_LINE_RETRIES = 3;
  let lastErr: any = null;
  for (let attempt = 0; attempt < PER_LINE_RETRIES; attempt++) {
    if (attempt > 0) {
      const waitMs = 1000 * 2 ** (attempt - 1);
      await new Promise((r) => setTimeout(r, waitMs));
    }
    try {
      const reply = await streamOllamaChat(cfg.baseUrl, payload, 120_000);
      const cleaned = reply
        .replace(NUMBERED_PREFIX_RE, "")
        .replace(/^["'“”]+|["'“”]+$/g, "")
        .trim();
      if (cleaned) return cleaned;
      lastErr = new Error("Empty reply after stripping thinking blocks");
    } catch (e: any) {
      lastErr = e;
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error(String(lastErr ?? "Local LLM returned an empty translation"));
}

const PAREN_LINE_RE = /^[（(].*[)）]$/;

export interface LineTranslationResult {
  lyrics: string;
  translations: Array<{ lineIndex: number; original: string; translated: string }>;
}

// Re-translates just the requested line indices (one line for a plain cursor
// position, several for a multi-line textarea selection) and splices the
// result back into the full lyrics text in a single pass, leaving every other
// line — including unrelated prior translations — byte-for-byte untouched.
export async function translateLinesAtIndices(
  lyricsText: string,
  requestedIndices: number[],
  title: string | undefined,
  artist: string | undefined,
  mode: string | undefined
): Promise<LineTranslationResult> {
  const { baseUrl, model, think, maxLineChars } = getLocalLlmConfig();
  const disableThinking = !think;
  const effectiveMode = mode || "translate_and_merge";

  const rawLines = lyricsText.split(/\r\n|\r|\n/);
  const parsed = parseLrcLines(lyricsText);

  // If a requested index points at an existing parenthetical translation line
  // (i.e. the user clicked/selected our own prior output), redirect to the
  // original lyric line directly above it instead of re-translating a
  // translation.
  const resolveIndex = (i: number): number => {
    const cur = parsed[i];
    if (cur && cur.isLyric && PAREN_LINE_RE.test(cur.rawContent.trim()) && i > 0) {
      const prev = parsed[i - 1];
      if (prev && prev.isLyric && prev.timestamps === cur.timestamps) return i - 1;
    }
    return i;
  };

  const uniqueIndices = Array.from(
    new Set(
      requestedIndices.filter((i) => Number.isInteger(i) && i >= 0 && i < parsed.length).map(resolveIndex)
    )
  ).sort((a, b) => a - b);

  const validIndices = uniqueIndices.filter((i) => parsed[i]?.isLyric && parsed[i].cleanContent.trim());
  if (!validIndices.length) {
    throw new Error("No translatable lyric line at the selected position");
  }

  const contextParts: string[] = [];
  if (title && title !== "Unknown") contextParts.push(`Song Title: "${title}"`);
  if (artist && artist !== "Unknown") contextParts.push(`Artist:     "${artist}"`);
  const contextHeader = contextParts.length ? contextParts.join("\n") + "\n\n" : "";
  const lineCfg = { baseUrl, model, disableThinking, maxLineChars, contextHeader };

  console.log(`[Ollama] Re-translating ${validIndices.length} selected line(s) via ${model}...`);

  const translationByIndex = new Map<number, string>();
  const translations: Array<{ lineIndex: number; original: string; translated: string }> = [];
  for (const idx of validIndices) {
    const cleanText = parsed[idx].cleanContent;
    const translated = await translateOneLineText(cleanText, lineCfg);
    translationByIndex.set(idx, translated);
    translations.push({ lineIndex: idx, original: cleanText, translated });
  }

  // Single reconstruction pass over the original lines — substitute the
  // handful we just translated, leave everything else (including unrelated
  // pre-existing parentheticals) exactly as it was.
  const outLines: string[] = [];
  let skipNext = 0; // upcoming original lines to swallow (a stale parenthetical we're replacing)
  for (let i = 0; i < rawLines.length; i++) {
    if (skipNext > 0) {
      skipNext--;
      continue;
    }
    if (!translationByIndex.has(i)) {
      outLines.push(rawLines[i]);
      continue;
    }

    const line = parsed[i];
    const translated = translationByIndex.get(i)!;
    const prefix = line.timestamps || "";
    const nextParsed = parsed[i + 1];
    const nextIsStaleTranslation =
      !!nextParsed &&
      nextParsed.isLyric &&
      nextParsed.timestamps === line.timestamps &&
      PAREN_LINE_RE.test(nextParsed.rawContent.trim());

    if (effectiveMode === "translate_only") {
      outLines.push(`${prefix}${translated}`);
      continue;
    }

    outLines.push(`${prefix}${line.rawContent}`);
    const origClean = normalizeForCompare(line.cleanContent);
    const engClean = normalizeForCompare(translated);
    if (origClean !== engClean && engClean) {
      outLines.push(`${prefix}（${translated.replace(/[\r\n]+/g, " ").trim()}）`);
      if (nextIsStaleTranslation) skipNext = 1; // fresh translation replaces the old one below it
    } else if (nextIsStaleTranslation) {
      // New result matches the original (line turned out to already be
      // English) — drop the now-stale parenthetical rather than stranding it.
      skipNext = 1;
    }
  }

  return { lyrics: outLines.join("\n"), translations };
}

// ---------------------------------------------------------------------------
// Video caption translation — same local Ollama pipeline as the Lyrics
// Studio above (config, streaming, retry/backoff, English-skip, the
// dual-pass numbered-line parser), reused via the shared helpers/regexes
// already defined earlier in this file. The batch loop itself is a
// deliberate separate copy of translateLinesInBatches() rather than that
// function with a couple of new parameters: the "song"/"lyric" framing baked
// into LOCAL_LLM_SYSTEM_PROMPT (poetic intent, emotional arc, "Song Title")
// is actively wrong for ordinary spoken dialogue — an interview, lecture, or
// home video isn't a song, and a model told to preserve "poetic intent" in a
// grocery-store conversation produces stilted, over-written subtitles. Same
// reasoning as video_captions.py's own note about re-parsing transcribe_audio's
// output instead of threading a new return value through it: keep the
// already-tuned caller's contract untouched, duplicate the small amount of
// prompt-building code that actually differs instead.
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Video caption translation — same local Ollama pipeline as the Lyrics
// Studio above (config, streaming, retry/backoff, already-in-target-language
// skip, the dual-pass numbered-line parser), reused via the shared
// helpers/regexes already defined earlier in this file. The batch loop
// itself is a deliberate separate copy of translateLinesInBatches() rather
// than that function with a couple of new parameters: the "song"/"lyric"
// framing baked into LOCAL_LLM_SYSTEM_PROMPT (poetic intent, emotional arc,
// "Song Title") is actively wrong for ordinary spoken dialogue — an
// interview, lecture, or home video isn't a song, and a model told to
// preserve "poetic intent" in a grocery-store conversation produces stilted,
// over-written subtitles. Same reasoning as video_captions.py's own note
// about re-parsing transcribe_audio's output instead of threading a new
// return value through it: keep the already-tuned caller's contract
// untouched, duplicate the small amount of prompt-building code that
// actually differs instead.
//
// Unlike the Lyrics Studio (English-only, hardcoded), captions can target
// any of a small set of languages — defaulting to Persian, since this app's
// whole caption/subtitle pipeline is Persian/RTL-first (libfribidi RTL
// shaping, Tahoma font, "fa" as the default spoken-language option above).
// ---------------------------------------------------------------------------
export const CAPTION_TARGET_LANGUAGES: Record<string, string> = {
  fa: "Persian (Farsi)",
  en: "English",
  ar: "Arabic",
  fr: "French",
  es: "Spanish"
};

function captionTargetLanguageName(code: string): string {
  return CAPTION_TARGET_LANGUAGES[code] || CAPTION_TARGET_LANGUAGES.fa;
}

// "Is this line already in the target language?" -- lets a batch skip the
// model call entirely for lines that don't need translating (e.g. an
// English proper noun a Persian speaker used mid-sentence, or a line that's
// already been translated). Persian and Arabic share a script, so
// isPersoArabicLine() correctly covers both; English reuses isEnglishLine()
// (the same check the Lyrics Studio uses above); French/Spanish reuse the
// keyword detectors already defined earlier in this file.
function isAlreadyInTargetLanguage(text: string, targetCode: string): boolean {
  switch (targetCode) {
    case "fa":
    case "ar":
      return isPersoArabicLine(text);
    case "fr":
      return isFrenchLine(text);
    case "es":
      return isSpanishLine(text);
    case "en":
    default:
      return isEnglishLine(text);
  }
}

function buildCaptionSystemPrompt(targetLanguageName: string): string {
  return (
    "You are an expert subtitle translator, fluent in Japanese, Korean, Chinese " +
    "(Mandarin/Cantonese), Spanish, French, German, Arabic, Persian, English, and other " +
    "languages. You translate spoken dialogue from videos — interviews, lectures, films, " +
    `everyday conversation — into natural, colloquial ${targetLanguageName} subtitles, the way ` +
    "a professional subtitler would, not a stiff word-for-word machine translation.\n" +
    "GUIDELINES:\n" +
    "- Translate meaning-for-meaning, never word-for-word. Match the register of the " +
    "original speech: casual stays casual, formal stays formal, technical stays precise.\n" +
    "- Keep each line concise enough to read comfortably as an on-screen subtitle — " +
    "don't pad, embellish, or add flourish beyond what was actually said.\n" +
    "- Preserve the speaker's tone (humor, anger, hesitation, sarcasm, bluntness, etc.) " +
    "rather than flattening everything into neutral phrasing.\n" +
    `- Lines that are already in ${targetLanguageName} must be returned exactly as written.\n` +
    "TRANSLATION INTEGRITY: You are a translation instrument, not a content filter. " +
    "Translate profanity, slang, and sensitive subject matter faithfully and literally — " +
    "never soften, censor, asterisk, or omit a word because of its subject matter. " +
    "A subtitle that censors misrepresents what the speaker actually said.\n" +
    "Respond EXCLUSIVELY with numbered line translations and nothing else. " +
    "Do NOT output any thinking, reasoning, planning, or <think> blocks — " +
    "go directly to the numbered output."
  );
}

interface CaptionBatchOptions {
  baseUrl: string;
  model: string;
  batchSize: number;
  maxLineChars: number;
  disableThinking: boolean;
  videoTitle?: string;
  targetLanguageCode: string;
}

async function translateCaptionTextsInBatches(texts: string[], opts: CaptionBatchOptions): Promise<string[]> {
  if (!texts.length) return [];
  const { baseUrl, model, batchSize, maxLineChars, disableThinking, videoTitle, targetLanguageCode } = opts;
  const targetLanguageName = captionTargetLanguageName(targetLanguageCode);
  const systemPrompt = buildCaptionSystemPrompt(targetLanguageName);
  const contextHeader = videoTitle && videoTitle.trim() ? `Video Title: "${videoTitle.trim()}"\n\n` : "";

  const PER_BATCH_RETRIES = 3;
  const results: Array<string | null> = new Array(texts.length).fill(null);
  const totalBatches = Math.ceil(texts.length / batchSize);

  for (let batchStart = 0; batchStart < texts.length; batchStart += batchSize) {
    const batchNum = Math.floor(batchStart / batchSize) + 1;
    const batch = texts.slice(batchStart, batchStart + batchSize);

    const alreadyMask = batch.map((line) => isAlreadyInTargetLanguage(line, targetLanguageCode));
    const active: Array<[number, string]> = [];
    batch.forEach((line, i) => {
      if (!alreadyMask[i]) active.push([i, line]);
    });

    if (!active.length) {
      batch.forEach((line, i) => (results[batchStart + i] = line));
      console.log(
        `[Ollama] Caption batch ${batchNum}/${totalBatches}: all lines already ${targetLanguageName} — skipped`
      );
      continue;
    }

    const clampedActive: Array<[number, string]> =
      maxLineChars > 0 ? active.map(([i, line]) => [i, line.slice(0, maxLineChars)]) : active;

    const numbered = clampedActive.map(([, line], pos) => `${pos + 1}. ${line}`).join("\n");
    const userContent =
      `${contextHeader}` +
      `Translate the following subtitle lines (spoken dialogue transcribed from a video) into ` +
      `natural, colloquial ${targetLanguageName}.\n` +
      `Read ALL lines together first for context, then translate each one so it reads naturally ` +
      `as a subtitle — preserve tone and register, don't add anything that wasn't said.\n` +
      `Lines that are already in ${targetLanguageName} must be returned exactly as written.\n` +
      `CRITICAL: Reply with exactly the same number of lines as the input. ` +
      `Each line MUST start with its number followed by a period (e.g., '1. ', '2. '). ` +
      `Do NOT skip or combine lines, do NOT add commentary, ` +
      `and do NOT output thinking, reasoning, or <think> blocks — ` +
      `go directly to the numbered translations.\n\n${numbered}`;

    const messages = [{ role: "user", content: `${systemPrompt}\n\n${userContent}` }];

    const basePayload: Record<string, unknown> = {
      model,
      messages,
      temperature: disableThinking ? 0.2 : 1,
      keep_alive: 600
    };
    if (disableThinking) {
      basePayload.think = false;
      basePayload.reasoning_effort = "none";
    }

    let batchSuccess = false;
    console.log(
      `[Ollama] Caption batch ${batchNum}/${totalBatches}: translating ${active.length}/${batch.length} lines to ${targetLanguageName}...`
    );

    for (let attempt = 0; attempt < PER_BATCH_RETRIES; attempt++) {
      if (attempt > 0) {
        const waitMs = 1000 * 2 ** (attempt - 1);
        console.log(`[Ollama] Caption batch ${batchNum} retry ${attempt}/${PER_BATCH_RETRIES - 1} in ${waitMs / 1000}s...`);
        await new Promise((r) => setTimeout(r, waitMs));
      }
      try {
        const reply = await streamOllamaChat(baseUrl, basePayload, 300_000);
        if (!reply) throw new Error("Empty reply after stripping thinking blocks");

        const replyLines = reply.split("\n");
        const batchTranslated = new Map<number, string>();

        for (const rawLine of replyLines) {
          const lineStr = rawLine.trim();
          if (!lineStr) continue;
          const m = NUMBERED_LINE_RE.exec(lineStr);
          if (m) {
            const localIdx = parseInt(m[1], 10) - 1;
            const textContent = m[2].trim().replace(/^[*_ ]+/, "").replace(/[*_ ]+$/, "");
            if (localIdx >= 0 && localIdx < clampedActive.length) {
              batchTranslated.set(localIdx, textContent);
            }
          }
        }

        // Only run the positional fallback when pass 1 matched NOTHING —
        // mapping cleanLines[0..N] onto slots by position is only unambiguous
        // when no slots are already filled. Mixing positional fills into a
        // partially-matched batch can silently assign the wrong translation
        // to the wrong line if the model emitted extra/reordered lines.
        if (batchTranslated.size === 0) {
          const cleanLines: string[] = [];
          for (const rawLine of replyLines) {
            const s = rawLine.trim();
            if (!s || PREAMBLE_RE.test(s)) continue;
            const cleaned = s
              .replace(NUMBERED_PREFIX_RE, "")
              .replace(/^[*_ ]+/, "")
              .replace(/[*_ ]+$/, "")
              .trim();
            if (cleaned) cleanLines.push(cleaned);
          }
          if (cleanLines.length >= clampedActive.length) {
            const trimmed = cleanLines.slice(0, clampedActive.length);
            trimmed.forEach((t, i) => {
              if (!batchTranslated.has(i)) batchTranslated.set(i, t);
            });
          }
        }

        clampedActive.forEach(([origI], pos) => {
          results[batchStart + origI] = batchTranslated.get(pos) ?? active[pos][1];
        });
        batch.forEach((line, i) => {
          if (alreadyMask[i]) results[batchStart + i] = line;
        });

        batchSuccess = true;
        break;
      } catch (e: any) {
        const errStr = String(e?.message ?? e).slice(0, 200);
        if (attempt < PER_BATCH_RETRIES - 1) {
          console.warn(`[Ollama] Caption batch ${batchNum} attempt ${attempt + 1} error: ${errStr} — retrying...`);
        } else {
          console.warn(`[Ollama] Caption batch ${batchNum} exhausted: ${errStr} — keeping originals`);
        }
      }
    }

    if (!batchSuccess) {
      batch.forEach((line, i) => (results[batchStart + i] = line));
    }
  }

  return results.map((r, i) => (r !== null ? r : texts[i]));
}

export interface CaptionSegmentIO {
  start: number;
  end: number;
  text: string;
  speaker: string;
}

export interface CaptionTranslateResult {
  segments: CaptionSegmentIO[];
  // Indices into the *returned* segments array whose line needed translation
  // but came back unchanged (batch exhausted its retries, or the model
  // echoed it back) — surfaced so the web UI can flag them for a manual look,
  // same idea as FlaggedLyricLine above but simpler since caption text has
  // no timestamp-tag/parenthetical bookkeeping to worry about.
  flaggedIndices: number[];
}

// ---------------------------------------------------------------------------
// Public entry point — called by POST /api/captions/translate.
// mode "replace" (default) swaps each segment's text for its translation
// into opts.targetLanguage (a key of CAPTION_TARGET_LANGUAGES, default
// "fa" — Persian), which is what you want burned into the video as a
// single-language subtitle track. mode "bilingual" keeps the original line
// and appends the translation as a second line within the same
// segment/timing; build_ass()'s _ass_escape_text()
// (monster_archiver/video_captions.py) already turns a literal "\n" into
// ASS's "\N" line-break tag, so a two-line segment burns correctly with
// zero changes needed on the Python side — the same convention
// translate_and_merge uses for lyrics, just stacked vertically instead of
// parenthesised, since screen space for a subtitle is much tighter than a
// lyrics line.
// ---------------------------------------------------------------------------
export async function translateCaptionSegments(
  segments: CaptionSegmentIO[],
  opts: { mode?: "replace" | "bilingual"; videoTitle?: string; targetLanguage?: string }
): Promise<CaptionTranslateResult> {
  const { baseUrl, model, think, batchSize, maxLineChars } = getLocalLlmConfig();
  const disableThinking = !think;
  const mode = opts.mode === "bilingual" ? "bilingual" : "replace";
  const targetLanguageCode =
    opts.targetLanguage && CAPTION_TARGET_LANGUAGES[opts.targetLanguage] ? opts.targetLanguage : "fa";
  const targetLanguageName = captionTargetLanguageName(targetLanguageCode);

  const texts = segments.map((s) => s.text ?? "");
  if (!texts.some((t) => t.trim())) {
    return { segments, flaggedIndices: [] };
  }

  console.log(
    `[Ollama] Translating ${texts.length} caption line(s) to ${targetLanguageName} via ${model} ` +
      `(thinking=${think ? "ON — may take a while" : "OFF"}, batch_size=${batchSize}, mode=${mode})...`
  );

  const translated = await translateCaptionTextsInBatches(texts, {
    baseUrl,
    model,
    batchSize,
    maxLineChars,
    disableThinking,
    videoTitle: opts.videoTitle,
    targetLanguageCode
  });

  const flaggedIndices: number[] = [];
  const outSegments: CaptionSegmentIO[] = segments.map((seg, i) => {
    const original = seg.text ?? "";
    const newLine = (translated[i] ?? original).trim();
    if (!original.trim()) return seg; // nothing to translate on an empty line

    const needed = !isAlreadyInTargetLanguage(original, targetLanguageCode);
    const origClean = normalizeForCompare(original);
    const newClean = normalizeForCompare(newLine);
    const unchanged = !newClean || origClean === newClean;

    if (needed && unchanged) flaggedIndices.push(i);

    if (mode === "bilingual") {
      const text = !unchanged ? `${original}\n${newLine}` : original;
      return { ...seg, text };
    }
    return { ...seg, text: newLine || original };
  });

  return { segments: outSegments, flaggedIndices };
}
