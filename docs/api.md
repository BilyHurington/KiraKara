# Kara Align HTTP API (local WebUI)

Served by `kara-align serve` (FastAPI, default `http://127.0.0.1:8765`). All JSON.
Times are integer ms on the original audio timeline, intervals `[start_ms, end_ms)`.
Errors: HTTP 4xx/5xx with `{"detail": "<human readable message>"}`.

`Project`, `LyricsDoc`, `Line`, `Segment`, `Unit`, `Calibration`, `AudioAsset`,
`AlignmentResult`, `UnitTiming`, `Issue`, `Candidate`, `MixSettings` are the
pydantic models in `kara_align/models.py`, serialized as-is.

## General

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| GET | `/api/info` | – | `{version, backends: [{name, description, languages, available, default_model, license}], separation_presets: [{name, filename, notes}], separation_available: bool, export_formats: {fmt: {filename, description}}}` |
| GET | `/api/jobs/{job_id}` | – | `Job` = `{id, kind, project_id, status: queued\|running\|succeeded\|failed\|cancelled, progress 0..1, message, error, created, finished, output}` |
| POST | `/api/jobs/{job_id}/cancel` | – | `Job` |
| GET | `/api/projects/{pid}/jobs` | – | `[Job]` |

## Projects

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| GET | `/api/projects` | – | `[{id, name, mode, updated}]` |
| POST | `/api/projects` | `{name, mode: "plain"\|"lrc"}` | `ProjectView` |
| GET | `/api/projects/{pid}` | – | `ProjectView` |
| PATCH | `/api/projects/{pid}` | `{name?, mode?, config?: AlignConfig (partial ok), mix?: MixSettings}` | `ProjectView` |
| POST | `/api/projects/import` | multipart `file` (project.json or .kara.zip) | `ProjectView` |
| GET | `/api/projects/{pid}/package?include_audio=1` | – | zip download |

`ProjectView` = `{project: Project, view: {effective_starts: {line_id: {ms, kind: "soft"|"hard"}}, calibration_issues: [Issue], mode_notice: str|null, results: [{id, created, mode, stale, stale_reason, coverage, parent_result_id, n_units, n_failed, n_issues, n_manual}], capability_warnings: [str], audio: {role: {asset_id, available: bool, duration_ms}}}}`.

Result staleness is recomputed on every read: results whose input snapshot no longer
matches the current lyrics text / readings / calibration / mode / audio get
`stale: true` with a reason (still viewable).

## Lyrics input (paste and upload share the same path: uploads are read as text by the browser and sent with `origin: "upload"` + `filename`)

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| POST | `/api/projects/{pid}/lyrics/parse` | `{text, origin: "paste"\|"upload", filename?}` | `LyricsPreview` |
| POST | `/api/projects/{pid}/lyrics/apply` | `{preview_id}` | `ProjectView` (replaces lyrics doc; old results become stale) |
| POST | `/api/projects/{pid}/lyrics/track/preview` | `{text, kind: "translation"\|"romanization", origin, filename?}` | `{preview_id, pairs: [{line_id, line_text, text, method}], unmatched: [text]}` |
| POST | `/api/projects/{pid}/lyrics/track/apply` | `{kind, pairs: [{line_id, text}]}` | `ProjectView` |
| POST | `/api/lyrics/link` | `{text}` (URL / share text / short link / `netease:123` / `qq:mid`) | `{kind: "song", song: FetchedSong}` or `{kind: "collection", platform, songs: [{platform, song_id, title, artists, album, duration_ms}]}` |
| POST | `/api/lyrics/song` | `{platform, song_id}` | `{kind: "song", song: FetchedSong}` |
| POST | `/api/projects/{pid}/lyrics/from-song` | `{platform, song_id}` | `LyricsPreview` (original track; translation/romanization offered as `extra_tracks`) |

`lyrics/parse` also accepts `prepared.json` (lyrics with readings). For project / alignment / reading-patch JSON it returns an `error` plus `route` naming where that file belongs.

`LyricsPreview` = `{preview_id, detected, warnings: [str], error: str|null, doc: LyricsDoc, extra_tracks: {kind: text}}`. When `error` is set (e.g. LRC mode without valid times) the preview cannot be applied; the UI must offer to add times or switch mode.
`FetchedSong` = `{platform, song_id, title, artists: [str], album, duration_ms, tracks: {original?, translation?, romanization?}, has_timestamps: {track: bool}}`.

## Lines and readings

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| PATCH | `/api/projects/{pid}/lines/{line_id}` | `{text?, sing?, kind?, translation?, voice?}` | `ProjectView` |
| POST | `/api/projects/{pid}/lines/merge` | `{line_ids}` | `ProjectView` |
| POST | `/api/projects/{pid}/lines/{line_id}/split` | `{at: int (char index)}` | `ProjectView` |
| PUT | `/api/projects/{pid}/lines/{line_id}/anchor` | `{abs_ms: int\|null, hard: bool, tolerance_ms}` | `ProjectView` |
| POST | `/api/projects/{pid}/readings/prepare` | `{overwrite_rule: bool}` | `ProjectView` + `report` |
| PUT | `/api/projects/{pid}/lines/{line_id}/segments/{segment_id}` | `{reading, units?: [str], confirm: bool}` | `ProjectView` |
| POST | `/api/projects/{pid}/ai/prompt` | `{line_ids?: [str]}` | `{prompt, snapshot_id, roundtrip_id}` |
| POST | `/api/projects/{pid}/ai/validate` | `{text}` (raw chat reply or JSON) | `{report_id, report: PatchReport}` |
| POST | `/api/projects/{pid}/ai/apply` | `{report_id, line_ids?: [str]}` | `ProjectView` |

