# File Transcription Redesign

**Date:** 2026-04-26
**Scope:** DashScribe's File tab only (no changes to Dictation, ClassNote, or Meeting modes).
**Status:** Design — pending implementation plan.

---

## 1. Goals

Bring the File tab from "paste a path, get a wall of text" to a 2026-tier local file-transcription experience comparable to MacWhisper. Specifically:

1. **Modern upload UX**: full-area drop zone + Browse + URL paste; no required text input; auto-start on drop.
2. **AI-grade speaker diarization** (sherpa-onnx default; pyannote community-1 as opt-in download for premium accuracy).
3. **Best-in-class English ASR** for file mode by adding NVIDIA Parakeet TDT 0.6B v3 (via `parakeet-mlx`) as a selectable engine alongside Whisper.
4. **Preserve and surface segment data** — stop discarding Whisper's per-segment timestamps and per-word data.
5. **Rich post-transcription view** — speaker-labeled paragraphs, click-to-seek timestamps, audio player with karaoke-style word highlighting, in-place editing, click-to-rename speakers.
6. **Multi-format export** — TXT, Markdown (with speaker headers + timestamps), SRT, VTT, JSON, DOCX.
7. **Pre-transcription knobs** — language (auto + manual), task (transcribe/translate-to-English), diarization on/off + speaker hint, quality preset (Fast/Balanced/Best), custom vocabulary, advanced options (initial prompt, timestamp granularity, temperature, beam size).

## 2. Non-goals (deferred to Phase 2)

- AI summary, chapter detection, action-item extraction.
- Chat-with-transcript.
- Cross-file persistent speaker library ("this is the same Alex who appeared in last week's recording").
- Live edit-text-edits-audio (Descript-style).
- Custom export-format builder (MacWhisper v13 feature).
- Translation to non-English target languages with timestamp preservation (Whisper task=translate covers English-only target).
- Per-app system audio capture is unchanged — it lives in the Meeting tab.

These are explicitly out of scope so the Phase 1 surface stays focused. The unified JSON segment payload defined below is the contract that will feed all of them later.

## 3. The unified transcript payload

The single most important architectural change. Today the backend returns a flat string. After this change, every transcription run produces:

```jsonc
{
  "version": 1,
  "engine": "whisper-turbo" | "whisper-large" | "parakeet",  // short names; full model id resolved at load time
  "language": "en",                 // detected or forced
  "duration_seconds": 1834.2,
  "audio_path": "/.../source.mp3",  // original file
  "created_at": "2026-04-26T18:22:11Z",
  "speakers": [
    // assigned by transcript_assembler from a fixed 8-color palette in source order
    { "id": "S1", "label": "Speaker 1", "color": "#5B8DEF" },
    { "id": "S2", "label": "Speaker 2", "color": "#F08C5B" }
  ],
  "segments": [
    {
      "id": 0,
      "speaker_id": "S1",
      "start": 0.32,
      "end": 4.81,
      "text": "Hey, thanks for joining today.",
      "no_speech_prob": 0.01,
      "avg_logprob": -0.21,
      "words": [
        { "text": "Hey,",   "start": 0.32, "end": 0.55, "prob": 0.99 },
        { "text": "thanks", "start": 0.60, "end": 0.91, "prob": 0.97 },
        // ...
      ]
    }
    // ...
  ]
}
```

This is persisted as a sibling `.json` file alongside any user-chosen export and is the source of truth that every export format and the post-transcription view both read. Parakeet output is normalized into this shape so the frontend doesn't branch on engine.

`words` may be empty when the user picks timestamp granularity = `sentence` or `none`. `speakers` may have one synthetic entry when diarization is off.

## 4. UI design

Three states for the File tab: **empty**, **transcribing**, **result**. All three live in the existing `#file-mode` page. No modal dialogs.

