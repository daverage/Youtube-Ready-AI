from pathlib import Path

from youtube_ready.models import (
    PackagingBrief,
    ThumbnailAdvice,
    ThumbnailAssessment,
    ThumbnailFrame,
    ThumbnailIdea,
    ThumbnailPackage,
    ThumbnailCompositionPlan,
    TitleSuggestion,
    Transcript,
    TranscriptSegment,
)
from youtube_ready.render import RENDER_HEIGHT, RENDER_WIDTH, render_variant
from youtube_ready.thumbnails import (
    STRATEGIES,
    classify_thumbnail_asset,
    find_better_take,
    generate_thumbnail_variants,
    heuristic_thumbnail_variants,
    migrate_legacy_package,
    recheck_rendered_thumbnail,
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


def test_heuristic_variants_include_assistant_assessment_and_plan():
    brief = PackagingBrief(core_topic="guitar audio", primary_subjects=["audio interface"])
    package = heuristic_thumbnail_variants(sample_frames(), sample_transcript(), sample_titles(), brief=brief)
    variant = package.variants[0]
    assert variant.assessment is not None
    assert variant.composition is not None
    assert variant.composition.primary_subject == "guitar audio"
    assert variant.advice is not None
    assert variant.source_assets["frame"] == variant.frame_path
    assert "factual transformation prohibited" in variant.edit_prompt
    assert variant.assessment.highest_value_change


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
    assert search.composition is not None
    assert search.edit_prompt


def test_generate_thumbnail_variants_preserves_llm_assessment_and_plan():
    def fake_call(_model, _prompt):
        return {
            "variants": [
                {
                    "strategy": strategy,
                    "overlay_text": "AI Plan",
                    "visual_rationale": "AI chose a supported composition.",
                    "audience_promise": "clean audio",
                    "frame_timestamp": 0.0,
                    "assessment": {
                        "frame_quality": "good",
                        "subject_clarity": "good",
                        "product_visibility": "medium",
                        "person_visibility": "poor",
                        "background_clutter": "medium",
                        "negative_space": "good",
                        "text_readability_potential": "good",
                        "cutout_potential": "medium",
                        "strengths": ["AI strength"],
                        "weaknesses": ["AI weakness"],
                        "highest_value_change": "AI improvement",
                    },
                    "composition": {
                        "primary_subject": "AI subject",
                        "secondary_subject": "AI secondary",
                        "visual_anchor": "AI anchor",
                        "secondary_anchor": "AI secondary",
                        "layout": "ai_layout",
                        "crop_instruction": "AI crop",
                        "background_treatment": "subtle_contrast",
                        "overlay_text": "AI Plan",
                    },
                    "advice": {
                        "overall": "AI advice",
                        "strengths": ["AI strength"],
                        "weaknesses": ["AI weakness"],
                        "highest_value_change": "AI improvement",
                    },
                }
                for strategy in STRATEGIES
            ]
        }

    package = generate_thumbnail_variants(
        sample_transcript(), sample_frames(), model="local-model", call=fake_call, titles=sample_titles()
    )

    assert package.source == "llm"
    assert package.variants[0].assessment.strengths == ["AI strength"]
    assert package.variants[0].composition.layout == "ai_layout"
    assert package.variants[0].advice.overall == "AI advice"


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
    assert all(v.assessment is not None for v in package.variants)


def test_uploaded_assets_are_classified_and_persisted(tmp_path: Path):
    asset_path = tmp_path / "clean-product-cutout.png"
    asset_path.write_bytes(b"not a real image")
    asset = classify_thumbnail_asset(asset_path)
    package = heuristic_thumbnail_variants(
        sample_frames(),
        sample_transcript(),
        sample_titles(),
        uploaded_assets=[asset],
    )
    assert asset.role == "product"
    assert package.uploaded_assets[0].role == "product"
    assert package.variants[0].source_assets["uploaded_assets"][0]["role"] == "product"


def test_uploaded_asset_classification_can_use_ollama_vision(tmp_path: Path, monkeypatch):
    from youtube_ready import thumbnails

    asset_path = tmp_path / "IMG_1234.jpg"
    asset_path.write_bytes(b"not a real image")
    monkeypatch.setattr(thumbnails, "_classify_thumbnail_asset_with_ollama", lambda _path, _model: ("logo", "AI saw a logo."))

    asset = classify_thumbnail_asset(asset_path, model="llava", backend="ollama")

    assert asset.role == "logo"
    assert asset.reason == "AI saw a logo."


def test_uploaded_asset_classification_can_use_mlx_vision(tmp_path: Path, monkeypatch):
    from youtube_ready import thumbnails

    asset_path = tmp_path / "IMG_1234.jpg"
    asset_path.write_bytes(b"not a real image")
    monkeypatch.setattr(thumbnails, "_classify_thumbnail_asset_with_mlx", lambda _path, _model: ("product", "MLX saw a product."))

    asset = classify_thumbnail_asset(asset_path, model="gemma3-vision", backend="mlx")

    assert asset.role == "product"
    assert asset.reason == "MLX saw a product."


def test_uploaded_asset_classification_skips_vision_for_text_model(tmp_path: Path, monkeypatch):
    from youtube_ready import thumbnails

    asset_path = tmp_path / "IMG_1234.jpg"
    asset_path.write_bytes(b"not a real image")

    def fail(*_args):
        raise AssertionError("vision classification should not run for a non-vision model")

    monkeypatch.setattr(thumbnails, "_classify_thumbnail_asset_with_ollama", fail)

    asset = classify_thumbnail_asset(asset_path, model="llama3", backend="ollama")

    assert asset.role == "other"


def test_uploaded_asset_classification_falls_back_when_ai_fails(tmp_path: Path, monkeypatch):
    from youtube_ready import thumbnails

    asset_path = tmp_path / "product-photo.jpg"
    asset_path.write_bytes(b"not a real image")

    def fail(_path, _model):
        raise RuntimeError("vision unavailable")

    monkeypatch.setattr(thumbnails, "_classify_thumbnail_asset_with_ollama", fail)

    asset = classify_thumbnail_asset(asset_path, model="llava", backend="ollama")

    assert asset.role == "product"


def test_recommendation_clears_when_matching_asset_exists(tmp_path: Path):
    asset_path = tmp_path / "clean-product-cutout.png"
    asset_path.write_bytes(b"not a real image")
    brief = PackagingBrief(core_topic="guitar audio", primary_subjects=["audio interface"])
    package = heuristic_thumbnail_variants(
        sample_frames(),
        sample_transcript(),
        sample_titles(),
        brief=brief,
        uploaded_assets=[classify_thumbnail_asset(asset_path)],
    )
    assert package.variants[0].assessment is not None
    assert package.variants[0].assessment.recommended_uploads == []


def test_validate_variant_flags_duplication_and_must_not_imply():
    brief = PackagingBrief(must_not_imply=["guaranteed results"])
    variant = heuristic_thumbnail_variants(sample_frames(), sample_transcript(), sample_titles()).variants[0]
    variant.title = "Guaranteed Results Every Time"
    variant.overlay_text = "Guaranteed Results"
    warnings = validate_variant(variant, brief)
    assert any("must_not_imply" in w for w in warnings)
    assert any("duplicates" in w for w in warnings)


def test_validate_variant_warns_on_unsupported_before_after():
    variant = heuristic_thumbnail_variants(sample_frames(), sample_transcript(), sample_titles()).variants[0]
    variant.overlay_text = "Before vs After"
    warnings = validate_variant(variant, PackagingBrief())
    assert any("Before/after" in warning for warning in warnings)


def test_validate_variant_allows_supported_before_after(tmp_path: Path):
    asset_path = tmp_path / "after-result.png"
    asset_path.write_bytes(b"not a real image")
    variant = heuristic_thumbnail_variants(
        sample_frames(),
        sample_transcript(),
        sample_titles(),
        uploaded_assets=[classify_thumbnail_asset(asset_path)],
    ).variants[0]
    variant.overlay_text = "Before vs After"
    warnings = validate_variant(variant, PackagingBrief())
    assert not any("Before/after" in warning for warning in warnings)


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
    assert legacy.ideas[0].image_prompt == package.variants[0].edit_prompt


def test_generate_thumbnail_variants_uses_ollama_vision_assessment(monkeypatch):
    from youtube_ready import thumbnails

    def fake_call(_model, _prompt):
        return {
            "variants": [
                {
                    "strategy": strategy,
                    "frame_timestamp": 0,
                    "overlay_text": "Better Audio",
                    "visual_rationale": "Use the clearest visible source.",
                    "audience_promise": "A practical improvement.",
                }
                for strategy in STRATEGIES
            ]
        }

    def fake_vision(_model, variant, _brief):
        return (
            ThumbnailAssessment(strengths=["Vision saw a clean subject."], highest_value_change="Use the clean frame."),
            ThumbnailCompositionPlan(layout="vision_layout", visual_anchor="visible guitar"),
            ThumbnailAdvice(overall=f"Vision advice for {variant.strategy}."),
        )

    monkeypatch.setattr(thumbnails, "_ollama_vision_thumbnail_assessment", fake_vision)

    package = generate_thumbnail_variants(
        sample_transcript(),
        sample_frames(),
        model="llava",
        backend="ollama",
        call=fake_call,
        titles=sample_titles(),
    )

    assert package.variants[0].assessment.strengths == ["Vision saw a clean subject."]
    assert package.variants[0].composition.layout == "vision_layout"
    assert package.variants[0].advice.overall == "Vision advice for search."
    assert package.variants[0].visual_verified is True


def test_generate_thumbnail_variants_accepts_bare_list_response():
    # Some local models skip the {"variants": [...]} wrapper the schema asks
    # for and return a bare JSON array instead.
    def fake_call(_model, _prompt):
        return [
            {"strategy": strategy, "frame_timestamp": 0, "overlay_text": f"{strategy} overlay"}
            for strategy in STRATEGIES
        ]

    package = generate_thumbnail_variants(
        sample_transcript(),
        sample_frames(),
        model="llama3",
        backend="ollama",
        call=fake_call,
        titles=sample_titles(),
    )

    assert package.source == "llm"
    by_strategy = {v.strategy: v for v in package.variants}
    assert set(by_strategy) == set(STRATEGIES)


def test_generate_thumbnail_variants_accepts_alternate_wrapper_key():
    # ... or wrap the list under a different key entirely, e.g. "thumbnails".
    def fake_call(_model, _prompt):
        return {
            "thumbnails": [
                {"strategy": strategy, "frame_timestamp": 0, "overlay_text": f"{strategy} overlay"}
                for strategy in STRATEGIES
            ]
        }

    package = generate_thumbnail_variants(
        sample_transcript(),
        sample_frames(),
        model="llama3",
        backend="ollama",
        call=fake_call,
        titles=sample_titles(),
    )

    assert package.source == "llm"
    by_strategy = {v.strategy: v for v in package.variants}
    assert set(by_strategy) == set(STRATEGIES)


def test_generate_thumbnail_variants_failure_message_is_diagnostic():
    # When generation genuinely can't be salvaged, the fallback reason shown
    # in the UI should say what the model actually sent back instead of just
    # "model did not return a 'search' variant" with no way to tell why.
    def fake_call(_model, _prompt):
        return {"variants": [{"strategy": "browse", "overlay_text": "only one"}]}

    package = generate_thumbnail_variants(
        sample_transcript(),
        sample_frames(),
        model="llama3",
        backend="ollama",
        call=fake_call,
        titles=sample_titles(),
    )

    assert package.source == "fallback"
    assert "got 1 variant" in package.fallback_reason
    assert "browse" in package.fallback_reason


def test_generate_thumbnail_variants_reports_flattened_single_object_response():
    # A real failure seen in practice: an overloaded small model returned one
    # flat object matching the *composition* sub-schema's field names, with no
    # "variants" wrapper and no per-strategy structure at all.
    def fake_call(_model, _prompt):
        return {
            "primary_subject": "...",
            "layout": "...",
            "background_treatment": "...",
            "overlay_text": "...",
            "advice": {},
        }

    package = generate_thumbnail_variants(
        sample_transcript(),
        sample_frames(),
        model="llama3",
        backend="ollama",
        call=fake_call,
        titles=sample_titles(),
    )

    assert package.source == "fallback"
    assert "got 0 variant(s)" in package.fallback_reason
    assert "primary_subject" in package.fallback_reason  # names the actual keys it got, not just "failed"


def test_generate_thumbnail_variants_llm_response_needs_only_the_essentials():
    # The prompt no longer asks the (text-only, blind-to-the-frame) model for
    # assessment/composition/advice — those are always heuristically computed
    # afterwards regardless, so a minimal response should still produce a
    # fully-populated package, not a degraded one.
    def fake_call(_model, _prompt):
        return {
            "variants": [
                {"strategy": strategy, "frame_timestamp": 0, "overlay_text": f"{strategy} overlay", "visual_rationale": "why"}
                for strategy in STRATEGIES
            ]
        }

    package = generate_thumbnail_variants(
        sample_transcript(),
        sample_frames(),
        model="llama3",
        backend="ollama",
        call=fake_call,
        titles=sample_titles(),
    )

    assert package.source == "llm"
    for variant in package.variants:
        assert variant.assessment is not None
        assert variant.composition is not None
        assert variant.advice is not None


def test_generate_thumbnail_variants_tolerates_aliased_strategy_labels():
    # Small/quantized local models often paraphrase the schema's exact
    # "search"/"browse"/"outcome" strategy strings as the UI-facing label
    # instead (e.g. "Clarity/Search") — this used to raise "model did not
    # return a 'search' variant" and force an unnecessary heuristic fallback
    # even though the model's response was otherwise perfectly usable.
    aliased_labels = {"search": "Clarity/Search", "browse": "Curiosity / Browse", "outcome": "outcome_stakes"}

    def fake_call(_model, _prompt):
        return {
            "variants": [
                {
                    "strategy": aliased_labels[strategy],
                    "frame_timestamp": 0,
                    "overlay_text": f"{strategy} overlay",
                    "visual_rationale": "Use the clearest visible source.",
                }
                for strategy in STRATEGIES
            ]
        }

    package = generate_thumbnail_variants(
        sample_transcript(),
        sample_frames(),
        model="llama3",
        backend="ollama",
        call=fake_call,
        titles=sample_titles(),
    )

    assert package.source == "llm"
    by_strategy = {v.strategy: v for v in package.variants}
    assert set(by_strategy) == set(STRATEGIES)
    assert by_strategy["search"].overlay_text == "search overlay"
    assert by_strategy["outcome"].overlay_text == "outcome overlay"


def test_generate_thumbnail_variants_tolerates_null_composition_fields():
    # Some local models (mlx-vlm, small Ollama vision models) emit an explicit
    # JSON null for a string field instead of omitting it or using "" — this
    # used to raise a pydantic ValidationError that discarded the whole LLM
    # response and fell back to heuristic generation.
    def fake_call(_model, _prompt):
        return {
            "variants": [
                {
                    "strategy": strategy,
                    "frame_timestamp": 0,
                    "overlay_text": "Better Audio",
                    "visual_rationale": "Use the clearest visible source.",
                    "composition": {"secondary_anchor": None, "layout": None, "remove_or_suppress": None},
                }
                for strategy in STRATEGIES
            ]
        }

    package = generate_thumbnail_variants(
        sample_transcript(),
        sample_frames(),
        model="llama3",
        backend="ollama",
        call=fake_call,
        titles=sample_titles(),
    )

    assert package.source == "llm"
    assert package.variants[0].composition.secondary_anchor == ""
    assert package.variants[0].composition.layout == "single_frame"


def test_generate_thumbnail_variants_uses_mlx_vision_assessment(monkeypatch):
    from youtube_ready import thumbnails

    def fake_call(_model, _prompt):
        return {
            "variants": [
                {
                    "strategy": strategy,
                    "frame_timestamp": 0,
                    "overlay_text": "Better Audio",
                    "visual_rationale": "Use the clearest visible source.",
                    "audience_promise": "A practical improvement.",
                }
                for strategy in STRATEGIES
            ]
        }

    def fake_mlx_vision(_model, variant, _brief):
        return (
            ThumbnailAssessment(strengths=["MLX-VLM saw a clean subject."]),
            ThumbnailCompositionPlan(layout="mlx_vision_layout"),
            ThumbnailAdvice(overall=f"MLX vision advice for {variant.strategy}."),
        )

    monkeypatch.setattr(thumbnails, "_mlx_vision_thumbnail_assessment", fake_mlx_vision)

    package = generate_thumbnail_variants(
        sample_transcript(),
        sample_frames(),
        model="gemma3-vision",
        backend="mlx",
        call=fake_call,
        titles=sample_titles(),
    )

    assert package.variants[0].assessment.strengths == ["MLX-VLM saw a clean subject."]
    assert package.variants[0].composition.layout == "mlx_vision_layout"
    assert package.variants[0].visual_verified is True


def test_generate_thumbnail_variants_skips_vision_for_text_model(monkeypatch):
    from youtube_ready import thumbnails

    def fake_call(_model, _prompt):
        return {
            "variants": [
                {
                    "strategy": strategy,
                    "frame_timestamp": 0,
                    "overlay_text": "Better Audio",
                    "visual_rationale": "Use the clearest visible source.",
                }
                for strategy in STRATEGIES
            ]
        }

    def fail_vision(*_args):
        raise AssertionError("vision assessment should not run for non-vision models")

    monkeypatch.setattr(thumbnails, "_ollama_vision_thumbnail_assessment", fail_vision)

    package = generate_thumbnail_variants(
        sample_transcript(),
        sample_frames(),
        model="llama3",
        backend="ollama",
        call=fake_call,
        titles=sample_titles(),
    )

    assert package.variants[0].assessment is not None
    assert package.variants[0].visual_verified is False


def test_refresh_thumbnail_assistant_fields_resets_visual_verified(monkeypatch):
    from youtube_ready import thumbnails

    package = heuristic_thumbnail_variants(sample_frames(), sample_transcript(), sample_titles())
    package.variants[0].visual_verified = True

    thumbnails.refresh_thumbnail_assistant_fields(package, sample_transcript())

    assert package.variants[0].visual_verified is False


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


def test_render_variant_uses_style_for_auto_crop(tmp_path: Path):
    from PIL import Image

    source = tmp_path / "wide.jpg"
    image = Image.new("RGB", (1600, 900), (0, 0, 0))
    for x in range(800):
        for y in range(900):
            image.putpixel((x, y), (220, 20, 20))
    for x in range(800, 1600):
        for y in range(900):
            image.putpixel((x, y), (20, 20, 220))
    image.save(source)

    clear = heuristic_thumbnail_variants([ThumbnailFrame(timestamp=0, path=str(source))], sample_transcript(), sample_titles()).variants[0]
    clear.style_intent = "clear"
    clear.focal_x = 0.0
    bold = clear.model_copy(deep=True)
    bold.style_intent = "bold"

    clear_out = tmp_path / "clear.jpg"
    bold_out = tmp_path / "bold.jpg"
    render_variant(clear, clear_out)
    render_variant(bold, bold_out)

    with Image.open(clear_out) as clear_image, Image.open(bold_out) as bold_image:
        assert clear_image.getpixel((1270, 360))[2] > 100
        assert clear_image.getpixel((700, 360))[2] > 100
        assert bold_image.getpixel((700, 360))[0] > 100


def test_render_variant_applies_background_treatment(tmp_path: Path):
    from PIL import Image

    source = tmp_path / "frame.jpg"
    Image.new("RGB", (1280, 720), (120, 120, 120)).save(source)
    package = heuristic_thumbnail_variants([ThumbnailFrame(timestamp=0, path=str(source))], sample_transcript(), sample_titles())
    variant = package.variants[0]
    variant.overlay_text = ""
    variant.composition = ThumbnailCompositionPlan(background_treatment="blur_and_darken")
    output = tmp_path / "treated.jpg"

    render_variant(variant, output)

    with Image.open(output) as rendered:
        r, g, b = rendered.getpixel((640, 360))
    assert (r, g, b) < (120, 120, 120)


def test_render_variant_text_treatments_produce_distinct_output(tmp_path: Path):
    from PIL import Image

    source = tmp_path / "frame.jpg"
    Image.new("RGB", (1280, 720), (60, 60, 60)).save(source)
    package = heuristic_thumbnail_variants([ThumbnailFrame(timestamp=0, path=str(source))], sample_transcript(), sample_titles())
    base_variant = package.variants[0]
    base_variant.overlay_text = "GOOD ENOUGH TO GIG?"
    base_variant.text.position = "bottom"

    treatments = ["restrained_high_contrast", "large_bold_high_contrast", "oversized_bold_stroked", None]
    renders = {}
    for treatment in treatments:
        variant = base_variant.model_copy(deep=True)
        variant.composition = ThumbnailCompositionPlan(text_treatment=treatment) if treatment else None
        output = tmp_path / f"{treatment or 'legacy'}.jpg"
        render_variant(variant, output)
        with Image.open(output) as rendered:
            # Sample a pixel just outside the text glyphs but inside the backdrop
            # footprint to distinguish "no backdrop" from a drawn panel/gradient.
            renders[treatment] = rendered.getpixel((30, 660))

    # No-backdrop treatment leaves the source frame's grey untouched near the edge.
    assert renders["restrained_high_contrast"] == (60, 60, 60)
    # Tight panel, gradient, and legacy strip all draw some backdrop, but the
    # legacy strip spans the full width while the tight panel/gradient don't
    # necessarily reach this far-left sample point the same way.
    assert renders[None] != (60, 60, 60)


def test_find_better_take_requires_a_face_in_the_current_frame(tmp_path: Path, monkeypatch):
    from PIL import Image

    from youtube_ready import thumbnails

    current = tmp_path / "current.jpg"
    Image.new("RGB", (320, 180), (50, 50, 50)).save(current)

    monkeypatch.setattr(thumbnails, "_ffmpeg_available", lambda: True)
    monkeypatch.setattr(thumbnails, "_load_face_cascades", lambda _cv2: None)
    monkeypatch.setattr(thumbnails, "_FACE_CASCADE", object())
    monkeypatch.setattr(thumbnails, "_face_take_score", lambda _cv2, _gray: None)

    try:
        find_better_take("video.mp4", str(current), 10.0, tmp_path)
        assert False, "expected a ValueError"
    except ValueError as exc:
        assert "no face" in str(exc)


def test_find_better_take_swaps_to_a_clearly_stronger_candidate(tmp_path: Path, monkeypatch):
    from PIL import Image

    from youtube_ready import thumbnails
    from youtube_ready.models import ThumbnailFrame

    current = tmp_path / "current.jpg"
    Image.new("RGB", (320, 180), (50, 50, 50)).save(current)
    candidate_path = tmp_path / "burst" / "take_001.jpg"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (320, 180), (60, 60, 60)).save(candidate_path)

    monkeypatch.setattr(thumbnails, "_ffmpeg_available", lambda: True)
    monkeypatch.setattr(thumbnails, "_load_face_cascades", lambda _cv2: None)
    monkeypatch.setattr(thumbnails, "_FACE_CASCADE", object())
    monkeypatch.setattr(
        thumbnails,
        "_extract_burst_frames",
        lambda *_args, **_kwargs: [ThumbnailFrame(timestamp=11.2, path=str(candidate_path), source="burst")],
    )

    scores = {str(current): 50.0, str(candidate_path): 200.0}
    # cv2.imread reads decoded pixel bytes, not a path, so there's no direct
    # way to key the score off which file is being scored — use call order
    # instead (current frame is always scored first, then each candidate).
    read_order = []

    def fake_score(_cv2, gray):
        read_order.append(gray)
        return scores[str(current)] if len(read_order) == 1 else scores[str(candidate_path)]

    monkeypatch.setattr(thumbnails, "_face_take_score", fake_score)

    result = find_better_take("video.mp4", str(current), 10.0, tmp_path)

    assert result.path == str(candidate_path)
    assert result.timestamp == 11.2


