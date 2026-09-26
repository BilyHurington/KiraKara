// Types mirroring kara_align/models.py and the view payloads in docs/api.md.
// Times are integer ms on the original audio timeline, intervals [start, end).

export type Mode = 'plain' | 'lrc';
export type Role = 'original' | 'vocals' | 'instrumental';
export type Source = Role | 'mix';

export interface Unit { id: string; reading: string; surface: string; flags: string[] }

export interface Segment {
  id: string;
  surface: string;
  reading: string | null;
  lang: 'ja' | 'zh' | 'en' | 'other';
  units: Unit[];
  reading_source: 'rule' | 'manual' | 'ai' | 'import' | 'none';
  confirmed: boolean;
  uncertain: boolean;
  candidates: string[];
  note: string;
}

export interface LineAnchor { abs_ms: number; hard: boolean; tolerance_ms: number; note: string }

export interface Line {
  id: string;
  text: string;
  kind: 'lyric' | 'translation' | 'romanization' | 'meta' | 'blank';
  sing: boolean;
  segments: Segment[];
  imported_start_ms: number | null;
  imported_end_ms: number | null;
  anchor: LineAnchor | null;
  translation: string | null;
  romanization: string | null;
  voice: string;
  confirmed: boolean;
  source: { origin: string; raw_index: number | null; merged_from: string[]; split_from: string | null; tag_index: number };
}

export interface LyricsDoc {
  language: string;
  meta: { title: string | null; artist: string | null; album: string | null; duration_ms: number | null };
  lines: Line[];
  embedded_offset_raw: string | null;
  embedded_shift_ms: number;
  embedded_offset_note: string;
}

export interface CalibrationCheck { line_id: string; marked_ms: number; residual_ms: number }
export interface Calibration {
  user_shift_ms: number;
  confirmed: boolean;
  reference_line_id: string | null;
  marked_ms: number | null;
  checks: CalibrationCheck[];
  history: unknown[];
}

export interface AudioAsset {
  id: string;
  role: Role | 'mix';
  sha256: string;
  path: string | null;
  duration_ms: number;
  sample_rate: number;
  channels: number;
  num_samples: number;
  origin_offset_samples: number;
  sync_checked: boolean;
  sync_report: Record<string, any> | null;
  source: { kind: string; filename: string | null; model: string | null; model_version: string | null; notes: string[]; config: Record<string, any> };
}

export interface DecodeConfig {
  left_margin_ms: number; right_margin_ms: number; soft_sigma_ms: number; soft_lambda: number;
  huber_delta: number; hard_tolerance_ms: number; joint_context_lines: number; tight_gap_ms: number;
  band_frames: number | null;
}
export interface AlignConfig {
  backend: string;
  model_id: string | null;
  model_revision: string | null;
  device: string;
  audio_role: 'original' | 'vocals';
  chunk_s: number;
  context_s: number;
  decode: DecodeConfig;
  checks: Record<string, number>;
  retry: { enabled: boolean; max_candidates_per_line: number; max_total_candidates: number };
  tail: { strategy: 'off' | 'trim' | 'energy'; max_extend_ms: number; max_trim_ms: number; energy_floor_db: number };
}

export interface ManualEdit { start_ms: number | null; end_ms: number | null; locked: boolean; at: string; note: string }

export interface UnitTiming {
  unit_id: string;
  line_id: string;
  segment_id: string;
  reading: string;
  start_ms: number | null;
  end_ms: number | null;
  status: 'ok' | 'failed' | 'unaligned' | 'skipped';
  reason: string | null;
  model_start_ms: number | null;
  model_end_ms: number | null;
  tail: { original_end_ms: number | null; new_end_ms: number | null; method: string; reason: string } | null;
  manual: ManualEdit | null;
  manual_history: ManualEdit[];
  acoustic_score: number | null;
  flags: string[];
}

export interface Issue {
  code: string;
  severity: 'info' | 'warning' | 'error';
  line_id: string | null;
  unit_id: string | null;
  message: string;
  data: Record<string, any>;
}

