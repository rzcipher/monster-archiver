export interface TrackMetadata {
  title: string;
  artist: string;
  album: string;
  album_artist: string;
  explicit?: boolean | null;
  year: string;
  track: string;
  disc: string;
  genre: string;
  composer: string;
  isrc: string;
  duration: number;
  bpm: number;
  key: string;
  has_lyrics: boolean;
  has_cover: boolean;
  cover: string;
}

export interface SpectrogramPoint {
  freq: number;
  db: number;
}

// Full-resolution time x frequency heatmap. `data` is a base64-encoded byte
// buffer of `rows * cols` uint8 values (row-major, row 0 = maxFreqHz down to
// row rows-1 = 0Hz), each a dB level quantized between dbFloor and dbCeil.
export interface SpectrogramImage {
  data: string;
  rows: number;
  cols: number;
  dbFloor: number;
  dbCeil: number;
  maxFreqHz: number;
}

export interface AudioFileInfo {
  type: string;
  sampleRate: number;
  bitDepth: number | null;
  channels: number;
  duration: number;
  nyquist: number;
  sizeBytes: number;
  samples: number;
  analysisFrames: number;
  fftSize: number;
  freqResolution: number;
}

export interface AnalysisResult {
  metadata: TrackMetadata;
  spectral: {
    suspect: boolean;
    is_lossless: boolean;
    energy_below_16k: number;
    max_active_freq_hz: number;
  };
  spectrogram: SpectrogramPoint[];
  spectrogramFull?: {
    image: SpectrogramImage;
    fileInfo: AudioFileInfo;
  } | null;
  bpm: number;
  key: string;
}

export interface LyricsOption {
  source: string;
  id?: number;
  title: string;
  artist: string;
  synced: string;
  plain: string;
  duration?: number;
}

// A lyric line the local LLM's full-song batch pass likely skipped or
// mistranslated (still non-English with no translation added). Lets the UI
// point the user at exactly the lines worth a manual single-line re-translate.
export interface FlaggedLyricLine {
  index: number;
  rawLine: string;
  preview: string;
}

// One diarized, review-ready caption line from POST /api/captions/transcribe
// (see caption_video() in monster_archiver/video_captions.py). `speaker` is
// a display label ("Speaker 1", "Speaker 2", ...) rather than pyannote's raw
// SPEAKER_00-style id -- editable in the review UI, and carried straight
// through to POST /api/captions/burn once corrected.
export interface CaptionSegment {
  start: number;
  end: number;
  text: string;
  speaker: string;
}

export interface CaptionTranscribeResult {
  segments: CaptionSegment[];
  speakers: string[];
  audioDuration: number;
}

export interface SearchPreset extends TrackMetadata {
  source: string;
}