def test_find_better_take_keeps_current_frame_when_nothing_better_found(tmp_path: Path, monkeypatch):
    from PIL import Image

    from youtube_ready import thumbnails
    from youtube_ready.models import ThumbnailFrame

    current = tmp_path / "current.jpg"
    Image.new("RGB", (320, 180), (50, 50, 50)).save(current)
    candidate_path = tmp_path / "burst" / "take_001.jpg"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (320, 180), (60, 60, 60)).save(candidate_path)

    monkeypatch.setattr(thumbnails, "_ffmpeg_available", lambda: True)
    monkeypatch.setattr(thumbnails, "_load_face_cascades", lambda _cv2: None)
    monkeypatch.setattr(thumbnails, "_FACE_CASCADE", object())
    monkeypatch.setattr(
        thumbnails,
        "_extract_burst_frames",
        lambda *_args, **_kwargs: [ThumbnailFrame(timestamp=11.2, path=str(candidate_path), source="burst")],
    )
    # Candidate scores barely above current — not enough to clear the margin.
    monkeypatch.setattr(thumbnails, "_face_take_score", lambda _cv2, _gray: 100.0)

    try:
        find_better_take("video.mp4", str(current), 10.0, tmp_path)
        assert False, "expected a ValueError"
    except ValueError as exc:
        assert "already the strongest" in str(exc)