export interface LineTiming {
  line_id: string;
  start_ms: number | null;
  end_ms: number | null;
  status: string;
  reason: string | null;
  anchor_ms: number | null;
  anchor_kind: 'soft' | 'hard' | null;
  anchor_residual_ms: number | null;
  window_ms: [number, number] | null;
  context_line_ids: string[];
  audio_role: string | null;
  candidate: string | null;
  flags: string[];
}

export interface Candidate { id: string; line_id: string; label: string; units: UnitTiming[]; summary: Record<string, any> }

export interface AlignmentResult {
  id: string;
  created: string;
  mode: Mode;
  backend: { name: string; model_id: string; model_revision: string | null; license: string | null; profile: string; sample_rate: number };
  config: AlignConfig;
  snapshot: { mode: Mode; audio_role: string; line_ids: string[]; audio_asset_id: string };
  coverage: { full: boolean; line_ids: string[]; from_ms: number | null; to_ms: number | null };
  lines: LineTiming[];
  units: UnitTiming[];
  issues: Issue[];
  candidates: Candidate[];
  stale: boolean;
  stale_reason: string | null;
  parent_result_id: string | null;
  stats: Record<string, any>;
}

export interface MixSettings { vocal_keep_pct: number; instrumental_pct: number; master: number; limiter: 'none' | 'normalize_peak' }

export interface AiRoundtrip { id: string; created: string; snapshot_id: string; line_ids: string[]; status: string; applied_at: string | null }

export interface VideoAsset {
  id: string;
  sha256: string;
  path: string | null;
  filename: string | null;
  container: string;
  duration_ms: number;
  width: number | null;
  height: number | null;
  fps: number | null;
  video_codec: string | null;
  audio_codec: string | null;
  audio_offset_s: number;
  audio_sha256: string;
}

export interface KaraokeStyle {
  version: number;
  /** name of the saved style it was loaded from ("" = none) */
  preset: string;
  layout: {
    position: 'bottom' | 'top'; lines: number; arrangement: 'alternate' | 'center';
    margin_v: number; line_spacing: number; margin_h: number; alternate_indent: number; shrink_long_lines: boolean;
  };
  text: {
    font: string; size: number; bold: boolean; color_unsung: string; color_sung: string; outline_color: string;
    outline: number; shadow: number; shadow_color: string; shadow_opacity: number;
  };
  ruby: {
    enabled: boolean; script: 'hiragana' | 'katakana' | 'romaji'; target: 'kanji' | 'all'; size_pct: number; gap: number;
    fit: 'widen' | 'overflow'; sweep: 'own' | 'base'; follow_colors: boolean; font: string; color_unsung: string; color_sung: string;
    outline_color: string; outline: number;
  };
  translation: {
    enabled: boolean; position: 'opposite' | 'block' | 'line'; size_pct: number; font: string; bold: boolean;
    color: string; outline_color: string; outline: number; shadow: number; glow: boolean;
  };
  glow: { enabled: boolean; color_unsung: string; color_sung: string; size: number; blur: number; strength: number; ruby: boolean };
  timing: {
    lead_in_ms: number; hold_ms: number; highlight: 'sweep' | 'instant'; early_show: boolean; early_max_ms: number;
    advance_ms: number; fade_in_ms: number; fade_out_ms: number;
  };
  /** effects around the lyrics, fired by each syllable as it is sung */
  effects: { kind: EffectKind; amount: number; size: number; color: string; ruby: boolean; behind: boolean };
  /** the colour template the colours came from; null once a colour or effect is changed by hand */
  theme?: { template: 'plain' | 'glow'; color: string; secondary: string } | null;
  /** song title card in a top corner at the start */
  info: {
    enabled: boolean; position: 'top-left' | 'top-right'; fields: SongInfoField[];
    start_ms: number; duration_ms: number; size: number; margin: number; color: string; accent: string;
  };
  /** burn-in audio: vocals kept at this % over the full instrumental */
  output?: { vocal_keep_pct: number };
}

