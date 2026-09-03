// Describes the subset of rezakir.py's config.json that the web app's
// Settings panel exposes, so SettingsPanel.tsx can render a generic form
// instead of hardcoding one input per field. Adding a new configurable key
// later just means adding an entry here — see monster_archiver/config.py's
// DEFAULT_CONFIG for the full (much larger) set this is drawn from.

export type SettingType = "string" | "password" | "number" | "boolean" | "select";

export interface SettingOption {
  value: string;
  label: string;
}

export interface SettingField {
  key: string;
  label: string;
  type: SettingType;
  help: string;
  section: string;
  options?: SettingOption[];
  min?: number;
  max?: number;
  step?: number;
}

export const SETTINGS_SCHEMA: SettingField[] = [
  // ── General & metadata sources ──────────────────────────────────────────
  {
    key: "MAX_WORKERS",
    label: "Max Parallel Workers",
    type: "number",
    section: "General",
    help: "How many files the CLI archives concurrently. Higher uses more CPU/GPU/network at once.",
    min: 1,
    max: 16,
    step: 1,
  },
  {
    key: "ACOUSTID_API_KEY",
    label: "AcoustID API Key",
    type: "password",
    section: "General",
    help: "Free key from acoustid.org/api-key — enables audio fingerprinting and duplicate detection. Left empty, fingerprinting is skipped.",
  },
  {
    key: "MUSICBRAINZ_CONTACT_EMAIL",
    label: "MusicBrainz Contact Email",
    type: "string",
    section: "General",
    help: "Sent as MusicBrainz's required contact header. Without it, MusicBrainz may throttle metadata lookups.",
  },

  // ── Naming / folder templates ────────────────────────────────────────────
  // Also rendered as a dedicated card (NamingTemplatesPanel.tsx) in the
  // Library tab; these entries are what actually persist the values via
  // GET/PUT /api/settings — the dedicated card just gives them a friendlier
  // home than the generic grid below.
  {
    key: "NAMING_FOLDER_TEMPLATE",
    label: "Folder Template",
    type: "string",
    section: "Naming",
    help: "Tokens: {artist} {albumartist} {album} {title} {year} {track} {genre} {isrc} {disc} {composer}. \"/\" creates nested folders. Only affects files archived from now on.",
  },
  {
    key: "NAMING_FILENAME_TEMPLATE",
    label: "Filename Template",
    type: "string",
    section: "Naming",
    help: "Same tokens as the folder template. Only affects files archived from now on.",
  },
  {
    key: "PRIMARY_ARTIST_BY_FAME",
    label: "Pick Multi-Artist Folder By Fame",
    type: "boolean",
    section: "Naming",
    help: "When a track credits more than one artist, file it under whichever one has more Deezer fans instead of the first-listed name. The full artist credit is still written to the track's tags either way, so players show everyone.",
  },

  // ── Translation engine ───────────────────────────────────────────────────
  {
    key: "DEFAULT_TRANSLATION_ENGINE",
    label: "Default Translation Engine",
    type: "select",
    section: "Translation",
    help: "Which lyrics-translation engine the CLI defaults to when not overridden per-run.",
    options: [
      { value: "0", label: "Disabled" },
      { value: "1", label: "Google Translate (Smart Auto) — Recommended" },
      { value: "2", label: "MyMemory (Batch API)" },
      { value: "3", label: "Google Translate (Line-by-Line)" },
      { value: "5", label: "Local LLM (Ollama)" },
      { value: "6", label: "Google Gemini Pro (AI Studio)" },
    ],
  },
  {
    key: "GEMINI_API_KEY",
    label: "Gemini API Key",
    type: "password",
    section: "Translation",
    help: "Required for Google Gemini Pro translations. Get one at aistudio.google.com/app/apikey.",
  },
  {
    key: "GEMINI_MODEL",
    label: "Gemini Model",
    type: "select",
    section: "Translation",
    help: "Which Gemini model to use for translations. 3.7 Flash is recommended for speed and high quota. 3.1 Pro is for the best nuance.",
    options: [
      { value: "gemini-3.7-flash", label: "Gemini 3.7 Flash" },
      { value: "gemini-3.1-pro-preview", label: "Gemini 3.1 Pro (Preview)" },
      { value: "gemini-3.1-flash-lite", label: "Gemini 3.1 Flash Lite" },
    ],
  },
  {
    key: "LOCAL_LLM_BASE_URL",
    label: "Local LLM Base URL",
    type: "string",
    section: "Translation",
    help: "OpenAI-compatible endpoint for your local Ollama server.",
  },
  {
    key: "LOCAL_LLM_MODEL",
    label: "Local LLM Model",
    type: "string",
    section: "Translation",
    help: "Ollama model tag used for local translation (e.g. gemma4:12b).",
  },
  {
    key: "LOCAL_LLM_THINK",
    label: "Enable Model Thinking",
    type: "boolean",
    section: "Translation",
    help: "Lets reasoning-capable models (e.g. Gemma) show their thinking trace. Usually left off for faster, cleaner output.",
  },
  {
    key: "LOCAL_LLM_BATCH_SIZE",
    label: "Local LLM Batch Size",
    type: "number",
    section: "Translation",
    help: "How many lyric lines are sent to the local LLM per request.",
    min: 1,
    max: 64,
    step: 1,
  },
  {
    key: "LOCAL_LLM_MAX_LINE_CHARS",
    label: "Local LLM Max Line Characters",
    type: "number",
    section: "Translation",
    help: "Lines longer than this are split before translation to avoid overloading the model's context.",
    min: 100,
    max: 4000,
    step: 50,
  },

  // ── Audio quality ────────────────────────────────────────────────────────
  {
    key: "KEY_NOTATION",
    label: "Musical Key Notation",
    type: "select",
    section: "Audio Quality",
    help: "How the detected musical key is written into tags.",
    options: [
      { value: "text", label: "Text (e.g. F# minor)" },
      { value: "camelot", label: "Camelot (e.g. 11A)" },
      { value: "openkey", label: "Open Key (e.g. 4m)" },
    ],
  },
  {
    key: "LRC_OFFSET_MS",
    label: "Lyrics Timing Offset (ms)",
    type: "number",
    section: "Audio Quality",
    help: "Shifts every synced lyric line by this many milliseconds (+later / -earlier). 0 disables it.",
    min: -10000,
    max: 10000,
    step: 10,
  },
  {
    key: "REJECT_LOSSY_UPCONVERT",
    label: "Reject Fake-Lossless Files",
    type: "boolean",
    section: "Audio Quality",
    help: "When on, files that claim to be lossless but were upconverted from a lossy source are rejected instead of just flagged.",
  },
  {
    key: "UPCONVERT_ENERGY_THRESHOLD",
    label: "Upconvert Detection Threshold",
    type: "number",
    section: "Audio Quality",
    help: "Spectral-energy fraction below 16kHz that flags a file as fake-lossless. Raise toward 1.0 to reduce false positives.",
    min: 0.5,
    max: 1.0,
    step: 0.01,
  },

  // ── AI transcription ─────────────────────────────────────────────────────
  {
    key: "VRAM_SAFE_MODE",
    label: "VRAM Safe Mode",
    type: "boolean",
    section: "AI Transcription",
    help: "Runs Demucs and Whisper more conservatively to avoid out-of-memory errors on smaller GPUs.",
  },
  {
    key: "DEMUCS_MODEL",
    label: "Demucs Stem-Separation Model",
    type: "select",
    section: "AI Transcription",
    help: "htdemucs_ft gives better vocal separation but runs slower; htdemucs is faster.",
    options: [
      { value: "htdemucs_ft", label: "htdemucs_ft (best quality)" },
      { value: "htdemucs", label: "htdemucs (faster)" },
    ],
  },
  {
    key: "WHISPER_MODEL_SIZE",
    label: "Whisper Model Size",
    type: "select",
    section: "AI Transcription",
    help: "Larger models transcribe more accurately but need more VRAM and time.",
    options: [
      { value: "large-v3", label: "large-v3 (best, ~6GB VRAM)" },
      { value: "medium", label: "medium (~2.8GB VRAM)" },
      { value: "small", label: "small (fastest, lowest VRAM)" },
    ],
  },
  {
    key: "WHISPER_COMPUTE_TYPE",
    label: "Whisper Compute Type",
    type: "select",
    section: "AI Transcription",
    help: "Quantisation used to run Whisper. Auto picks the best option for your hardware.",
    options: [
      { value: "", label: "Auto" },
      { value: "float16", label: "float16 (best quality, most VRAM)" },
      { value: "int8_float16", label: "int8_float16 (balanced, CUDA)" },
      { value: "int8", label: "int8 (lowest VRAM)" },
    ],
  },

  // ── Video Captions (speaker diarization) ─────────────────────────────────
  {
    key: "HUGGINGFACE_TOKEN",
    label: "Hugging Face Access Token",
    type: "password",
    section: "Video Captions",
    help: "Required for pyannote.audio speaker diarization in the Video Captions tab. Create a free account at huggingface.co, accept the gated-model terms on huggingface.co/pyannote/speaker-diarization-3.1 and huggingface.co/pyannote/segmentation-3.0, then generate a token at huggingface.co/settings/tokens.",
  },
];

export const SETTINGS_SECTIONS = Array.from(new Set(SETTINGS_SCHEMA.map((f) => f.section)));
