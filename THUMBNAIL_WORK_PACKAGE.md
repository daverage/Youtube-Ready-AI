# Thumbnail Packaging Studio — Work Package

## Objective

Replace the current advisory thumbnail cards with three coherent, editable, upload-ready title–thumbnail packages. Each package must connect one title, one visually verified frame, complementary overlay text, and a rendered 16:9 image. The workflow must remain local-first and degrade gracefully when local AI, vision models, FFmpeg, or OpenCV are unavailable.

## Current-State Problem

The pipeline passes all generated titles to thumbnail generation, but `ThumbnailIdea` does not record which title an idea supports. The text model receives timestamps and nearby transcript content without seeing the candidate frames, so visual descriptions can disagree with the selected image. Frame ranking emphasizes sharpness and face presence rather than subject relevance or composition. The web UI displays overlay text beneath an unedited 640×360 still instead of producing a finished thumbnail.

## Product Outcomes

- Generate three meaningfully different packages: search-led, browse-led, and outcome/stakes-led.
- Make the title and thumbnail complementary rather than repetitive.
- Preview the actual crop, overlay, and branding at desktop and small/mobile sizes.
- Allow creators to change the title, frame, crop, overlay, and text placement.
- Export three high-resolution images suitable for YouTube title-and-thumbnail testing.
- Preserve truthful packaging by checking concepts against transcript evidence and unsupported-claim constraints.

## Scope

### 1. Shared Packaging Brief

Promote the existing `PackagingBrief` into reusable pipeline data instead of discarding it after metadata generation. Persist it in `youtube_metadata.json` or a versioned `packaging.json`. Thumbnail generation must consume its target viewer, payoff, claim, result, contrasts, proof points, visual subjects, search phrases, and `must_not_imply` values.

Affected areas: `metadata.py`, `models.py`, `pipeline.py`, session loading in `web/jobs.py`.

### 2. Title–Thumbnail Variant Model

Replace or evolve `ThumbnailIdea` into a versioned `ThumbnailVariant` containing:

- stable variant ID and strategy (`search`, `browse`, or `outcome`)
- selected title and optional title-suggestion ID
- frame timestamp and source path
- overlay text
- visual rationale and audience promise
- normalized crop/focal-point coordinates
- text position, alignment, style, and safe margins
- rendered image filename
- model/backend provenance and fallback notes

Continue reading legacy `thumbnail_ideas.json` files so previous sessions remain loadable.

### 3. Candidate Frame Pipeline

- Extract candidates across the full runtime rather than stopping at the earliest scene changes.
- Include scene changes, evenly distributed samples, chapter boundaries, demonstrations, and conclusion/result moments.
- Extract master frames at a minimum of 1280×720 while retaining lightweight previews for the UI.
- Detect duplicates, blur, closed or awkward eyes where practical, face/object cropping, and usable negative space.
- Keep roughly 20–30 candidates and shortlist 8–12 diverse frames.
- Allow a creator to upload a dedicated thumbnail photograph or select another point from the video.

### 4. Optional Visual Analysis

Introduce a dedicated image-analysis interface; do not overload the text-only metadata backend. When a compatible local vision model is available, describe concrete visible subjects, composition, expression, product visibility, and text-safe space for each shortlisted frame. Match title hypotheses to these visual facts.

Without a vision model, use technical scoring plus transcript context, label the result as unverified, and keep manual frame selection fully functional. Never fabricate visible details.

### 5. Variant Planning and Validation

Generate exactly one initial variant per title strategy. Each overlay should add information, evidence, tension, or outcome without copying the title. Validate:

- title and overlay length limits
- duplication between title and overlay
- conflicts with `must_not_imply`
- unsupported claims or visual descriptions
- whether the opening 30–60 seconds begins delivering the package promise
- presence of a valid frame and renderable crop

Warnings should be visible but should not prevent manual export unless the package is technically invalid.

### 6. Deterministic Rendering

Add a shared Python renderer so CLI and web outputs are identical. Render 1280×720 JPEG or PNG files with deterministic crop, type scale, wrapping, contrast treatment, and safe margins. Bundle an appropriately licensed default font or document a reliable packaged alternative. Keep rendered text out of optional image-generation prompts.