`PatchReport` = `{ok: bool, snapshot_match: bool, warnings: [str], errors: [str], lines: [{line_id, status: "ok"\|"stale_text"\|"unknown_line"\|"locked_skipped"\|"invalid"\|"unchanged", reasons: [str], diff: [{surface, old_reading, new_reading, old_units: [str], new_units: [str]}]}]}`.

## Audio

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| POST | `/api/projects/{pid}/audio` | multipart `file`, form `role: original\|vocals\|instrumental` | `ProjectView` (+ stems get `sync_report`). The file may be a **video**: its first audio track is extracted losslessly (FLAC) and used; a video uploaded as the original is kept as `project.video` (with `audio_offset_s`) for re-muxing. |
| GET | `/api/projects/{pid}/audio/{asset_id}/playback.wav` | – | decoded PCM WAV (same decoder as alignment → identical time origin). Supports Range. |
| GET | `/api/projects/{pid}/audio/{asset_id}/peaks?per_second=200` | – | `{sample_rate, duration_ms, per_second, mins: [float], maxs: [float]}` (mono, first peak at 0 ms) |
| POST | `/api/projects/{pid}/separate` | `{preset, device?: "auto"\|"cpu"}` | `Job` (on success adds vocals + instrumental assets) |
| POST | `/api/projects/{pid}/mix/export` | `MixSettings` | `Job`; output `{filename, url, report}`; `url` downloads the WAV |

Mix rule (same in browser and export): `mix = master × (p/100·V + q/100·I)`; bus gain `min(1, 10^(-0.3/20)/peak)` when `limiter = normalize_peak`. Browser playback computes it with GainNodes; only the export applies a precomputed bus gain from the full-file peak (the UI shows the same number from `/mix/preview-gain`).

| POST | `/api/projects/{pid}/video/export` | `MixSettings` | `Job` (kind `video`); output `{filename, url, report}`: the original video's picture copied unchanged, the mix as its only soundtrack, at the original audio offset. Needs `project.video` and both stems. |
| POST | `/api/projects/{pid}/mix/preview-gain` | `MixSettings` | `{bus_gain, peak_before}` |

## Calibration (LRC mode)

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| POST | `/api/projects/{pid}/calibration/mark` | `{line_id, marked_ms}` | `ProjectView` |
| POST | `/api/projects/{pid}/calibration/shift` | `{user_shift_ms}` | `ProjectView` |
| POST | `/api/projects/{pid}/calibration/confirm-zero` | – | `ProjectView` |
| POST | `/api/projects/{pid}/calibration/check` | `{line_id, marked_ms}` | `ProjectView` (check residual in `calibration.checks`, warnings in `view.calibration_issues`) |
| POST | `/api/projects/{pid}/calibration/undo` | – | `ProjectView` |

## Alignment and results

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| POST | `/api/projects/{pid}/align` | `{line_ids?: [str], audio_role?: "original"\|"vocals", config?: partial AlignConfig}` | `Job` (output `{result_id}`) |
| GET | `/api/projects/{pid}/results/{rid}` | – | `AlignmentResult` (with fresh `stale`) |
| POST | `/api/projects/{pid}/results/import` | `{text}` (alignment.json content) | `ProjectView` + `result_id` (non-active; staleness recomputed) |
| POST | `/api/projects/{pid}/results/{rid}/activate` | – | `ProjectView` |
| PUT | `/api/projects/{pid}/results/{rid}/units/{uid}` | `{start_ms, end_ms, locked}` | `UnitTiming` |
| DELETE | `/api/projects/{pid}/results/{rid}/units/{uid}/manual` | – | `UnitTiming` |
| POST | `/api/projects/{pid}/results/{rid}/units/{uid}/lock` | `{locked}` | `UnitTiming` |
| POST | `/api/projects/{pid}/results/{rid}/units/{uid}/restore` | `{manual: ManualEdit\|null}` | `UnitTiming` (undo/redo support) |
| POST | `/api/projects/{pid}/results/{rid}/adopt` | `{from_result_id?, candidate_id?, line_ids: [str]}` | `AlignmentResult` (copies non-locked unit times of the lines from a local rerun or a candidate; locked units untouched) |

A local rerun (`align` with `line_ids`) creates a new partial result with `parent_result_id`; it never overwrites the parent. The UI compares and adopts per line.

## Export

| Method | Path | Response |
| --- | --- | --- |
| GET | `/api/projects/{pid}/export/{fmt}?result_id=&download=1` | file download (Content-Disposition) |
| GET | `/api/projects/{pid}/export/{fmt}?result_id=` | `{filename, media_type, content, warnings}` |

`fmt` ∈ `alignment, prepared, project, csv, lrc-line, lrc-unit, lrc-calibrated`; stems via `/audio/{asset_id}/playback.wav`, mix via `/mix/export`.