def test_recheck_rendered_thumbnail_requires_a_render(tmp_path: Path):
    package = heuristic_thumbnail_variants([], sample_transcript(), sample_titles())
    variant = package.variants[0]
    variant.rendered_path = None

    try:
        recheck_rendered_thumbnail("llava", "ollama", variant, None)
        assert False, "expected a ValueError"
    except ValueError as exc:
        assert "render" in str(exc)


def test_recheck_rendered_thumbnail_requires_vision_model(tmp_path: Path):
    package = heuristic_thumbnail_variants([], sample_transcript(), sample_titles())
    variant = package.variants[0]
    variant.rendered_path = str(tmp_path / "rendered.jpg")
    Path(variant.rendered_path).write_bytes(b"not a real image, just needs to exist")

    try:
        recheck_rendered_thumbnail("llama3", "ollama", variant, None)
        assert False, "expected a ValueError"
    except ValueError as exc:
        assert "vision" in str(exc)


def test_recheck_rendered_thumbnail_assesses_the_rendered_image(tmp_path: Path, monkeypatch):
    from youtube_ready import thumbnails

    package = heuristic_thumbnail_variants([], sample_transcript(), sample_titles())
    variant = package.variants[0]
    variant.rendered_path = str(tmp_path / "rendered.jpg")
    Path(variant.rendered_path).write_bytes(b"not a real image, just needs to exist")

    seen_images = {}

    def fake_ollama_vision(_model, _variant, _brief, *, images=None):
        seen_images["images"] = images
        return ThumbnailAssessment(strengths=["Looks clean in the final render."]), None, None

    monkeypatch.setattr(thumbnails, "_ollama_vision_thumbnail_assessment", fake_ollama_vision)

    result = recheck_rendered_thumbnail("llava", "ollama", variant, None)

    assert result.strengths == ["Looks clean in the final render."]
    # Must assess the rendered composite, not the raw source frame/assets.
    assert seen_images["images"] == [variant.rendered_path]