Write outputs such as:

```text
thumbnails/
  candidates/
  variant-search.jpg
  variant-browse.jpg
  variant-outcome.jpg
thumbnail_variants.json
```

### 7. Web Thumbnail Studio

Replace the read-only gallery with an editor that supports:

- three side-by-side package tabs/cards
- editable title and overlay fields
- candidate-frame strip and uploaded-image selection
- crop/zoom and focal-point controls
- text position and basic brand styling
- large canvas plus small-size legibility preview
- regenerate overlay, regenerate package, and lock-field actions
- download one image or all variants
- visible warnings and AI/fallback provenance

Automatic generation must still complete without requiring an interactive pause. Edits and rendered files must survive session reloads.

### 8. Branding Presets

Support an optional reusable local profile containing channel name, audience, tone, fonts, colours, logo, preferred text density, presenter/product emphasis, and prohibited phrases. Ship a neutral default. Branding is an enhancement, not a prerequisite for generating thumbnails.

## Delivery Plan

### Milestone 1 — Data and Coherence

- Persist `PackagingBrief`.
- Add the versioned variant schema and legacy loader.
- Pair each title with one thumbnail strategy.
- Pass shared evidence and constraints into variant generation.

### Milestone 2 — Frame Quality

- Upgrade full-runtime extraction and master resolution.
- Improve duplicate and composition scoring.
- Add optional visual analysis and explicit unverified fallback state.

### Milestone 3 — Rendering

- Implement deterministic cropping and text rendering.
- Produce three finished images from the CLI pipeline.
- Add promise/claim validation and warnings.

### Milestone 4 — Editor and Export

- Build the web thumbnail studio.
- Add update/render/download API routes.
- Persist edits and support dedicated image uploads.

### Milestone 5 — Branding and Feedback

- Add local branding presets.
- Record the selected variant and optional YouTube test result for future channel-level guidance.

## Acceptance Criteria

- Every AI-generated variant identifies exactly one title, one frame, and one strategy.
- Three visually and rhetorically distinct variants are generated when sufficient titles and frames exist.
- Every automatic variant produces a 1280×720-or-higher image with readable, non-clipped text.
- The selected frame's visual description is based on image evidence when vision analysis is enabled.
- No-AI and failed-AI runs still return editable, renderable variants.
- Titles and overlays do not merely duplicate one another.
- Unsupported-claim and opening-promise checks produce actionable warnings.
- Users can edit, rerender, download, close, and reopen a session without losing changes.
- Existing sessions using `thumbnail_ideas.json` continue to load.
- Source video files remain untouched and no media is uploaded.

## Test Plan

- Unit-test variant validation, title/overlay deduplication, crop bounds, safe text layout, frame distribution, and legacy migration.
- Test the heuristic path, model failure, missing FFmpeg/OpenCV/vision model, missing frames, short videos, portrait source video, and long videos.
- Assert rendered dimensions, output filenames, non-empty images, and deterministic results for fixed inputs.
- Add pipeline tests confirming the packaging brief reaches thumbnail generation and all three rendered files are included in the publishing package.
- Add FastAPI tests for editing, rerendering, uploads, downloads, path traversal protection, and session reload.
- Manually verify large and small previews with presenter-led, product-led, screen-recording, and no-face videos.

## Risks and Guardrails

- Vision-model availability varies: preserve a clear manual and heuristic path.
- Font metrics and image rendering can differ by platform: package fonts and use one renderer.
- Additional frame extraction increases runtime and storage: cap candidates and delete superseded previews safely.
- Previous session schemas must remain readable through explicit versioning/migration.
- Avoid false performance scores; only real YouTube test results should be treated as evidence of effectiveness.

## Out of Scope

- Uploading videos or thumbnails to YouTube.
- Cloud image generation or mandatory external APIs.
- Predicting click-through rate with an unsupported synthetic score.
- Automatically changing published videos based on analytics.

## Definition of Done

The feature is complete when a local CLI or web run reliably produces three truthful, paired title–thumbnail packages; the web app can edit and persist them; each package exports as a high-resolution image; fallback behavior remains usable offline; and the full automated test suite passes.