### 4.1 Empty state

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│                       [ icon ]                               │
│              Drop an audio or video file here                │
│                                                              │
│              [ Browse… ]   or paste a URL                    │
│              [____________________________________]          │
│                                                              │
│                  Try a sample recording                      │
│                                                              │
│   Supports MP3, WAV, M4A, MP4, MOV, MKV, and more —          │
│   transcribed locally on your Mac. Nothing leaves            │
│   the device.                                                │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

- The entire `#file-mode .page-content` area is the drop target. A subtle dashed border appears on `dragenter`; the border becomes solid + tinted on a valid `dragover`.
- Drag-over a folder or unsupported format → red border + tooltip "Unsupported format".
- Multi-file drop is queued (see §4.4 Batch).
- "Browse" opens the existing native dialog (`/api/browse-file`).
- URL field accepts http(s) URLs; on Enter, hands off to `yt-dlp` extractor (added as optional dep — see §6.3).
- "Try a sample recording" loads a bundled 8–15s clip from `static/samples/sample-en.m4a`. Lets a first-time user see the post-transcription view without finding a file. (Inspired by AssemblyAI Playground.)

### 4.2 Transcribing state

A horizontal progress card replaces the drop zone. Multi-stage label:

```
[ ●●○○○ ]  Extracting audio…       (videos only)
[ ●●●●○ ]  Transcribing 2:14 / 8:30  •  Parakeet TDT v3
[ ●●●●● ]  Diarizing 4 speakers…
                                    [ Cancel ]
```

- Progress is real where we can compute it (`elapsed / duration` once duration is known); indeterminate bar otherwise.
- Stage labels are emitted from the backend over the existing WebSocket as `file_progress` messages with `stage` + `percent` + `message`.
- User can navigate to other tabs and back — the page restores state from a `currentJob` JS variable. There is no native macOS notification on completion in Phase 1 (Phase 2 if useful).
- Cancel sends `{action: "cancel_file_job", job_id}`; backend cancels the running thread.

### 4.3 Result state — two-pane layout

```
┌─────────────────────────────────────────────────┬───────────────┐
│  source.mp3 — 30:42 — 2 speakers      [⌄][📋]   │   OPTIONS     │
├─────────────────────────────────────────────────┤               │
│                                                 │  Engine       │
│  ● Speaker 1   00:00:03                         │  Language     │
│  Hey, thanks for joining today. I wanted to…    │  Diarization  │
│                                                 │  Quality      │
│  ● Speaker 2   00:00:12                         │  Vocabulary   │
│  Sure, glad to be here. So before we start…     │  ► Advanced   │
│                                                 │               │
│  ● Speaker 1   00:00:34                         │   EXPORT      │
│  Right, on that point — there's a question…     │  ○ TXT        │
│                                                 │  ○ Markdown   │
│  …                                              │  ○ SRT        │
│                                                 │  ○ VTT        │
│                                                 │  ○ DOCX       │
│                                                 │  ○ JSON       │
│                                                 │  [ Save as… ] │
│                                                 │  [ Copy all ] │
│                                                 │               │
│                                                 │   AI          │
├─────────────────────────────────────────────────┤  (Phase 2)    │
│  ◀◀  ▶  ▶▶   00:01:32 / 30:42  ━━━━●━━━━━━━━   │               │
└─────────────────────────────────────────────────┴───────────────┘
```

- **Header strip**: file name, duration, speaker count. `[⌄]` reveals re-transcribe (with current option panel values). `[📋]` copies plain text to clipboard.
- **Transcript pane** (scrollable):
  - Each speaker turn is a paragraph headed by a colored chip (`● Speaker 1`) and a clickable timestamp.
  - Click the chip → inline rename input → on Enter, applies to **every** segment with that `speaker_id` and persists to the JSON.
  - Click a timestamp → seeks the audio player.
  - Click any **word** → seeks to that word's `start` (when word timestamps available).
  - During playback, the currently-spoken word gets a soft background highlight (karaoke).
  - Words with `prob < 0.5` (or `no_speech_prob > 0.4` on the segment) get a faint dotted underline (Descript pattern).
  - The transcript is `contenteditable` with `plaintext-only`. Edits are saved to the in-memory segment payload on blur; the underlying `audio_path` and timestamps are not touched.
