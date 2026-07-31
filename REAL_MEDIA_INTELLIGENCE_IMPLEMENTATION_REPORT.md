# Real Media Intelligence Implementation Report

## Providers

- `LocalMediaAnalyzer` uses local FFmpeg/FFprobe to persist bounded speech, silence, long-silence, scene, shot-change, loud/quiet-audio, audio-peak, motion, and motion-spike facts. It never stores frames or audio samples.
- `FasterWhisperTranscriptionProvider` uses local `faster-whisper` 1.2.1 lazily. It supports model/device/compute type, forced or detected language, VAD, beam size, word timestamps, and a model cache. Provider metadata, language confidence, segment confidence, and bounded word timestamps are persisted; transcript content is not logged at normal level.
- `TesseractOcrProvider` is local, bounded, and disabled by default. It de-duplicates adjacent text and OCR failure is nonfatal.

## Timeline, versioning, and migration

`real-media-v1` is the default requested version and coexists with, rather than overwrites, `foundation-v1`. Timeline normalization clamps timestamps, removes invalid and duplicate facts, orders deterministically, caps events, and records truncation warnings.

Migration `0014_real_media_progress` adds only `video_analyses.current_stage`, `video_analyses.progress_percent`, and `transcript_segments.metadata_json`. No released migration changed and no new competing analysis schema was created.

The opportunity scorer was not modified. It consumes the existing persisted speech, silence, scene, audio, motion, transcript, OCR, and event types.

## Worker, API, and Discord

- The existing analysis task selects the requested version instead of hard-coding `foundation-v1`; progress/stage are persisted and cancellation is observed between bounded stages.
- Start/rerun accepts optional `analysis_version`. Timeline, transcript, and event reads support bounded `offset`/`limit` pagination.
- The Discord project dashboard now shows provider/version, stage/progress, warnings, speech/silence duration, motion spikes, and audio peaks. It includes Start Real Analysis plus compact paged Transcript and Timeline viewers.

## Dependencies and configuration

`faster-whisper>=1.1,<2` is installed. Docker installs FFmpeg and Tesseract. The model cache is mounted at `/viralforge-data/models`; no model downloads during API startup, and the lightweight `tiny` model downloads only on the first selected worker job.

Typed settings and `.env.example` cover transcription, VAD, word timestamps, model cache, silence, scene, audio, motion, OCR, timeouts, and timeline cap.

## Verification

- Ruff passed.
- mypy passed for 59 application source files.
- pytest passed: 87 tests.
- SQLite Alembic-to-ORM schema drift passed.
- PostgreSQL isolated-database upgrade -> downgrade -> re-upgrade passed and ended at `0014_real_media_progress`; production PostgreSQL is also at that revision.
- Docker API/worker images rebuilt successfully. API health/readiness, PostgreSQL/Redis health, Celery ping/task registration, and Discord gateway reconnect all passed.

### Controlled live media test

A locally generated 16-second video contained spoken English, deliberate silence, a red-to-moving-test-pattern scene transition, motion, and audible speech. A temporary `faster_whisper`-configured worker processed the actual Celery-delivered job: English was detected; four transcript segments, 39 timeline segments, and 17 events were persisted. The result included speech, silence, scene, loud/quiet-audio, and motion ranges plus shot-change, audio-peak, and motion-spike events.

The unchanged opportunity worker generated one ranked opportunity from that real analysis. Approval through the existing renderer made exactly one `SUCCEEDED` clip. It remained `PENDING` / `NOT_QUEUED`; repeated generation returned the same clip, the posting queue stayed empty, and nothing was published.

## Exact limitations

- The populated local `.env` was deliberately not overwritten. It still selects the legacy `mock` transcript provider. Real FFmpeg media signals are active because the legacy `ffprobe` setting maps to the local analyzer, but normal jobs require `VIRALFORGE_ANALYSIS_TRANSCRIPT_PROVIDER=faster_whisper` and a worker restart to enable transcription.
- OCR remains opt-in: set `VIRALFORGE_ANALYSIS_OCR_ENABLED=true` and `VIRALFORGE_ANALYSIS_OCR_PROVIDER=tesseract` when needed.
- The initial selected faster-whisper model download needs network access. Operators should pre-warm the mounted cache for offline deployments.

## Next recommended milestone

AI content package generation for approved rendered clips only: titles, hooks, captions, hashtags, descriptions, platform variants, thumbnail text, and content warnings. Do not add publishing in that milestone without a separate authorization and review design.