def test_render_variant_composites_supporting_product_asset(tmp_path: Path):
    from PIL import Image

    frame = tmp_path / "frame.jpg"
    product = tmp_path / "product-cutout.png"
    Image.new("RGB", (1280, 720), (20, 20, 20)).save(frame)
    Image.new("RGBA", (300, 220), (230, 30, 30, 255)).save(product)

    package = heuristic_thumbnail_variants(
        [ThumbnailFrame(timestamp=0, path=str(frame))],
        sample_transcript(),
        sample_titles(),
        uploaded_assets=[classify_thumbnail_asset(product)],
    )
    variant = package.variants[0]
    variant.overlay_text = ""
    output = tmp_path / "composite.jpg"

    render_variant(variant, output)

    with Image.open(output) as rendered:
        red_pixels = 0
        for x in range(40, 560, 20):
            for y in range(150, 560, 20):
                r, g, b = rendered.getpixel((x, y))
                if r > 150 and g < 90 and b < 90:
                    red_pixels += 1
    assert red_pixels > 20


def test_render_variant_places_product_asset_on_planned_product_side(tmp_path: Path):
    from PIL import Image

    frame = tmp_path / "frame.jpg"
    product = tmp_path / "product-cutout.png"
    Image.new("RGB", (1280, 720), (20, 20, 20)).save(frame)
    Image.new("RGBA", (300, 220), (230, 30, 30, 255)).save(product)
    package = heuristic_thumbnail_variants(
        [ThumbnailFrame(timestamp=0, path=str(frame))],
        sample_transcript(),
        sample_titles(),
        uploaded_assets=[classify_thumbnail_asset(product)],
    )
    variant = package.variants[0]
    variant.overlay_text = ""
    variant.composition = ThumbnailCompositionPlan(layout="presenter_left_product_right", supporting_asset_roles=["product"])
    output = tmp_path / "right-product.jpg"

    render_variant(variant, output)

    with Image.open(output) as rendered:
        left_red = rendered.getpixel((120, 120))[0]
        right_red = rendered.getpixel((1180, 120))[0]
    assert left_red < 80
    assert right_red > 150