- **Options sidebar** (collapsible): same controls as the empty-state pre-options, but now with a "Re-transcribe" button at the top that re-runs with the new settings. Width 280px; Cmd+0 toggles.
- **Audio player** (pinned bottom): play/pause, ±10s, scrubber, current time / total. Standard HTML5 `<audio>` is sufficient.

### 4.4 Batch queue

Dropping multiple files at once enqueues them. A small queue chip appears in the header strip ("3 files in queue"). The currently-displayed file is the first one finished; clicking the chip opens a queue popover listing each file with its status (queued / transcribing / done / error). Switching files swaps the result view. Queue persists in memory only (cleared on page reload); not in scope for Phase 1 to persist across app restarts.

### 4.5 Keyboard shortcuts

- `Cmd+0` — toggle sidebar
- `Cmd+E` — focus the Export Save-as button
- `Cmd+C` with no selection — copy full transcript
- `Space` — play/pause audio
- `←` / `→` — ±5s
- `1`–`9` — with a text selection that spans one or more turns, reassigns those turns to speaker N (capped at 9; speakers beyond require clicking the chip)

In-transcript search (`Cmd+F`) is deferred to Phase 2.

### 4.6 Error states

- Unsupported format on drop → toast + drop zone remains.
- URL fetch failure → inline red message under the URL field.
- Transcription failure (e.g. ASR crash, ffmpeg missing) → result state shows error card with the underlying message and a "Retry" button.
- Diarization failure → don't fail the whole job; fall back to a single synthetic speaker and show a yellow banner: "Diarization failed; transcript only."

## 5. Pre-transcription options model

All options live in a single `FileJobOptions` object passed from frontend to backend per job and persisted as the user's defaults in `~/.dashscribe/config.json`.

```jsonc
{
  "engine": "auto" | "parakeet" | "whisper-turbo" | "whisper-large",  // matches payload.engine after auto resolves
  "language": "auto" | "en" | "es" | ...,    // ISO 639-1
  "task": "transcribe" | "translate",        // translate = → English
  "diarization": {
    "enabled": true,
    "engine": "sherpa-onnx" | "pyannote-community-1",
    "speaker_count": "auto" | 1..10
  },
  "quality_preset": "fast" | "balanced" | "best",  // maps to engine + beam_size
  "custom_vocabulary": ["DashScribe", "MLX", "..."],
  "advanced": {
    "initial_prompt": "",
    "timestamp_granularity": "none" | "sentence" | "word",
    "temperature": 0.0,
    "beam_size": 5,
    "condition_on_previous_text": false
  },
  "output_dir": null   // null = same dir as source file
}
```

Defaults: `engine=auto`, `language=auto`, `task=transcribe`, `diarization.enabled=true`, `diarization.engine=sherpa-onnx`, `quality_preset=balanced`, `timestamp_granularity=sentence`.

`engine=auto` → maps via `quality_preset`: fast→parakeet, balanced→whisper-turbo, best→whisper-large. The user can override the mapping by picking an engine explicitly.

Custom vocabulary auto-merges the global Dictionary file (`~/.dashscribe/dictionary.txt`) with per-job additions; the merged list goes into Whisper's `initial_prompt` (after the existing `PUNCTUATION_STYLE_PROMPT`) or Parakeet's per-utterance hot-words list.

## 6. Backend architecture

### 6.1 New / modified files

