from __future__ import annotations

import asyncio
import dataclasses
import io
import threading
import webbrowser
import zipfile
from pathlib import Path

import click
import uvicorn
from fastapi import Body, FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from youtube_ready import __version__
from youtube_ready import branding as branding_store
from youtube_ready import dictionary as dictionary_store
from youtube_ready.metadata import list_models
from youtube_ready.models import BrandingProfile, TranscriptSegment
from youtube_ready.pipeline import write_transcript_outputs
from youtube_ready.transcribe import QUALITY_PRESETS
from youtube_ready.web.jobs import (
    JOBS,
    delete_session,
    list_sessions,
    load_session,
    regenerate_thumbnail_variant,
    submit_job,
    update_thumbnail_variant,
    upload_thumbnail_frame,
)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="youtube-ready-ai", version=__version__)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.state.default_output_root = "youtube-ready-output"


def _versioned_index_html() -> str:
    """Stamp static asset URLs with each file's mtime so a server restart with
    new JS/CSS always busts the browser cache, instead of silently serving a
    stale cached app.js/styles.css after an update."""
    import re

    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    def stamp(match: "re.Match[str]") -> str:
        prefix, name, suffix = match.group(1), match.group(2), match.group(3)
        asset_path = STATIC_DIR / name
        version = int(asset_path.stat().st_mtime) if asset_path.is_file() else 0
        return f"{prefix}/static/{name}?v={version}{suffix}"

    return re.sub(r'(["\'])/static/([\w.-]+)(["\'])', stamp, html)


@app.get("/")
def index() -> HTMLResponse:
    return HTMLResponse(_versioned_index_html())


@app.get("/api/config")
def config() -> dict:
    return {
        "version": __version__,
        "qualities": sorted(QUALITY_PRESETS),
        "default_output_root": app.state.default_output_root,
        "metadata_backends": ["ollama", "mlx"],
    }


@app.get("/api/models")
def models(backend: str = "ollama") -> dict:
    if backend not in ("ollama", "mlx"):
        raise HTTPException(400, f"unknown metadata backend {backend!r}")
    try:
        return {"models": list_models(backend)}
    except Exception as exc:  # noqa: BLE001 - the backend may simply not be running/installed
        return {"models": [], "error": str(exc)}


@app.post("/api/jobs")
async def create_job(
    video: UploadFile,
    output_root: str = Form("youtube-ready-output"),
    quality: str = Form("balanced"),
    language: str = Form(""),
    translate: bool = Form(False),
    ai: bool = Form(True),
    metadata_model: str = Form("qwen2.5:7b-instruct"),
    metadata_backend: str = Form("ollama"),
    context: str = Form(""),
    thumbnails: bool = Form(True),
) -> dict:
    if quality not in QUALITY_PRESETS:
        raise HTTPException(400, f"unknown quality {quality!r}")
    if metadata_backend not in ("ollama", "mlx"):
        raise HTTPException(400, f"unknown metadata backend {metadata_backend!r}")
    job = submit_job(
        video,
        output_root=output_root,
        quality=quality,
        language=language.strip() or None,
        translate=translate,
        metadata_model=metadata_model.strip() if ai and metadata_model.strip() else None,
        metadata_backend=metadata_backend,
        context=context.strip() or None,
        thumbnails=thumbnails,
    )
    return {"job_id": job.id}


@app.get("/api/sessions")
def sessions(output_root: str | None = None) -> dict:
    return {"sessions": list_sessions(output_root or app.state.default_output_root)}


@app.post("/api/sessions/{name}/load")
def load_session_route(name: str, output_root: str | None = None) -> dict:
    try:
        job = load_session(output_root or app.state.default_output_root, name)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return job.as_dict()


@app.delete("/api/sessions/{name}")
def delete_session_route(name: str, output_root: str | None = None) -> dict:
    try:
        delete_session(output_root or app.state.default_output_root, name)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    return job.as_dict()


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")

    subscriber = job.subscribe()

    async def stream():
        loop = asyncio.get_event_loop()
        try:
            while True:
                message = await loop.run_in_executor(None, subscriber.get)
                yield f"data: {message}\n\n"
                if '"type": "done"' in message or '"type": "error"' in message:
                    break
        finally:
            job.unsubscribe(subscriber)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/jobs/{job_id}/files/{filename}")