def test_render_variant_can_composite_presenter_asset(tmp_path: Path):
    from PIL import Image

    frame = tmp_path / "frame.jpg"
    presenter = tmp_path / "presenter-cutout.png"
    Image.new("RGB", (1280, 720), (20, 20, 20)).save(frame)
    Image.new("RGBA", (260, 520), (30, 210, 80, 255)).save(presenter)
    package = heuristic_thumbnail_variants(
        [ThumbnailFrame(timestamp=0, path=str(frame))],
        sample_transcript(),
        sample_titles(),
        uploaded_assets=[classify_thumbnail_asset(presenter)],
    )
    variant = package.variants[0]
    variant.overlay_text = ""
    variant.composition = ThumbnailCompositionPlan(
        layout="presenter_left_product_right",
        primary_subject="presenter",
        supporting_asset_roles=["person"],
    )
    output = tmp_path / "presenter.jpg"

    render_variant(variant, output)

    with Image.open(output) as rendered:
        green_pixels = 0
        for x in range(60, 430, 20):
            for y in range(80, 610, 20):
                r, g, b = rendered.getpixel((x, y))
                if g > 140 and r < 90 and b < 120:
                    green_pixels += 1
    assert green_pixels > 30


def test_render_variant_can_composite_logo_asset(tmp_path: Path):
    from PIL import Image

    frame = tmp_path / "frame.jpg"
    logo = tmp_path / "brand-logo.png"
    Image.new("RGB", (1280, 720), (20, 20, 20)).save(frame)
    Image.new("RGBA", (320, 120), (240, 240, 30, 255)).save(logo)
    package = heuristic_thumbnail_variants(
        [ThumbnailFrame(timestamp=0, path=str(frame))],
        sample_transcript(),
        sample_titles(),
        uploaded_assets=[classify_thumbnail_asset(logo)],
    )
    variant = package.variants[0]
    variant.overlay_text = ""
    output = tmp_path / "logo.jpg"

    render_variant(variant, output)

    with Image.open(output) as rendered:
        r, g, b = rendered.getpixel((1160, 90))
    assert r > 150 and g > 150 and b < 100