| File | Status | Purpose |
|---|---|---|
| `transcriber.py` | Modified | Add `transcribe_segments()` returning structured segment+word data; keep `transcribe()` as compat wrapper |
| `parakeet_transcriber.py` | New | `ParakeetTranscriber` mirroring `WhisperTranscriber` API; uses `parakeet-mlx` |
| `engine_registry.py` | New | Picks transcriber instance by engine name; lazy-loads to avoid loading both models |
| `diarizer.py` | New | `Diarizer` wrapping sherpa-onnx pipeline; `diarize(audio_path, hint=None) -> list[SpeakerSegment]` |
| `diarizer_pyannote.py` | New | Optional opt-in pyannote community-1 path; only loaded if installed |
| `transcript_assembler.py` | New | Merges ASR segments + diarizer segments into the unified JSON payload by overlap-aligning speaker turns to word timestamps |
| `exporter.py` | New | `to_txt`, `to_markdown`, `to_srt`, `to_vtt`, `to_docx`, `to_json` from the unified payload |
| `file_job.py` | New | `FileJob` dataclass + `FileJobRunner` orchestrating the pipeline (extract → ASR → diarize → assemble → export) with progress callbacks |
| `app.py` | Modified | New REST + WebSocket endpoints (see §6.2); `transcribe_file` action removed |
| `config.py` | Modified | Add accessor for `file_job_defaults` (FileJobOptions blob) |
| `static/index.html` | Modified | Replace `#file-mode` content with new layout (drop zone, sidebar, audio player, transcript container) |
| `static/file.js` | New | All file-mode logic split out of `app.js` (currently 700+ lines and growing) |
| `static/file.css` | New | Or appended to `style.css` — drop zone, sidebar, transcript, player styles |
| `static/samples/sample-en.m4a` | New | 8–15s spoken sample for "Try a sample" link |
| `tests/test_diarizer.py` | New | Diarizer correctness on a known fixture (e.g., AMI 2-spk clip) |
| `tests/test_transcript_assembler.py` | New | Speaker-turn alignment edge cases |
| `tests/test_exporter.py` | New | All six export formats from a fixture payload |
| `tests/test_file_job.py` | New | End-to-end pipeline with a tiny fixture audio file |
| `requirements.txt` | Modified | Add `parakeet-mlx`, `sherpa-onnx`, `python-docx`, `yt-dlp` (optional) |

### 6.2 API surface

WebSocket actions (replacing the single `transcribe_file`):

| Inbound action | Payload | Outbound messages |
|---|---|---|
| `start_file_job` | `{path?, url?, options: FileJobOptions}` | `file_job_started`, `file_progress` (stream), `file_job_done` |
| `cancel_file_job` | `{job_id}` | `file_job_cancelled` |
| `update_speaker_label` | `{job_id, speaker_id, label}` | `speaker_label_updated` (echo) |
| `save_transcript_edits` | `{job_id, segments}` | `transcript_saved` |

REST:

| Method + Path | Purpose |
|---|---|
| `GET /api/browse-file` | (existing — unchanged) |
| `GET /api/file-job/:id/payload` | Returns the unified JSON payload (used to restore state on page reload) |
| `POST /api/file-job/:id/export` | Body: `{format, dest_path}` → writes the file, returns `{path}` |
| `GET /api/file-job/:id/audio` | Streams the source audio (or the extracted-audio temp file for videos) for the `<audio>` element |
| `GET /api/file-job/options-defaults` | Returns saved defaults |
| `PUT /api/file-job/options-defaults` | Saves new defaults |

### 6.3 URL ingestion (yt-dlp)

`yt-dlp` becomes an optional dependency. If present and the user pastes a URL, the backend downloads bestaudio, hands the local path to the same pipeline. If absent, the URL field is hidden and a small "Install URL support" link in the empty state explains the optional install. Bundle size impact: `yt-dlp` is a single ~3 MB Python file; safe to include.

### 6.4 Engine pipeline

```
                        ┌──────────────────────────────┐
                        │  FileJobRunner               │
                        │                              │
  drop / URL / browse → │  1. resolve to local path    │
                        │  2. probe duration (ffprobe) │
                        │  3. extract audio (if video) │  emits file_progress
                        │  4. ASR  →  raw segments     │
                        │  5. Diarization → spk turns  │  (parallelizable with 4)
                        │  6. assemble unified payload │
                        │  7. write .json sidecar      │
                        │  8. write user-chosen export │
                        └──────────────────────────────┘
                                      │
                                      ▼
                              file_job_done payload
```