def job_file(job_id: str, filename: str) -> FileResponse:
    job = JOBS.get(job_id)
    if job is None or job.result is None:
        raise HTTPException(404, "unknown job")
    path = job.result.output_dir / Path(filename).name
    if not path.is_file() or path.parent != job.result.output_dir:
        raise HTTPException(404, "unknown file")
    return FileResponse(path)


@app.get("/api/jobs/{job_id}/thumbnails/{filename:path}")
def job_thumbnail_frame(job_id: str, filename: str) -> FileResponse:
    job = JOBS.get(job_id)
    if job is None or job.result is None:
        raise HTTPException(404, "unknown job")
    frame_dir = (job.result.output_dir / "thumbnails").resolve()
    path = (frame_dir / filename).resolve()
    if not path.is_file() or frame_dir not in path.parents:
        raise HTTPException(404, "unknown thumbnail frame")
    return FileResponse(path)


@app.get("/api/jobs/{job_id}/thumbnail-variants")
def job_thumbnail_variants(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None or job.result is None:
        raise HTTPException(404, "unknown job")
    return job.as_dict()["thumbnail_variants"] or {"variants": [], "frames": []}


@app.put("/api/jobs/{job_id}/thumbnail-variants/{variant_id}")
def edit_thumbnail_variant(job_id: str, variant_id: str, updates: dict = Body(default={})) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    try:
        update_thumbnail_variant(job, variant_id, updates)
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return job.as_dict()["thumbnail_variants"]


@app.post("/api/jobs/{job_id}/thumbnail-variants/{variant_id}/regenerate")
def regenerate_variant_route(job_id: str, variant_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    try:
        regenerate_thumbnail_variant(job, variant_id)
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return job.as_dict()["thumbnail_variants"]


@app.post("/api/jobs/{job_id}/thumbnail-variants/{variant_id}/upload")
async def upload_variant_frame_route(job_id: str, variant_id: str, image: UploadFile) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    try:
        upload_thumbnail_frame(job, variant_id, image)
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return job.as_dict()["thumbnail_variants"]


@app.get("/api/jobs/{job_id}/thumbnail-variants/download-all")
def download_all_variants(job_id: str) -> StreamingResponse:
    job = JOBS.get(job_id)
    if job is None or job.result is None or job.result.thumbnail_variants is None:
        raise HTTPException(404, "unknown job")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for variant in job.result.thumbnail_variants.variants:
            if variant.rendered_path and Path(variant.rendered_path).is_file():
                archive.write(variant.rendered_path, arcname=f"variant-{variant.strategy}.jpg")
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=thumbnails.zip"},
    )


@app.post("/api/jobs/{job_id}/thumbnail-variants/{variant_id}/select")
def select_variant_route(job_id: str, variant_id: str, payload: dict = Body(default={})) -> dict:
    job = JOBS.get(job_id)
    if job is None or job.result is None or job.result.thumbnail_variants is None:
        raise HTTPException(404, "unknown job")
    variant = next((v for v in job.result.thumbnail_variants.variants if v.variant_id == variant_id), None)
    if variant is None:
        raise HTTPException(404, "unknown thumbnail variant")
    entry = branding_store.record_selection(
        job_id=job_id,
        variant_id=variant_id,
        strategy=variant.strategy,
        title=variant.title,
        youtube_test_result=payload.get("youtube_test_result"),
    )
    return entry.model_dump()


@app.get("/api/branding")
def get_branding() -> dict:
    return branding_store.load_branding_profile().model_dump()


@app.put("/api/branding")
def put_branding(profile: dict = Body(...)) -> dict:
    try:
        parsed = BrandingProfile.model_validate(profile)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return branding_store.save_branding_profile(parsed).model_dump()


@app.post("/api/branding/logo")
async def upload_branding_logo(logo: UploadFile) -> dict:
    profile = branding_store.load_branding_profile()
    target_dir = branding_store.branding_path().parent / "assets"
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(logo.filename or "logo.png").suffix or ".png"
    dest = target_dir / f"logo{suffix}"
    with dest.open("wb") as handle:
        import shutil as _shutil

        _shutil.copyfileobj(logo.file, handle)
    profile.logo_path = str(dest)
    return branding_store.save_branding_profile(profile).model_dump()


@app.get("/api/branding/logo")
def get_branding_logo() -> FileResponse:
    profile = branding_store.load_branding_profile()
    if not profile.logo_path or not Path(profile.logo_path).is_file():
        raise HTTPException(404, "no logo uploaded")
    return FileResponse(profile.logo_path)


@app.delete("/api/branding/logo")
def delete_branding_logo() -> dict:
    profile = branding_store.load_branding_profile()
    if profile.logo_path:
        Path(profile.logo_path).unlink(missing_ok=True)
    profile.logo_path = None
    return branding_store.save_branding_profile(profile).model_dump()


@app.get("/api/branding/fonts")
def list_branding_fonts() -> dict:
    return {"fonts": branding_store.list_fonts()}


@app.post("/api/branding/fonts")
async def add_branding_font(font: UploadFile) -> dict:
    import tempfile as _tempfile

    filename = font.filename or "custom-font.ttf"
    with _tempfile.NamedTemporaryFile(delete=False, suffix=Path(filename).suffix or ".ttf") as tmp:
        tmp.write(await font.read())
        tmp_path = Path(tmp.name)
    try:
        branding_store.save_font(tmp_path, filename)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        tmp_path.unlink(missing_ok=True)
    return {"fonts": branding_store.list_fonts()}


@app.get("/api/jobs/{job_id}/video")
def job_video(job_id: str) -> FileResponse:
    job = JOBS.get(job_id)
    if job is None or job.video_path is None or not job.video_path.is_file():
        raise HTTPException(404, "no preview video for this job")
    return FileResponse(job.video_path)


@app.get("/api/jobs/{job_id}/transcript")
def job_transcript(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None or job.result is None:
        raise HTTPException(404, "unknown job")
    return {"segments": [segment.model_dump() for segment in job.result.transcript.segments]}


@app.put("/api/jobs/{job_id}/transcript")
def update_job_transcript(job_id: str, payload: dict = Body(...)) -> dict:
    job = JOBS.get(job_id)
    if job is None or job.result is None:
        raise HTTPException(404, "unknown job")
    segments_in = payload.get("segments")
    if not isinstance(segments_in, list) or not segments_in:
        raise HTTPException(400, "segments must be a non-empty list")
    try:
        segments = [
            TranscriptSegment(
                start=item["start"],
                end=item["end"],
                text=str(item["text"]),
                words=[],  # word-level timing can't be edited by hand; captions re-chunk from text
            )
            for item in segments_in
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, f"invalid segment: {exc}") from exc
    transcript = job.result.transcript.model_copy(update={"segments": segments})
    job.result = dataclasses.replace(job.result, transcript=transcript)
    write_transcript_outputs(job.result.output_dir, transcript)
    return {"ok": True}


@app.get("/api/dictionary")
def get_dictionary() -> dict:
    return {"corrections": dictionary_store.load_corrections()}


@app.post("/api/dictionary")
def add_dictionary_entry(wrong: str = Form(...), right: str = Form(...)) -> dict:
    try:
        corrections = dictionary_store.add_correction(wrong, right)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"corrections": corrections}


@app.delete("/api/dictionary/{wrong}")
def delete_dictionary_entry(wrong: str) -> dict:
    return {"corrections": dictionary_store.remove_correction(wrong)}


@app.post("/api/jobs/{job_id}/transcript/apply-dictionary")
def apply_dictionary_to_job(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None or job.result is None:
        raise HTTPException(404, "unknown job")
    transcript = dictionary_store.apply_corrections(job.result.transcript)
    job.result = dataclasses.replace(job.result, transcript=transcript)
    write_transcript_outputs(job.result.output_dir, transcript)
    return {"segments": [segment.model_dump() for segment in transcript.segments]}


@click.command()
@click.version_option(version=__version__)
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8787, show_default=True, type=int)
@click.option("--output-root", default="youtube-ready-output", show_default=True)
@click.option("--open-browser/--no-open-browser", default=True, help="Open the UI in your default browser.")
def cli_main(host: str, port: int, output_root: str, open_browser: bool) -> None:
    """Serve the youtube-ready-ai web interface."""
    app.state.default_output_root = output_root
    url = f"http://{host}:{port}"
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    click.echo(f"youtube-ready-ai web interface running at {url}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    cli_main()
