import { GoogleGenAI, ThinkingLevel } from "@google/genai";
import { readSharedConfig } from "./sharedConfig";
import { FlaggedLyricLine, LineTranslationResult, OllamaTranslateResult } from "./ollamaTranslate";

// Reusing some helpers from ollamaTranslate
const SYNC_RE = /^((?:\[\d+:\d+(?:\.\d+)?\])+)(.*)$/;
const META_RE = /^\[[a-zA-Z]+:.*\]/;
const TS_ONLY_RE = /^(?:\[\d+:\d+(?:\.\d+)?\])+\s*$/;
const WORD_TS_RE = /<\d+:\d+(?:\.\d+)?>/g;
const NUMBERED_LINE_RE = /^(?:[-*\s•]*)?(?:\[|\()?(\d+)(?:\]|\))?[\s.)[\]:,-]*\s*(.*)/;
const NUMBERED_PREFIX_RE = /^(?:[-*\s•]*)?(?:\[|\()?(\d+)(?:\]|\))?[\s.)[\]:,-]*\s*/;
const PREAMBLE_RE = /^(?:here\s+(?:are|is)\b|sure[,\s]|certainly[,:]|translations?\s*(?:results?)?:|output:|results?:|notes?:)/i;
const PAREN_LINE_RE = /^[（(].*[)）]$/;

interface LrcLine {
  isLyric: boolean;
  timestamps: string;
  rawContent: string;
  cleanContent: string;
  rawLine: string;
}

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

function hasCjkOrHangul(text: string): boolean {
  return /[\u3000-\u9fff\uac00-\ud7af\u1100-\u11ff\ua960-\ua97f\ud7b0-\ud7ff\uf900-\ufaff\u{20000}-\u{2fa1f}]/u.test(text);
}
const EUROPEAN_DIACRITICS = new Set("àáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞß".split(""));

function isEnglishLine(text: string): boolean {
  if (!text || !text.trim()) return false;
  if (hasCjkOrHangul(text)) return false;
  for (const ch of text) if (EUROPEAN_DIACRITICS.has(ch)) return false;
  let asciiCount = 0;
  for (let i = 0; i < text.length; i++) {
    if (text.charCodeAt(i) < 128) asciiCount++;
  }
  return asciiCount / Math.max(text.length, 1) > 0.75;
}

const SYSTEM_PROMPT =
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
  "Go directly to the numbered output.";

function getAiClient(): GoogleGenAI {
  const conf = readSharedConfig();
  const apiKey = conf.GEMINI_API_KEY || process.env.GEMINI_API_KEY;
  if (!apiKey) {
    throw new Error("Gemini API Key is missing. Please add it in the Settings panel.");
  }
  return new GoogleGenAI({
    apiKey: String(apiKey),
    httpOptions: {
      headers: {
        'User-Agent': 'aistudio-build',
      }
    }
  });
}

async function assessSongTone(lines: string[], title: string | undefined, artist: string | undefined): Promise<string | null> {
  if (!lines.length) return null;
  const sample = lines.length > 20 ? Array.from({ length: 20 }, (_, i) => lines[Math.floor((i * lines.length) / 20)]) : lines;
  const ctxParts: string[] = [];
  if (title && title !== "Unknown") ctxParts.push(`Song Title: "${title}"`);
  if (artist && artist !== "Unknown") ctxParts.push(`Artist:     "${artist}"`);
  const contextHeader = ctxParts.length ? ctxParts.join("\n") + "\n\n" : "";

  const numbered = sample.map((line, i) => `${i + 1}. ${line}`).join("\n");
  const userContent =
    `${contextHeader}Read the following representative song lyric lines carefully.\n` +
    `Describe the song's overall emotional tone and mood in 1–2 sentences. ` +
    `Be deeply human and empathetic in your analysis — identify if it is happy, sad, melancholic, upbeat, angry, or bittersweet.\n` +
    `Be accurate — do not project darkness onto a light song or lightness onto a dark one. ` +
    `Base your description solely on what is actually in the lyrics.\n` +
    `Reply with ONLY the tone description.\n\n${numbered}`;

  try {
    const conf = readSharedConfig();
    const modelId = conf.GEMINI_MODEL || "gemini-3.7-flash";
    const reqConfig: any = {
      systemInstruction: "You are a music analyst. Your only job is to read song lyrics and describe their actual emotional tone accurately and neutrally in 1-2 sentences."
    };
    if (modelId.startsWith("gemini-3")) {
      reqConfig.thinkingConfig = { thinkingLevel: ThinkingLevel.HIGH };
    }

    const response = await getAiClient().models.generateContent({
      model: modelId,
      contents: userContent,
      config: reqConfig
    });
    return response.text || null;
  } catch (e: any) {
    console.warn(`[Gemini] Tone pre-read failed (non-fatal): ${String(e?.message ?? e).slice(0, 120)}`);
    return null;
  }
}

