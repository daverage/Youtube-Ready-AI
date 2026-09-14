# Repository Guidelines

## Project Structure & Module Organization

Application code lives in `src/youtube_ready/`. `pipeline.py` orchestrates transcription, captions, metadata, chapters, and thumbnails; the corresponding modules keep those concerns separate. Pydantic data models are defined in `models.py`, and Click entry points live in `cli.py`. The local FastAPI interface is under `src/youtube_ready/web/`, with framework-free frontend assets in `web/static/`. Tests mirror modules in `tests/test_*.py`. Generated publishing packs belong in `youtube-ready-output/` and must not be committed.

Preserve the local-first design: source media is never uploaded or modified, and unavailable AI, FFmpeg, or OpenCV features should degrade to documented fallback behavior where supported.

## Build, Test, and Development Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,web,thumbnails]"
pytest
pytest tests/test_chapters.py::test_name -v
youtube-ready /path/to/video.mp4 --no-ai
youtube-ready-web --no-open-browser
```

The editable install provides both console commands. `pytest` runs the full suite; the targeted form is useful during iteration. The CLI example exercises deterministic fallbacks without requiring Ollama. The web command serves the UI locally. FFmpeg must be available on `PATH` for media processing. No separate build, lint, or formatting command is currently configured.

## Coding Style & Naming Conventions

Follow existing Python conventions: four-space indentation, type annotations, `snake_case` functions and variables, `PascalCase` classes, and `UPPER_CASE` constants. Keep modules focused and prefer small pure helpers for transformations. Preserve lazy imports for optional heavyweight dependencies. Frontend code uses plain JavaScript and CSS; avoid adding a build tool unless the project explicitly adopts one.

## Testing Guidelines

Use pytest and name files `test_<module>.py` and functions `test_<behavior>`. Mock Whisper, Ollama, MLX, FFmpeg, and filesystem-heavy operations rather than requiring models or network access. Add regression tests for fallback paths and verify that pipeline runs leave source files unchanged. There is no stated coverage threshold; prioritize behavior at module boundaries and error handling.

## Commit & Pull Request Guidelines

Git history is unavailable in this checkout. Use concise, imperative commit subjects, such as `Handle missing thumbnail frames`, and keep each commit focused. Pull requests should explain user-visible effects, list tests run, link relevant issues, and include screenshots for web UI changes. Call out dependency or fallback-behavior changes explicitly.
