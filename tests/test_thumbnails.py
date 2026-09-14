from pathlib import Path

from youtube_ready.models import ThumbnailFrame, Transcript, TranscriptSegment
from youtube_ready.thumbnails import (
    _attach_dimensions_and_previews,
    _flag_duplicates,
    generate_thumbnail_ideas,
    heuristic_thumbnail_ideas,
    render_thumbnail_ideas,
    select_top_frames,
)


def sample_transcript() -> Transcript:
    return Transcript(
        language="en",
        duration=120,
        segments=[
            TranscriptSegment(start=0, end=20, text="How to record better guitar audio."),
            TranscriptSegment(start=40, end=60, text="First set the input gain."),
        ],
    )


def sample_frames() -> list[ThumbnailFrame]:
    return [
        ThumbnailFrame(timestamp=0.0, path="frame_000.jpg", sharpness=10.0, has_face=False),
        ThumbnailFrame(timestamp=5.0, path="frame_001.jpg", sharpness=50.0, has_face=True),
        ThumbnailFrame(timestamp=10.0, path="frame_002.jpg", sharpness=5.0, has_face=False),
        ThumbnailFrame(timestamp=40.0, path="frame_003.jpg", sharpness=20.0, has_face=False),
    ]


def test_select_top_frames_prefers_faces_and_sharpness_with_spacing():
    top = select_top_frames(sample_frames(), count=2, min_gap_seconds=3.0)
    assert len(top) == 2
    assert any(frame.has_face for frame in top)
    # sorted by timestamp ascending
    assert top[0].timestamp < top[1].timestamp


def test_heuristic_thumbnail_ideas_without_model():
    package = heuristic_thumbnail_ideas(sample_frames(), sample_transcript())
    assert package.source == "heuristic"
    assert len(package.ideas) == 3
    assert all(idea.concept for idea in package.ideas)
    # one heuristic variant ("Clean Visual") intentionally omits overlay text
    assert any(idea.overlay_text for idea in package.ideas)
    assert render_thumbnail_ideas(package)


def test_generate_thumbnail_ideas_with_llm():
    def fake_call(_model: str, _prompt: str) -> dict:
        return {
            "ideas": [
                {
                    "concept": "Close-up reaction",
                    "overlay_text": "Wait For It",
                    "visual_description": "Close-up of surprised expression",
                    "frame_timestamp": 5.0,
                    "image_prompt": "close up shot, dramatic lighting",
                },
                {
                    "concept": "Gear shot",
                    "overlay_text": "Set It Right",
                    "visual_description": "Hands adjusting a gain knob",
                    "frame_timestamp": 0.0,
                    "image_prompt": "product shot, studio lighting",
                },
            ]
        }

    package = generate_thumbnail_ideas(
        sample_transcript(), sample_frames(), model="local-model", call=fake_call
    )
    assert package.source == "llm"
    assert len(package.ideas) == 2
    assert package.ideas[0].frame_path == "frame_001.jpg"


def test_generate_thumbnail_ideas_falls_back_if_model_fails():
    def fail(_model: str, _prompt: str) -> dict:
        raise ConnectionError("offline")

    package = generate_thumbnail_ideas(
        sample_transcript(), sample_frames(), model="local-model", call=fail
    )
    assert package.source == "fallback"
    assert package.fallback_reason


def test_generate_thumbnail_ideas_without_frames():
    package = generate_thumbnail_ideas(sample_transcript(), [], model="local-model", call=lambda *_: {})
    assert package.source == "fallback"
    assert package.fallback_reason


def test_flag_duplicates_marks_near_identical_frames(tmp_path: Path):
    from PIL import Image

    def half_split(color_left, color_right):
        image = Image.new("RGB", (64, 64), color_right)
        image.paste(Image.new("RGB", (32, 64), color_left), (0, 0))
        return image

    solid_a = tmp_path / "a.jpg"
    solid_b = tmp_path / "b.jpg"
    different = tmp_path / "c.jpg"
    half_split((200, 50, 50), (10, 10, 10)).save(solid_a)
    half_split((200, 50, 50), (10, 10, 10)).save(solid_b)
    half_split((10, 10, 10), (200, 50, 50)).save(different)

    frames = [
        ThumbnailFrame(timestamp=0.0, path=str(solid_a)),
        ThumbnailFrame(timestamp=1.0, path=str(solid_b)),
        ThumbnailFrame(timestamp=2.0, path=str(different)),
    ]
    flagged = _flag_duplicates(frames)
    assert flagged[0].is_duplicate is False
    assert flagged[1].is_duplicate is True
    assert flagged[2].is_duplicate is False


def test_attach_dimensions_and_previews(tmp_path: Path):
    from PIL import Image

    master = tmp_path / "master_000.jpg"
    Image.new("RGB", (1280, 720), (0, 0, 0)).save(master)
    frames = [ThumbnailFrame(timestamp=0.0, path=str(master))]
    _attach_dimensions_and_previews(frames, tmp_path)
    assert frames[0].width == 1280
    assert frames[0].height == 720
    assert frames[0].preview_path is not None
    assert Path(frames[0].preview_path).is_file()


def test_select_top_frames_excludes_duplicates_and_blurry(tmp_path: Path):
    frames = [
        ThumbnailFrame(timestamp=0.0, path="a.jpg", sharpness=50.0, is_duplicate=True),
        ThumbnailFrame(timestamp=5.0, path="b.jpg", sharpness=2.0, is_duplicate=False),
        ThumbnailFrame(timestamp=10.0, path="c.jpg", sharpness=50.0, is_duplicate=False),
    ]
    top = select_top_frames(frames, count=3, min_gap_seconds=1.0)
    paths = {f.path for f in top}
    assert "a.jpg" not in paths
    assert "c.jpg" in paths