async function translateAllLines(texts: string[], maxLineChars: number, title?: string, artist?: string, songTone?: string | null, batchSize = 8): Promise<string[]> {
  if (!texts.length) return [];
  const ctxParts: string[] = [];
  if (title && title !== "Unknown") ctxParts.push(`Song Title: "${title}"`);
  if (artist && artist !== "Unknown") ctxParts.push(`Artist:     "${artist}"`);
  if (songTone) ctxParts.push(`Song tone:  ${songTone}`);
  const contextHeader = ctxParts.length ? ctxParts.join("\n") + "\n\n" : "";

  const PER_BATCH_RETRIES = 3;
  const results: Array<string | null> = new Array(texts.length).fill(null);

  const englishMask = texts.map(line => isEnglishLine(line));
  const active: Array<[number, string]> = [];
  texts.forEach((line, i) => { if (!englishMask[i]) active.push([i, line]); });

  if (!active.length) {
    return texts;
  }

  const clampedActive = maxLineChars > 0 ? active.map(([i, line]) => [i, line.slice(0, maxLineChars)] as [number, string]) : active;

  // Batch the request (LOCAL_LLM_BATCH_SIZE — same setting the Ollama path
  // honours). Sending the entire song in one request risks output-token
  // truncation on long songs, which then degrades the parse and loses
  // translations; per-batch requests keep each reply comfortably small.
  const effectiveBatchSize = Number.isFinite(batchSize) && batchSize > 0 ? Math.floor(batchSize) : 8;
  const totalBatches = Math.ceil(clampedActive.length / effectiveBatchSize);

  for (let batchIdx = 0; batchIdx < totalBatches; batchIdx++) {
    const slice = clampedActive.slice(batchIdx * effectiveBatchSize, (batchIdx + 1) * effectiveBatchSize);
    const numbered = slice.map(([, line], pos) => `${pos + 1}. ${line}`).join("\n");

    const userContent =
      `${contextHeader}` +
      `Translate the following song lyric lines into natural, expressive English.\n` +
      `Read ALL lines together first to understand the song's theme, emotional arc, and recurring imagery.\n` +
      `CRITICAL: Using the "Song tone" provided in the context, ensure your translation matches the emotional weight (happy, sad, euphoric, grief-stricken, etc). Make it sound perfectly human, poetic, and emotionally resonant.\n` +
      `Lines that are already in English must be returned exactly as written.\n` +
      `CRITICAL: Reply with exactly the same number of lines as the input (${slice.length} lines). Each line MUST start with its number followed by a period (e.g., '1. ', '2. '). Do NOT skip or combine lines, do NOT add commentary.\n\n${numbered}`;

    let success = false;
    for (let attempt = 0; attempt < PER_BATCH_RETRIES; attempt++) {
      if (attempt > 0) await new Promise(r => setTimeout(r, 2000 * attempt));
      try {
        const conf = readSharedConfig();
        const modelId = conf.GEMINI_MODEL || "gemini-3.7-flash";
        const reqConfig: any = {
          systemInstruction: SYSTEM_PROMPT
        };
        if (modelId.startsWith("gemini-3")) {
          reqConfig.thinkingConfig = { thinkingLevel: ThinkingLevel.HIGH };
        }

        const response = await getAiClient().models.generateContent({
          model: modelId,
          contents: userContent,
          config: reqConfig
        });

        const reply = response.text || "";
        const replyLines = reply.split("\n");
        const translated = new Map<number, string>();

        for (const rawLine of replyLines) {
          const lineStr = rawLine.trim();
          if (!lineStr) continue;
          const m = NUMBERED_LINE_RE.exec(lineStr);
          if (m) {
            const localIdx = parseInt(m[1], 10) - 1;
            const textContent = m[2].trim().replace(/^[*_ ]+/, "").replace(/[*_ ]+$/, "");
            if (localIdx >= 0 && localIdx < slice.length) {
              translated.set(localIdx, textContent);
            }
          }
        }

        // Positional fallback: only when pass 1 matched NOTHING — mapping
        // cleanLines[0..N] onto slots by position is only unambiguous when
        // no slots are already filled. Mixing positional fills into a
        // partially-matched reply can silently assign the wrong translation
        // to the wrong lyric line.
        if (translated.size === 0) {
          const cleanLines: string[] = [];
          for (const rawLine of replyLines) {
            const s = rawLine.trim();
            if (!s || PREAMBLE_RE.test(s)) continue;
            const cleaned = s.replace(NUMBERED_PREFIX_RE, "").replace(/^[*_ ]+/, "").replace(/[*_ ]+$/, "").trim();
            if (cleaned) cleanLines.push(cleaned);
          }
          if (cleanLines.length >= slice.length) {
            const trimmed = cleanLines.slice(0, slice.length);
            trimmed.forEach((t, i) => { if (!translated.has(i)) translated.set(i, t); });
          }
        }

        slice.forEach(([origI, origLine], pos) => {
          results[origI] = translated.get(pos) ?? origLine;
        });
        success = true;
        break;
      } catch (e: any) {
        console.warn(`[Gemini] Batch ${batchIdx + 1}/${totalBatches} attempt ${attempt + 1} error: ${e.message}`);
      }
    }

    if (!success) {
      // Batch exhausted its retries — keep originals for its lines only;
      // other batches' translations are preserved.
      slice.forEach(([origI, origLine]) => {
        if (results[origI] === null) results[origI] = origLine;
      });
    }
  }

  texts.forEach((line, i) => {
    if (englishMask[i]) results[i] = line;
  });

  return results.map((r, i) => (r !== null ? r : texts[i]));
}