/** A saved subtitle style (预设); 默认 is built in and read-only. */
export interface SavedStyle { id: string; name: string; builtin: boolean; updated: string | null; style: KaraokeStyle }

export type SongInfoField = 'title' | 'artist' | 'album' | 'lyricist' | 'composer' | 'arranger';
/** GET /api/projects/{id}/karaoke/info: what the song data fills, and the project's own text (null = automatic) */
export interface SongInfo { fields: Partial<Record<SongInfoField, string>>; labels: Record<SongInfoField, string>; text: string | null }

export type EffectKind = 'none' | 'pulse' | 'ring' | 'shine' | 'sparkle' | 'petals' | 'hearts' | 'ball';

export interface FontFamily { family: string; names: string[]; bold: boolean }

export interface Project {
  id: string;
  name: string;
  created: string;
  updated: string;
  mode: Mode;
  lyrics: LyricsDoc;
  sources: { id: string; origin: string; kind: string; filename: string | null; url: string | null; platform_song_id?: string | null; created: string }[];
  calibration: Calibration;
  ai_roundtrips: AiRoundtrip[];
  config: AlignConfig;
  audio: AudioAsset[];
  results: AlignmentResult[];
  active_result_id: string | null;
  mix: MixSettings;
  video?: VideoAsset | null;
  karaoke?: KaraokeStyle;
  song_info_text?: string | null;
}

export interface ResultSummary {
  id: string;
  created: string;
  mode: Mode;
  stale: boolean;
  stale_reason: string | null;
  coverage: AlignmentResult['coverage'];
  parent_result_id: string | null;
  n_units: number;
  n_failed: number;
  n_issues: number;
  n_manual: number;
  audio_role: string;
  backend: string;
}

export interface ProjectView {
  project: Project;
  view: {
    effective_starts: Record<string, { ms: number; kind: 'soft' | 'hard' }>;
    calibration_issues: Issue[];
    mode_notice: string | null;
    results: ResultSummary[];
    capability_warnings: string[];
    audio: Partial<Record<Role, { asset_id: string; available: boolean; duration_ms: number; sample_rate: number }>>;
  };
  [extra: string]: any;
}

export interface ProjectListItem { id: string; name: string; mode: Mode; updated: string }

export interface Job {
  id: string;
  kind: string;
  project_id: string | null;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  progress: number;
  message: string;
  error: string | null;
  created: string;
  finished: string | null;
  output: any;
  label?: string;
}

export interface Info {
  version: string;
  backends: { name: string; description: string; languages: string[]; available: boolean; default_model: string | null; license: string; missing?: string[] }[];
  separation_presets: { name: string; model_filename: string; architecture: string; notes: string; license_note: string }[];
  separation_available: boolean;
  export_formats: Record<string, { filename: string; description: string }>;
}

export interface LyricsPreview {
  preview_id: string | null;
  detected: string;
  warnings: string[];
  error: string | null;
  doc: LyricsDoc | null;
  extra_tracks: Record<string, string>;
  route?: 'json-project' | 'json-alignment' | 'json-reading-patch';
  song?: Record<string, any>;
}

export interface FetchedSong {
  platform: string;
  song_id: string;
  title: string | null;
  artists: string[];
  album: string | null;
  duration_ms: number | null;
  tracks: Record<string, string>;
  has_timestamps: Record<string, boolean>;
  notes?: string[];
}
export interface SongRef { platform: string; song_id: string; title: string; artists: string[]; album: string | null; duration_ms: number | null }
export type LinkResult =
  | { kind: 'song'; song: FetchedSong }
  | { kind: 'collection'; platform: string; title: string | null; songs: SongRef[] };

export interface PairItem { line_id: string; line_text: string; text: string; method: string; delta_ms?: number | null }
export interface TrackPreview { kind: string; pairs: PairItem[]; unmatched_line_ids: string[]; unmatched: string[] }