def test_render_variant_does_not_force_unplanned_supporting_asset(tmp_path: Path):
    from PIL import Image

    frame = tmp_path / "frame.jpg"
    product = tmp_path / "product-cutout.png"
    Image.new("RGB", (1280, 720), (20, 20, 20)).save(frame)
    Image.new("RGBA", (300, 220), (230, 30, 30, 255)).save(product)
    package = heuristic_thumbnail_variants(
        [ThumbnailFrame(timestamp=0, path=str(frame))],
        sample_transcript(),
        sample_titles(),
        uploaded_assets=[classify_thumbnail_asset(product)],
    )
    variant = package.variants[0]
    variant.overlay_text = ""
    variant.composition.supporting_asset_roles = []
    output = tmp_path / "unplanned.jpg"

    render_variant(variant, output)

    with Image.open(output) as rendered:
        red_pixels = 0
        for x in range(40, 560, 20):
            for y in range(70, 540, 20):
                r, g, b = rendered.getpixel((x, y))
                if r > 150 and g < 90 and b < 90:
                    red_pixels += 1
    assert red_pixels == 0


def test_render_variant_places_supporting_asset_away_from_bottom_text(tmp_path: Path):
    from PIL import Image

    frame = tmp_path / "frame.jpg"
    product = tmp_path / "product-cutout.png"
    Image.new("RGB", (1280, 720), (20, 20, 20)).save(frame)
    Image.new("RGBA", (300, 220), (230, 30, 30, 255)).save(product)
    package = heuristic_thumbnail_variants(
        [ThumbnailFrame(timestamp=0, path=str(frame))],
        sample_transcript(),
        sample_titles(),
        uploaded_assets=[classify_thumbnail_asset(product)],
    )
    variant = package.variants[0]
    variant.overlay_text = ""
    variant.text.position = "bottom"
    output = tmp_path / "top-composite.jpg"

    render_variant(variant, output)

    with Image.open(output) as rendered:
        top_red = rendered.getpixel((120, 90))[0]
        bottom_red = rendered.getpixel((120, 650))[0]
    assert top_red > 150
    assert bottom_red < 80