Steps 4 and 5 can run in parallel (they share read-only access to the same audio file). The runner uses `concurrent.futures.ThreadPoolExecutor(max_workers=2)` and joins before assembly.

### 6.5 Diarization integration (sherpa-onnx)

Models bundled at first run (lazy download to `~/.cache/dashscribe/diarizer/`):
- `sherpa-onnx-pyannote-segmentation-3.0` (~6 MB)
- `3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced` (~28 MB; the standard CAM++ embedding model used by sherpa-onnx examples)

Wrapped behind `Diarizer` with a clean Python interface; sherpa-onnx is loaded lazily on first call to keep app startup fast.

`speaker_count` hint maps to sherpa-onnx's `num_clusters` parameter; `auto` uses the threshold-based clustering default.

### 6.6 Pyannote opt-in path

Behind a settings toggle ("Enhanced diarization (premium)"). When toggled on:
1. Show a download confirmation modal: "Downloads ~700 MB of additional models. Continue?"
2. Install `pyannote.audio` + CPU-only `torch` (`pip install --target ~/.dashscribe/pyannote pyannote.audio "torch>=2.6,<3" --index-url https://download.pytorch.org/whl/cpu`).
3. Re-host community-1 weights ourselves (CC-BY-4.0 with attribution) at a stable URL so users don't need a HuggingFace token. Document this in README and in the settings UI ("Model: pyannote/speaker-diarization-community-1 — CC-BY-4.0").
4. `diarizer_pyannote.py` is dynamically imported only when the option is enabled, so the main app's import graph never touches torch.

If the install fails or the user lacks disk space, fall back to sherpa-onnx with a toast.

### 6.7 Parakeet integration

`parakeet-mlx` exposes `from_pretrained("mlx-community/parakeet-tdt-0.6b-v3")` returning a model with `.transcribe(audio_path)` returning text + per-token timing. The `ParakeetTranscriber` class mirrors `WhisperTranscriber.transcribe_segments()` so the rest of the pipeline doesn't branch.

The model is downloaded on first use of the Fast preset, with the same warmup pattern as the existing Whisper transcriber. Hot-words / custom vocabulary maps to Parakeet's keyword boosting API (if available in the version we ship; otherwise fall back to inserting them as a post-hoc replacement pass).

## 7. Data flow end-to-end

```
User drop / URL paste / Browse
        │
        ▼
[file.js] reads File / URL → POST options + path/url over WS
        │
        ▼
[app.py] WebSocket "start_file_job" → spawns FileJobRunner thread
        │
        ▼
[file_job.py] runs pipeline, emits file_progress → WS → file.js progress UI
        │
        ▼
[transcript_assembler.py] builds unified payload
        │
        ▼
[file_job.py] writes <stem>.json sidecar + chosen export → emits file_job_done
        │
        ▼
[file.js] receives payload, swaps to result view, renders transcript + sidebar +
          audio player; subsequent edits saved via "save_transcript_edits"
          and re-exports go through "/api/file-job/:id/export"
```

## 8. Migration & compatibility

