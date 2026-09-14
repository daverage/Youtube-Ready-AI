# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`youtube-ready-ai` is a standalone, local-first CLI (derived from `roughcut-ai`) that turns one local video into a complete YouTube publishing pack — transcript, captions, chapters, description, title suggestions, hashtags, and thumbnail ideas — without modifying or uploading the source media. No paid API or cloud upload is used; Ollama is used locally for chapters/metadata/hashtag generation, with deterministic heuristic fallbacks if Ollama is unavailable.

## Commands

```bash
# Setup
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,web,thumbnails]"
ollama pull qwen2.5:7b-instruct   # optional, for AI-generated chapters/metadata/hashtags

# Run (CLI)
youtube-ready /path/to/video.mp4
youtube-ready video.mp4 --quality accurate
youtube-ready video.mp4 --language en --context "A review of a new guitar pedal"
youtube-ready video.mp4 --no-ai              # skip Ollama, use heuristic fallback
youtube-ready video.mp4 --no-thumbnails      # skip frame extraction/thumbnail ideas

# Run (local web UI)
youtube-ready-web                            # serves http://127.0.0.1:8787 and opens it

# Test
pytest
pytest tests/test_chapters.py               # single file
pytest tests/test_chapters.py::test_name -v # single test
```

There is no lint/format command configured in `pyproject.toml`.

## Architecture

The pipeline is a straight-line sequence orchestrated by `src/youtube_ready/pipeline.py::run_pipeline`, invoked by the Click CLI in `cli.py`:

