from pathlib import Path

from youtube_ready.models import (
    PackagingBrief,
    ThumbnailFrame,
    ThumbnailIdea,
    ThumbnailPackage,
    TitleSuggestion,
    Transcript,
    TranscriptSegment,
)
from youtube_ready.render import RENDER_HEIGHT, RENDER_WIDTH, render_variant
from youtube_ready.thumbnails import (
    STRATEGIES,
    generate_thumbnail_variants,
    heuristic_thumbnail_variants,
    migrate_legacy_package,
    validate_variant,
    variants_to_legacy_package,
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


def sample_titles() -> list[TitleSuggestion]:
    return [
        TitleSuggestion(title="How To Record Better Guitar Audio", reason="Search-led: clear subject"),
        TitleSuggestion(title="The One Setting Everyone Gets Wrong", reason="Browse-led: curiosity gap"),
        TitleSuggestion(title="I Fixed My Guitar Recordings Instantly", reason="Outcome-led: result"),
    ]


def sample_frames() -> list[ThumbnailFrame]:
    return [
        ThumbnailFrame(timestamp=0.0, path="frame_000.jpg"),
        ThumbnailFrame(timestamp=5.0, path="frame_001.jpg"),
        ThumbnailFrame(timestamp=40.0, path="frame_002.jpg"),
    ]


def test_heuristic_variants_pair_one_title_per_strategy():
    package = heuristic_thumbnail_variants(sample_frames(), sample_transcript(), sample_titles())
    assert package.source == "heuristic"
    assert [v.strategy for v in package.variants] == list(STRATEGIES)
    for index, variant in enumerate(package.variants):
        assert variant.title == sample_titles()[index].title
        assert variant.variant_id


def test_generate_thumbnail_variants_with_llm():
    def fake_call(_model, _prompt):
        return {
            "variants": [
                {"strategy": "search", "overlay_text": "Set Input Gain", "visual_rationale": "shows the knob", "audience_promise": "clean audio", "frame_timestamp": 0.0},
                {"strategy": "browse", "overlay_text": "One Mistake", "visual_rationale": "surprised reaction", "audience_promise": "avoid the mistake", "frame_timestamp": 5.0},
                {"strategy": "outcome", "overlay_text": "Fixed", "visual_rationale": "before/after result", "audience_promise": "better sound", "frame_timestamp": 40.0},
            ]
        }

    package = generate_thumbnail_variants(
        sample_transcript(), sample_frames(), model="local-model", call=fake_call, titles=sample_titles()
    )
    assert package.source == "llm"
    assert len(package.variants) == 3
    assert {v.strategy for v in package.variants} == set(STRATEGIES)
    search = next(v for v in package.variants if v.strategy == "search")
    assert search.title == sample_titles()[0].title
    assert search.frame_path == "frame_000.jpg"


def test_generate_thumbnail_variants_falls_back_on_missing_strategy():
    def fake_call(_model, _prompt):
        return {"variants": [{"strategy": "search", "overlay_text": "x"}]}

    package = generate_thumbnail_variants(
        sample_transcript(), sample_frames(), model="local-model", call=fake_call, titles=sample_titles()
    )
    assert package.source == "fallback"
    assert package.fallback_reason


def test_generate_thumbnail_variants_without_frames():
    package = generate_thumbnail_variants(
        sample_transcript(), [], model="local-model", call=lambda *_: {}, titles=sample_titles()
    )
    assert package.source == "fallback"
    assert all(v.frame_path is None for v in package.variants)
    assert any("placeholder" in w for v in package.variants for w in v.warnings)


def test_validate_variant_flags_duplication_and_must_not_imply():
    brief = PackagingBrief(must_not_imply=["guaranteed results"])
    variant = heuristic_thumbnail_variants(sample_frames(), sample_transcript(), sample_titles()).variants[0]
    variant.title = "Guaranteed Results Every Time"
    variant.overlay_text = "Guaranteed Results"
    warnings = validate_variant(variant, brief)
    assert any("must_not_imply" in w for w in warnings)
    assert any("duplicates" in w for w in warnings)


def test_migrate_legacy_package_round_trip():
    legacy = ThumbnailPackage(
        frames=sample_frames(),
        ideas=[
            ThumbnailIdea(concept="Subject First", overlay_text="", frame_timestamp=0.0, frame_path="frame_000.jpg"),
            ThumbnailIdea(concept="Clean Visual", overlay_text="Wow", frame_timestamp=5.0, frame_path="frame_001.jpg"),
        ],
        source="heuristic",
    )
    migrated = migrate_legacy_package(legacy, sample_titles())
    assert len(migrated.variants) == 2
    assert migrated.variants[0].strategy == "search"
    assert migrated.variants[1].strategy == "browse"
    assert migrated.variants[0].title == sample_titles()[0].title


def test_variants_to_legacy_package_preserves_content():
    package = heuristic_thumbnail_variants(sample_frames(), sample_transcript(), sample_titles())
    legacy = variants_to_legacy_package(package)
    assert len(legacy.ideas) == 3
    assert legacy.ideas[0].frame_path == package.variants[0].frame_path


def test_render_variant_produces_placeholder_without_frame(tmp_path: Path):
    package = heuristic_thumbnail_variants([], sample_transcript(), sample_titles(), reason="no frames")
    variant = package.variants[1]
    variant.overlay_text = "One Mistake Everyone Makes"
    output_path = tmp_path / "variant-browse.jpg"
    result = render_variant(variant, output_path)
    assert result == str(output_path)
    assert output_path.is_file()

    from PIL import Image

    with Image.open(output_path) as image:
        assert image.size == (RENDER_WIDTH, RENDER_HEIGHT)


def test_render_variant_is_deterministic_for_fixed_input(tmp_path: Path):
    package = heuristic_thumbnail_variants([], sample_transcript(), sample_titles())
    variant = package.variants[0]
    variant.overlay_text = "Deterministic Test"
    out_a = tmp_path / "a.jpg"
    out_b = tmp_path / "b.jpg"
    render_variant(variant, out_a)
    render_variant(variant, out_b)
    assert out_a.read_bytes() == out_b.read_bytes()
