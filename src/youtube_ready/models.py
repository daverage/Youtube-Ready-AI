from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class Word(BaseModel):
    start: float
    end: float
    text: str


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str
    words: list[Word] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_times(self) -> "TranscriptSegment":
        if self.start < 0 or self.end <= self.start:
            raise ValueError("transcript segment must have non-negative, increasing timestamps")
        return self


class Transcript(BaseModel):
    language: str | None = None
    duration: float = 0.0
    segments: list[TranscriptSegment] = Field(default_factory=list)


class Chapter(BaseModel):
    start: float
    title: str


class TitleSuggestion(BaseModel):
    title: str
    reason: str = ""


class PackagingBrief(BaseModel):
    """Compact, topic-neutral representation of what makes this video worth watching.

    This is intentionally separate from the final metadata. The transcript remains the
    factual source of truth; the brief simply makes the useful packaging signals explicit
    before a smaller local model has to write titles/descriptions/thumbnails. It is
    persisted alongside the metadata so thumbnail generation can reuse the same evidence
    instead of re-deriving it (and disagreeing with the titles it should complement).
    """

    core_topic: str = ""
    video_type: str = "other"
    viewer_intent: str = "mixed"
    target_viewer: str = ""
    primary_subjects: list[str] = Field(default_factory=list)
    viewer_problem: str = ""
    viewer_payoff: str = ""
    primary_claim: str = ""
    result_or_conclusion: str = ""
    surprising_points: list[str] = Field(default_factory=list)
    questions_answered: list[str] = Field(default_factory=list)
    stakes: str = ""
    contrasts: list[str] = Field(default_factory=list)
    proof_points: list[str] = Field(default_factory=list)
    search_phrases: list[str] = Field(default_factory=list)
    visual_subjects: list[str] = Field(default_factory=list)
    must_not_imply: list[str] = Field(default_factory=list)


class PublishingMetadata(BaseModel):
    titles: list[TitleSuggestion] = Field(default_factory=list)
    description: str = ""
    hashtags: list[str] = Field(default_factory=list)
    chapters: list[Chapter] = Field(default_factory=list)
    language: str | None = None
    source: str = "heuristic"
    model: str | None = None
    backend: str | None = None
    fallback_reason: str | None = None
    chapters_note: str | None = None
    packaging_brief: PackagingBrief | None = None


class ThumbnailFrame(BaseModel):
    timestamp: float
    path: str
    preview_path: str | None = None
    width: int = 0
    height: int = 0
    sharpness: float = 0.0
    has_face: bool = False
    is_duplicate: bool = False
    negative_space_score: float = 0.0
    source: str = "scene"  # scene | even | chapter | conclusion


class ThumbnailIdea(BaseModel):
    concept: str
    overlay_text: str
    visual_description: str = ""
    frame_timestamp: float | None = None
    frame_path: str | None = None
    image_prompt: str = ""


class ThumbnailPackage(BaseModel):
    frames: list[ThumbnailFrame] = Field(default_factory=list)
    ideas: list[ThumbnailIdea] = Field(default_factory=list)
    source: str = "heuristic"
    model: str | None = None
    backend: str | None = None
    fallback_reason: str | None = None


class CropBox(BaseModel):
    """Normalized (0-1) crop region within the source frame. The identity box
    (0,0,1,1) means "no explicit crop chosen"; the renderer computes an
    automatic 16:9 center-crop around the focal point in that case."""

    x: float = 0.0
    y: float = 0.0
    width: float = 1.0
    height: float = 1.0


class TextLayout(BaseModel):
    position: str = "bottom"  # top | bottom | center
    align: str = "center"  # left | center | right
    style: str = "bold-outline"
    safe_margin: float = 0.06


class ThumbnailVariant(BaseModel):
    """One title-thumbnail package: a title, a verified/candidate frame, overlay
    text, and enough layout information to render a deterministic 16:9 image."""

    variant_id: str
    strategy: str = "search"  # search | browse | outcome
    title: str = ""
    title_suggestion_index: int | None = None
    frame_timestamp: float | None = None
    frame_path: str | None = None
    overlay_text: str = ""
    visual_rationale: str = ""
    audience_promise: str = ""
    crop: CropBox = Field(default_factory=CropBox)
    focal_x: float = 0.5
    focal_y: float = 0.5
    text: TextLayout = Field(default_factory=TextLayout)
    rendered_path: str | None = None
    visual_verified: bool = False
    source: str = "heuristic"
    model: str | None = None
    backend: str | None = None
    fallback_reason: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ThumbnailVariantPackage(BaseModel):
    schema_version: int = 2
    frames: list[ThumbnailFrame] = Field(default_factory=list)
    variants: list[ThumbnailVariant] = Field(default_factory=list)
    source: str = "heuristic"
    model: str | None = None
    backend: str | None = None
    fallback_reason: str | None = None


class BrandingProfile(BaseModel):
    """Optional, reusable local channel profile. Purely an enhancement: thumbnail
    generation and rendering work identically well with the neutral default."""

    channel_name: str = ""
    audience: str = ""
    tone: str = ""
    primary_color: str = "#111318"
    accent_color: str = "#ffffff"
    logo_path: str | None = None
    font_name: str = "DejaVu Sans Bold (default)"
    font_path: str | None = None
    text_density: str = "balanced"  # minimal | balanced | bold
    emphasis: str = "balanced"  # presenter | product | balanced
    prohibited_phrases: list[str] = Field(default_factory=list)


class ThumbnailFeedbackEntry(BaseModel):
    """A creator's record of which variant they actually published, and (optionally)
    how it performed on YouTube — never a synthetic/predicted score."""

    job_id: str
    variant_id: str
    strategy: str
    title: str
    selected_at: str
    youtube_test_result: str | None = None