export async function translateWithGeminiPro(
  lyricsText: string,
  title: string | undefined,
  artist: string | undefined,
  mode: string | undefined
): Promise<OllamaTranslateResult> {
  const conf = readSharedConfig();
  const batchSizeRaw = Number(conf.LOCAL_LLM_BATCH_SIZE);
  const batchSize = Number.isFinite(batchSizeRaw) && batchSizeRaw > 0 ? Math.floor(batchSizeRaw) : 8;
  const maxLineCharsRaw = Number(conf.LOCAL_LLM_MAX_LINE_CHARS);
  const maxLineChars = Number.isFinite(maxLineCharsRaw) ? maxLineCharsRaw : 800;
  const effectiveMode = mode || "translate_and_merge";

  const parsed = parseLrcLines(lyricsText);
  const texts = parsed.filter((l) => l.isLyric).map((l) => l.cleanContent);
  if (!texts.length) return { lyrics: lyricsText, flaggedLines: [] };

  const songTone = await assessSongTone(texts, title, artist);
  const translatedTexts = await translateAllLines(texts, maxLineChars, title, artist, songTone, batchSize);

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
  return { lyrics: outputLines.join("\n"), flaggedLines };
}

async function translateOneLineTextGemini(cleanText: string, maxLineChars: number, contextHeader: string): Promise<string> {
  const clamped = maxLineChars > 0 ? cleanText.slice(0, maxLineChars) : cleanText;
  const userContent =
    `${contextHeader}Translate the following single song lyric line into natural, expressive English.\n` +
    `Read it in context of the surrounding song and preserve metaphor, mood, and poetic intent. Ensure your translation captures the human emotional weight of the song (e.g. happy, sad, melancholic) perfectly.\n` +
    `If the line is already natural English, return it unchanged.\n` +
    `Respond with ONLY the translated line — no line numbers, no quotation marks, no commentary.\n\n${clamped}`;

  for (let attempt = 0; attempt < 3; attempt++) {
    if (attempt > 0) await new Promise(r => setTimeout(r, 1000 * attempt));
    try {
      const conf = readSharedConfig();
      const modelId = conf.GEMINI_MODEL || "gemini-3.7-flash";
      const reqConfig: any = {
        systemInstruction: SYSTEM_PROMPT
      };
      if (modelId.startsWith("gemini-3")) {
        reqConfig.thinkingConfig = { thinkingLevel: ThinkingLevel.HIGH };
      }

      const response = await getAiClient().models.generateContent({
        model: modelId,
        contents: userContent,
        config: reqConfig
      });
      const reply = response.text || "";
      const cleaned = reply.replace(NUMBERED_PREFIX_RE, "").replace(/^["'“”]+|["'“”]+$/g, "").trim();
      if (cleaned) return cleaned;
    } catch (e: any) {
      console.warn(`[Gemini] Single line translation error: ${e.message}`);
    }
  }
  throw new Error("Gemini returned an empty translation");
}

export async function translateLinesAtIndicesGemini(
  lyricsText: string,
  requestedIndices: number[],
  title: string | undefined,
  artist: string | undefined,
  mode: string | undefined
): Promise<LineTranslationResult> {
  const conf = readSharedConfig();
  const maxLineCharsRaw = Number(conf.LOCAL_LLM_MAX_LINE_CHARS);
  const maxLineChars = Number.isFinite(maxLineCharsRaw) ? maxLineCharsRaw : 800;
  const effectiveMode = mode || "translate_and_merge";

  const rawLines = lyricsText.split(/\r\n|\r|\n/);
  const parsed = parseLrcLines(lyricsText);

  const resolveIndex = (i: number): number => {
    const cur = parsed[i];
    if (cur && cur.isLyric && PAREN_LINE_RE.test(cur.rawContent.trim()) && i > 0) {
      const prev = parsed[i - 1];
      if (prev && prev.isLyric && prev.timestamps === cur.timestamps) return i - 1;
    }
    return i;
  };

  const uniqueIndices = Array.from(
    new Set(requestedIndices.filter((i) => Number.isInteger(i) && i >= 0 && i < parsed.length).map(resolveIndex))
  ).sort((a, b) => a - b);

  const validIndices = uniqueIndices.filter((i) => parsed[i]?.isLyric && parsed[i].cleanContent.trim());
  if (!validIndices.length) throw new Error("No translatable lyric line at the selected position");

  const contextParts: string[] = [];
  if (title && title !== "Unknown") contextParts.push(`Song Title: "${title}"`);
  if (artist && artist !== "Unknown") contextParts.push(`Artist:     "${artist}"`);
  const contextHeader = contextParts.length ? contextParts.join("\n") + "\n\n" : "";

  const translationByIndex = new Map<number, string>();
  const translations: Array<{ lineIndex: number; original: string; translated: string }> = [];
  for (const idx of validIndices) {
    const cleanText = parsed[idx].cleanContent;
    const translated = await translateOneLineTextGemini(cleanText, maxLineChars, contextHeader);
    translationByIndex.set(idx, translated);
    translations.push({ lineIndex: idx, original: cleanText, translated });
  }

  const outLines: string[] = [];
  let skipNext = 0;
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
    const nextIsStaleTranslation = !!nextParsed && nextParsed.isLyric && nextParsed.timestamps === line.timestamps && PAREN_LINE_RE.test(nextParsed.rawContent.trim());

    if (effectiveMode === "translate_only") {
      outLines.push(`${prefix}${translated}`);
      continue;
    }

    outLines.push(`${prefix}${line.rawContent}`);
    const origClean = normalizeForCompare(line.cleanContent);
    const engClean = normalizeForCompare(translated);
    if (origClean !== engClean && engClean) {
      outLines.push(`${prefix}（${translated.replace(/[\r\n]+/g, " ").trim()}）`);
      if (nextIsStaleTranslation) skipNext = 1;
    } else if (nextIsStaleTranslation) {
      skipNext = 1;
    }
  }

  return { lyrics: outLines.join("\n"), translations };
}