export interface PatchLine {
  line_id: string;
  status: string;
  reasons: string[];
  diff: { surface: string; old_reading: string | null; new_reading: string | null; old_units: string[]; new_units: string[] }[];
}
export interface PatchReport { ok: boolean; snapshot_match: boolean; warnings: string[]; errors: string[]; lines: PatchLine[]; missing_line_ids?: string[] }

export interface ExportInline { filename: string; media_type: string; content: string; warnings: string[] }

// ------------------------------------------------------------------ app settings / simple mode

export type AiProviderId = 'none' | 'claude' | 'codex' | 'openai';

export interface AppSettings {
  version: number;
  ai: {
    provider: AiProviderId; model: string; base_url: string; api_key_env: string; timeout_s: number;
    has_api_key: boolean; env_key_present: boolean;
  };
  simple: {
    default_mode: Mode; ai_readings: boolean; separate: boolean; separation_preset: string;
    separation_device: 'auto' | 'cpu'; karaoke: KaraokeStyle; auto_export: boolean;
    video_audio: 'original' | 'mix' | 'none'; vocal_keep_pct: number; quality: 'standard' | 'high';
    /** last choices of the new-task form (step 4) */
    task_style: TaskStyleOptions;
  };
}

/** A simple-mode task's subtitle choices (bound to the task when it is added). */
export interface TaskStyleOptions {
  source: 'template' | 'saved' | 'default';
  template: 'plain' | 'glow';
  color: string;
  /** second theme colour; '' = one colour */
  secondary: string;
  saved_id: string;
  /** null: as the chosen style says */
  translation: boolean | null;
  song_info: boolean | null;
  ruby: 'style' | 'hiragana' | 'katakana' | 'romaji' | 'off';
  /** null: as the chosen style says */
  ruby_target?: 'kanji' | 'all' | null;
  /** lyric size at 1920 wide (scaled by the video width); null: the style's */
  font_size?: number | null;
  /** null: the settings' choice */
  video_audio: 'original' | 'mix' | 'none' | null;
  /** vocals kept with "mix"; null: the settings' level */
  vocal_keep_pct?: number | null;
}

/** POST /api/karaoke/theme */
export interface ThemePreview { palette: Record<string, string>; style: KaraokeStyle }

/** Partial update for PUT /api/settings (api_key / clear_api_key are write-only). */
export interface SettingsPatch {
  ai?: Partial<AppSettings['ai']> & { api_key?: string; clear_api_key?: boolean };
  simple?: Partial<AppSettings['simple']> & { reset_karaoke?: boolean };
}

export interface AiProviderInfo { id: Exclude<AiProviderId, 'none'>; label: string; available: boolean; version: string | null; detail: string }

export type TaskStatus = 'preparing' | 'queued' | 'running' | 'waiting' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted';

export interface PipelineStage { key: string; label: string; status: 'pending' | 'running' | 'waiting' | 'done' | 'skipped' | 'failed'; progress: number; message: string }

/** What the user confirms right after an LRC task is added (see pipeline.calibration_request). */
export interface CalibrationRequest {
  line_id: string; line_text: string; lrc_ms: number; lines: { id: string; text: string; lrc_ms: number }[];
  check_line: { id: string; text: string; lrc_ms: number } | null;
  asset_id: string | null; duration_ms: number | null; confirmed_ms?: number;
}

export interface PipelineTask {
  id: string; created: string; finished: string | null; name: string; mode: Mode; media_filename: string;
  lyrics_kind: 'link' | 'text'; lyrics_input: string; status: TaskStatus; project_id: string | null;
  stages: PipelineStage[]; progress: number; message: string; error: string | null; detail: string | null;
  warnings: string[]; outputs: { video?: { filename: string; url: string } };
  calibration?: CalibrationRequest | null; calibration_confirmed?: boolean;
  /** the subtitle style and video settings bound to this task */
  style_label?: string; style_colors?: string[];
  video?: { auto_export: boolean; video_audio: 'original' | 'mix' | 'none'; vocal_keep_pct: number; quality: 'standard' | 'high' } | null;
}