1. **`transcribe.py`** — `transcribe_video` runs `faster-whisper` locally (imported lazily; `WhisperModel` is `None` if not installed, raising `RuntimeError` at call time). `QUALITY_PRESETS` maps `fast`/`balanced`/`accurate` to Whisper model size + beam size. Produces a `Transcript` (Pydantic model in `models.py`) of timestamped `TranscriptSegment`s with word-level timing.
2. **`captions.py`** — pure functions that re-chunk transcript segments into caption `Cue`s (`build_cues`, capped by `max_chars`/`max_seconds`) and render `.srt`/`.vtt`/plain-text transcript output. No I/O.
3. **`metadata.py`** — `generate_metadata` produces `PublishingMetadata` (titles, description, hashtags, chapters). If a model name is given, it prompts a local LLM runtime with the full timestamped transcript, or a chunked LLM-summarized version if the transcript exceeds `MAX_SOURCE_CHARS`. Two backends are supported via `backend`/`_backend_call`: `ollama` (default, `_call_ollama`) and `mlx` (`_call_mlx`, runs a Hugging Face model in-process on Apple Silicon). The `mlx` backend itself tries two libraries: `_load_mlx` first attempts `mlx-lm` (plain text models); if that raises (typically an unsupported architecture), it falls back to `mlx-vlm` for vision-language models like Gemma 3n, which `mlx-lm` cannot load — the resolved `(kind, bundle)` is cached in `_MLX_MODEL_CACHE` per model name so repeated calls (e.g. transcript summarization chunks) don't reload it. `list_models`/`list_ollama_models`/`list_mlx_models` query each backend's available models for the web UI's dropdown — `list_mlx_models` scans the local Hugging Face cache (`huggingface_hub.scan_cache_dir`) rather than any registry, so it reflects whatever's actually downloaded. Any exception (backend unreachable/not installed, bad JSON, schema mismatch) falls through to `heuristic_metadata`, which derives a title/description/hashtags from the opening transcript segments — **the pipeline is designed to never fail just because the local model is unavailable**. Hashtags are always derived from the transcript (by the model, or by `_hashtag_seed`'s keyword heuristic) — there is no internet/trend lookup, since that would break the local-first, no-cloud-calls design.
4. **`chapters.py`** — chapter generation/validation is decoupled from metadata generation. `chapters_are_valid` enforces YouTube-style rules (start at 0:00, ≥3 chapters, ascending, each chapter ≥10s including the last). `normalize_chapters` sanitizes LLM-proposed chapters against these rules, falling back to `fallback_chapters` (evenly spaced chapters seeded from nearby transcript text) if invalid. Videos shorter than 30s cannot satisfy these rules, so chapters come back empty and `metadata.chapters_note` records why.
5. **`thumbnails.py`** — `extract_candidate_frames` pulls a handful of candidate thumbnail frames from the video in a single `ffmpeg` pass: the `select='gt(scene,...)'` filter makes ffmpeg itself decide, during decode, which frames to actually encode/write (scene changes), so the source is never dumped frame-by-frame to disk; it falls back to evenly-spaced samples if too few scene changes are found (e.g. static video), and returns `[]` gracefully if `ffmpeg` isn't on PATH. `select_top_frames` scores candidates with OpenCV (Laplacian-variance sharpness + Haar-cascade face detection, the `thumbnails` optional dependency group) and picks the highest-scoring frames spaced apart in time; any OpenCV failure (not installed, a broken build) degrades to unscored/evenly-picked frames rather than raising. `generate_thumbnail_ideas` follows the same LLM/heuristic-fallback pattern as `metadata.py`, proposing 3 thumbnail concepts (overlay text, visual description, a chosen candidate frame, and a text-to-image prompt) from the transcript — never from live trend/image data.
6. **`pipeline.py`** ties it together: creates a timestamped output directory (`_session_dir`, collision-safe, sanitizes the video filename), writes all output files, and appends the rendered chapter block into `description.txt`.

Key invariant to preserve when editing: metadata/chapter/thumbnail generation must always degrade gracefully to a heuristic/fallback path rather than raising, since the CLI's whole value proposition is "always produces a usable package even offline."

**opencv-python vs. opencv-python-headless:** the `thumbnails` extra installs plain `opencv-python`, not `-headless`, and is pinned below 5.0 (`>=4.9,<5`) — `mlx-vlm` (the `mlx` extra) already depends on `opencv-python`, so pulling in `-headless` too makes both packages clobber each other's shared libraries and silently breaks `cv2` entirely; separately, the 5.0.0 wheel dropped the `CascadeClassifier` (face detection) binding outright. Don't "fix" the pin without re-verifying `cv2.CascadeClassifier` exists in whatever version you pick.

### Output files (per run, in a new timestamped directory)

`transcript.txt`, `transcript.json`, `captions.srt`, `captions.vtt`, `chapters.txt`, `description.txt`, `titles.txt`, `hashtags.txt`, `thumbnails/` (candidate frame images), `thumbnail_ideas.txt`, `thumbnail_ideas.json`, `youtube_metadata.json`, `youtube_package.md`.

### Web interface (`src/youtube_ready/web/`)

`jobs.py` wraps `run_pipeline` for async use: `submit_job` copies an uploaded file into a temp dir and hands it to a single-worker `ThreadPoolExecutor` (Whisper/local LLMs are heavy, so jobs run one at a time), and `Job` fans progress messages out to subscribers via per-client `queue.Queue`s for Server-Sent Events. `Job.as_dict()` also summarizes `result.thumbnails` (source, fallback reason, and per-idea concept/overlay text/prompt/frame filename) so the frontend gets everything it needs for the gallery tab in the same "done" event, without a second round trip. `server.py` is a small FastAPI app (`POST /api/jobs`, `GET /api/jobs/{id}/events` for SSE progress, `GET /api/jobs/{id}/files/{name}` to preview/download top-level outputs, `GET /api/jobs/{id}/thumbnails/{filename}` to serve individual candidate-frame images from the run's `thumbnails/` subdirectory, `GET /api/models?backend=...` to list available models for the dropdown) serving a static single-page frontend (`static/index.html` + `styles.css` + `app.js`, no build step/framework — plain JS). The model field is a real `<select>` populated live from `/api/models`; it shows whatever models exist for the chosen backend and nothing is user-typed. `youtube-ready-web` (console script) launches it with uvicorn and opens a browser. `web`, `mlx`, and `thumbnails` are separate optional dependency groups so the base CLI install stays minimal.

**No-model submission must not be blocked client-side.** The frontend's "AI chapters & metadata" toggle defaults on, and if Ollama/MLX isn't running the model `<select>` is legitimately empty — `app.js` must let that submit anyway (`modelsAvailable` tracks whether the dropdown ever had real options; the submit guard only fires when it did but nothing got selected). `server.py`/`jobs.py` already turn a blank `metadata_model` into `None`, which `generate_metadata`/`generate_thumbnail_ideas` treat exactly like `--no-ai` — the same heuristic fallback path the CLI uses. Don't reintroduce a hard "pick a model first" block; it defeats the whole point of the fallback design for the one client (the web UI) most likely to be used by someone who's never touched Ollama.

**Session management (past runs on disk).** A "session" is just a previous run's output directory; its directory name doubles as the job id once reloaded, so the existing `/api/jobs/{id}/...` endpoints work unmodified for it. `jobs.py`'s `list_sessions`/`load_session`/`delete_session` operate directly on disk (a directory counts as a completed session once it has `youtube_metadata.json`) and don't require the original in-memory `Job` to still exist — this is what lets sessions survive a server restart. `load_session` reconstructs a `PipelineResult` by parsing `transcript.json`/`youtube_metadata.json`/`thumbnail_ideas.json` back into their Pydantic models and looking for a `source.*` file for video preview; `_resolve_session_dir` rejects any name that isn't a direct child of the configured output root (blocks `../` traversal). `server.py` exposes these as `GET /api/sessions`, `POST /api/sessions/{name}/load`, `DELETE /api/sessions/{name}` (all take an `output_root` query param, defaulting to `app.state.default_output_root`). The frontend lists sessions above the upload form and refreshes them whenever that panel becomes visible.

One-click launchers at the repo root (`start.command` for macOS, `start.bat` for Windows, `start.sh` for Linux) create/reuse a `.venv`, install the `web` extras on first run only, and start `youtube-ready-web`.

### Testing conventions

Tests are colocated per module (`tests/test_captions.py`, `test_chapters.py`, `test_metadata.py`, `test_pipeline.py`) and mock external dependencies (Whisper, Ollama) rather than invoking them — `metadata.py`'s `generate_metadata` takes an injectable `call` parameter specifically to support this.