- The old `transcribe_file` WS action is **removed**, not aliased — the only consumer is the file-mode UI we are replacing in the same change.
- `WhisperTranscriber.transcribe()` and `transcribe_array()` keep their current signatures (used by Dictation, ClassNote, Meeting). A new `transcribe_segments()` method is added returning the structured payload. The two existing methods become thin wrappers around `transcribe_segments()` that join `segments[*].text`.
- History entries from old file transcriptions remain readable (they're plain `text`); new file entries gain a `payload_path` column pointing to the JSON sidecar. Add a column with a nullable migration; old rows just have `payload_path = NULL`.
- The `_clean_hallucination` regex remains active for Whisper paths; Parakeet paths skip it (Parakeet doesn't produce that failure mode).

## 9. Testing strategy

- **Unit:** `transcript_assembler` overlap math (no overlap, exact overlap, partial, multi-speaker single segment), `exporter` golden-file tests for each format using a fixed fixture payload, `Diarizer` on a 2-speaker AMI clip checks `len(speakers) == 2`.
- **Integration:** `test_file_job.py` runs the full pipeline on a 5–10s bundled fixture (one of the existing test audio files in `tests/fixtures/`) with diarization both on and off, asserts payload shape and that the SRT export's last timestamp ≈ audio duration.
- **Regression:** existing 1088 tests must pass unchanged. The Dictation/ClassNote/Meeting flows go through the unchanged `transcribe()` / `transcribe_array()` API.
- **Manual UAT** (must pass before shipping):
  1. Drop a 2-min MP3 → result appears in <30s with diarized speakers.
  2. Drop an MP4 → audio is extracted, transcript is produced, no leftover temp file.
  3. Paste a YouTube URL → audio downloads, transcribes.
  4. Click "Try a sample" → result view appears with the bundled clip.
  5. Drop 3 files at once → queue chip appears, all complete sequentially.
  6. Rename "Speaker 1" to "Alex" → all 17 turns update; reload page → name persists.
  7. Click a word → audio seeks; press Space → plays from there with karaoke highlight.
  8. Edit a word in the transcript → blur → reload → edit persists.
  9. Export to each of the 6 formats → each opens in its native app and looks right.
  10. Toggle "Enhanced diarization" → ~700 MB download → re-run a job → speaker labels improve qualitatively on a hard clip.
  11. Cancel mid-job → UI returns to empty state cleanly, no orphan temp files.
  12. Drop unsupported file (`.zip`) → red border + tooltip, no crash.

## 10. Risks & open decisions

- **Bundle size:** Parakeet adds ~600 MB on first use of Fast preset; sherpa-onnx adds ~30 MB total models + ~15 MB native binary. Both are downloaded on demand, not bundled in `DashScribe.app`. The base `.app` size **decreases slightly** because nothing in the existing bundle changes.
- **py2app friction with sherpa-onnx:** sherpa-onnx ships native binaries; needs to be in `packages` list (not zipped), same pattern as `_sounddevice_data` and onnxruntime. Validated approach.
- **Pyannote weights re-hosting (CC-BY-4.0):** legally fine with attribution. Practical risk: hosting + bandwidth. Mitigation: GitHub Releases asset on the DashScribe repo, ~700 MB download, free-tier limits should comfortably cover it for a niche tool.
- **`parakeet-mlx` hot-words API:** need to verify the exact API in the released version we depend on. Plan-stage spike before implementation.
- **Auto-engine mapping:** if Parakeet is markedly worse on a user's actual content, "Fast" feels broken. Mitigation: surface the engine name in the result-state header strip so the user understands what produced the output and can switch.
- **Karaoke highlighting performance** for 1+ hour transcripts: rendering 30k words with per-word timing handlers is fine but `requestAnimationFrame` should drive the highlight, not per-word listeners. Spec-level concern only.
- **Browser audio for huge files:** the `<audio>` element streams from the local server fine for any reasonable file. For >2GB videos, `Range` request support in the audio endpoint is required.

## 11. Build sequencing (high level — full task list goes in the implementation plan)

1. Refactor `WhisperTranscriber` to emit structured segments; add `transcribe_segments()`. All existing tests pass.
2. Add `Diarizer` (sherpa-onnx) + tests.
3. Add `transcript_assembler` + tests.
4. Add `exporter` + tests.
5. Add `FileJob` runner + WS/REST surface; old `transcribe_file` removed.
6. Rebuild `#file-mode` UI: empty/transcribing/result states + sidebar + audio player + drop zone + URL paste.
7. Add `parakeet_transcriber` and engine registry; wire into FileJob.
8. Add `yt-dlp` URL ingestion.
9. Add pyannote community-1 opt-in install + downloader.
10. Settings persistence for FileJobOptions defaults.
11. UAT pass; build app via `build_app.sh` and verify py2app bundle.