def test_generate_thumbnail_variants_reports_progress_steps():
    def fake_call(_model, _prompt):
        return {"variants": [{"strategy": s, "overlay_text": "Hi", "frame_timestamp": 1.0} for s in STRATEGIES]}

    messages: list[str] = []
    generate_thumbnail_variants(
        sample_transcript(),
        sample_frames(),
        model="local-model",
        call=fake_call,
        titles=sample_titles(),
        on_progress=messages.append,
    )
    assert any("local-model" in message for message in messages)
    assert messages[-1].startswith("Checking thumbnail plans")


def test_job_activity_tracks_current_step_and_clears_afterwards():
    from youtube_ready.web.jobs import Job

    job = Job(id="job")
    job.set_activity_step("ignored outside an activity")
    assert job.activity_dict()["active"] is False

    with job.activity("Starting"):
        assert job.activity_dict()["message"] == "Starting"
        job.set_activity_step("Asking the model")
        state = job.activity_dict()
        assert state["active"] is True
        assert state["message"] == "Asking the model"

    assert job.activity_dict() == {"active": False, "message": None, "elapsed": 0.0, "step_elapsed": 0.0}


def test_suggest_overlay_ideas_uses_model_and_filters_repeats():
    from youtube_ready.thumbnails import heuristic_thumbnail_variants, suggest_overlay_ideas

    variant = heuristic_thumbnail_variants(sample_frames(), sample_transcript(), sample_titles()).variants[1]
    variant.overlay_text = "One Mistake"
    variant.overlay_options = ["Already Shown"]

    def fake_call(_model, prompt):
        assert "Already Shown" in prompt
        return {"ideas": ["One mistake", "already shown", "Worth It?", "A much too long overlay idea that keeps going", ""]}

    ideas = suggest_overlay_ideas(
        sample_transcript(), variant, brief=None, model="local-model", call=fake_call
    )
    assert ideas[0] == "Worth It?"
    assert len(ideas) == 2
    assert all(len(idea) <= 32 and len(idea.split()) <= 5 for idea in ideas)


def test_suggest_overlay_ideas_falls_back_when_model_fails():
    from youtube_ready.thumbnails import heuristic_thumbnail_variants, suggest_overlay_ideas

    variant = heuristic_thumbnail_variants(sample_frames(), sample_transcript(), sample_titles()).variants[2]

    def broken_call(_model, _prompt):
        raise RuntimeError("ollama down")

    ideas = suggest_overlay_ideas(sample_transcript(), variant, brief=None, model="m", call=broken_call)
    assert ideas
    assert variant.overlay_text not in ideas
