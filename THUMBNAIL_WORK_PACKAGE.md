# Thumbnail Assistant Work Package

## Objective

Move thumbnail creation from a simple frame-and-overlay renderer into a small assistant workflow that assesses available assets, identifies what is missing, gives useful optional upload advice, creates a composition plan, and then renders the strongest truthful thumbnail possible from the available material.

The product should support users with no extra assets while giving users with better images a clear path to materially improve the result.

## Work Status (2026-09-15)

The thumbnail assistant is fully implemented and wired into the web UI, including all items previously tracked under "Remaining" below. The core transcription workflow remains the default: thumbnail frame extraction, thumbnail-specific AI analysis, and thumbnail rendering run only after the user explicitly starts thumbnail planning or rendering. Quick start installs the optional thumbnail dependencies and, on Apple Silicon, the MLX language and vision packages.

### Implemented

- Structured models persist uploaded assets, asset recommendations, assessment diagnostics, composition plans, advice, edit prompts, source assets, and fallback plans.
- Uploaded images are classified into supported roles. Ollama vision analysis is used where available, with deterministic classification and assessment fallbacks.
- Plans can recommend useful assets, explain the reason, and proceed with the best available video frame when no upload is provided.
- The renderer consumes composition and style intent, crops and adjusts the source frame, and can composite selected uploaded assets.
- The web UI has a manual plan-generation action, contextual advice and upload controls, a “Use current assets” path, source-frame view/download, edit-prompt copy, variant selection, and rendering controls.
- Uploading assets after planning refreshes the plan with those assets and supports rendering the updated result.
- Thumbnail runtime startup and model readiness are handled on demand. Quick start covers web, thumbnail, and Apple Silicon MLX dependencies.
- Thumbnail generation is opt-in in the CLI and disabled by default in the standard pipeline.

### Remaining

None. The four items previously tracked here are complete:

1. **Plan-driven text treatments** (`render.py::_draw_overlay_text`): `restrained_high_contrast` now draws no backdrop (outline/shadow only), `large_bold_high_contrast` draws a tight panel sized to the wrapped text block, `oversized_bold_stroked` draws a local gradient confined to the text footprint, and the original full-width strip is retained as the fallback when a variant has no composition plan. Covered by `test_render_variant_text_treatments_produce_distinct_output`.
2. **`visual_verified` accuracy** (`thumbnails.py::_enrich_variants_with_vision`, `_attach_assistant_fields`): the flag is now set `True` only when the Ollama vision assessment call succeeds and returns assessment/composition/advice, and reset to `False` whenever assistant fields are recomputed from heuristic/text-only sources (e.g. a manual reanalyse). The web UI's thumbnail studio (`app.js::buildReadinessSummary`) shows a "Visually verified" vs "Estimated (no image analysis)" badge driven by this flag. Covered by `test_generate_thumbnail_variants_uses_ollama_vision_assessment`, `test_generate_thumbnail_variants_skips_vision_for_text_model`, and `test_refresh_thumbnail_assistant_fields_resets_visual_verified`.
3. **Style selector placement**: moved out of the initial job-setup form (`index.html`) into the manual thumbnail studio toolbar, alongside the download-all control, so the initial transcription setup only has core options and there's a single source of truth for style intent during thumbnail work.
4. **Manual round-trip verification**: exercised the full flow through the running web server's HTTP API against a synthetic test video — job submission, manual plan generation, asset upload, reanalyse (plan refresh with the new asset), "use current assets" render, and reopening the saved session all completed correctly and produced valid rendered JPEGs.

All 78 automated tests pass (`pytest`).

## Current State

The original thumbnail flow was broadly:

```text
choose frame -> choose overlay -> render/export
```

That basic flow has since been extended with assessment, advice, composition planning, optional uploads, and persistence. Thumbnail work is now explicitly user-triggered so it does not add startup or transcription load time.

## Target State

The new flow should be:

```text
assess available assets
-> identify strengths, weaknesses, and missing assets
-> recommend only the uploads that would help
-> let the user add assets or continue
-> produce a thumbnail composition plan
-> render variants from the best available plan
-> save the rationale and improvement advice
```

The system should behave like a thumbnail assistant, not a random thumbnail generator.

## Product Principles

- Prioritise immediate progress: users can always continue with video-only assets.
- Give contextual advice: ask for a product image, presenter cutout, screenshot, result image, logo, or other asset only when it improves the current thumbnail.
- Preserve truth: editorial transformation is allowed; factual transformation is prohibited.
- Keep advanced control optional: most users should not need to become designers.
- Save the reasoning: every generated thumbnail should explain why it works and how it could be improved.

## Non-Goals

- Do not build a full graphic design editor.
- Do not create a long questionnaire before generation.
- Do not require uploads before rendering.
- Do not predict video performance as a certainty.
- Do not invent products, outcomes, reactions, branding, or unsupported before/after states.

## User Flow

### 1. Video-Only Baseline

The user can generate thumbnails from the video alone.

The app should:

- select the strongest available frame;
- crop aggressively when useful;
- enlarge important subjects when quality allows;
- darken, blur, or simplify distracting background areas;
- add text only where it improves the thumbnail;
- produce the strongest possible result from the available footage.

### 2. Asset Readiness Review

Before final rendering, the app should analyse the selected frame and any uploaded assets.

It should return a compact assessment containing:

- frame quality;
- subject clarity;
- product prominence;
- person prominence;
- background clutter;
- negative space;
- text readability potential;
- cutout potential;
- missing or weak assets;
- recommended optional uploads;
- highest-value improvement.

Example:

```json
{
  "frame_quality": "good",
  "product_visibility": "medium",
  "person_visibility": "good",
  "background_clutter": "high",
  "negative_space": "poor",
  "cutout_potential": "good",
  "missing_assets": [
    "clean product image",
    "larger presenter cutout"
  ],
  "recommended_uploads": [
    {
      "type": "product_cutout",
      "priority": "high",
      "reason": "Would allow the product to become the dominant visual anchor"
    },
    {
      "type": "presenter_cutout",
      "priority": "medium",
      "reason": "Would allow a cleaner modern split composition"
    }
  ],
  "highest_value_change": "Upload a clean product image or transparent PNG"
}
```

### 3. Contextual Upload Prompt

The UI should show at most one compact improvement prompt at a time.

Example:

```text
You can make this stronger

The current frame is usable, but the product is relatively small and the background is busy.

Optional: upload a clean product photo or transparent PNG. This would let us make the product 2-3x larger without softening the image.

[Upload product image] [Use current assets]
```

Avoid generic upload boxes such as "Upload more images". The prompt should explain why the asset would help.

### 4. Optional Asset Uploads

The system should support arbitrary supporting assets and classify them into useful roles:

- person;
- product;
- before;
- after;
- result;
- screenshot;
- document;
- chart;
- logo;
- environment;
- alternative frame or photo;
- other.

The classification should not force the asset into the final composition. The model should decide whether the asset improves the thumbnail.

### 5. Thumbnail Style Intent

Add a simple user intent control:

```text
Thumbnail style

Clear & informative
Balanced
Bold / browse-first
```

Map this internally to:

- `clear`: restrained crop, lower text intensity, maximum authenticity;
- `balanced`: moderate crop, strong hierarchy, selective text;
- `bold`: aggressive crop, larger subjects, stronger contrast, more browse-first composition.

The factual transformation rules remain the same for all styles.

### 6. Thumbnail Plan

Before rendering, the model should produce a composition plan.

Example:

```json
{
  "strategy": "browse",
  "visual_anchor": "XVive U45 receiver",
  "secondary_anchor": "presenter",
  "layout": "presenter_left_product_right",
  "background_treatment": "blur_and_darken",
  "product_scale": "large",
  "person_scale": "medium",
  "overlay_text": "GOOD ENOUGH TO GIG?",
  "text_position": "upper_left",
  "text_treatment": "large_bold_high_contrast",
  "remove_or_suppress": [
    "shelf",
    "monitor",
    "small background pedals"
  ]
}
```

The renderer should consume this plan rather than relying on a vague prompt such as "use a sharp frame with high contrast".

### 7. Render and Critique

Each generated candidate should include a short critique.

Example:

```text
Strengths
- Product is recognisable
- Text remains readable on mobile
- Human presence supports trust

Weaknesses
- Product is too small
- Background competes for attention
- Text band covers the main object

Best improvement
Upload a clean product image or use a tighter product shot.
```

The critique should be framed as design diagnostics, not performance prediction.

## Transformation Rules

### Allowed Editorial Transformations

The generated thumbnail may:

- crop heavily;
- reposition subjects;
- isolate a person or product;
- enlarge an object beyond its original frame size when the source supports it;
- remove distracting background objects;
- simplify or blur backgrounds;
- introduce subtle gradients;
- create foreground/background depth;
- add shadows, strokes, exposure, contrast, and colour adjustments;
- combine a source frame with uploaded cutouts or supporting assets.

### Prohibited Factual Transformations

The generated thumbnail must not:

- invent a different product;
- alter branding;
- falsify results;
- fabricate a reaction;
- make someone look emotionally different;
- create unsupported before/after states;
- imply a result, claim, or comparison not present in the video or user-provided assets.

Core rule:

```text
Editorial transformation allowed; factual transformation prohibited.
```

## Fallback Ladder

The assistant should rank available input quality and choose the best feasible path.

```text
A. Best
Uploaded product/person/result assets + selected video frame

B. Good
Clean uploaded asset + video frame

C. Acceptable
Single strong video frame, aggressively recomposed

D. Last resort
Clean crop of the best available frame with minimal or no text
```

If no extra assets exist, do not nag the user. Say:

```text
No extra assets available. We'll optimise the strongest video frame.
```

Then proceed.

## Proposed Data Model

```json
{
  "strategy": "browse",
  "title": "...",
  "style_intent": "balanced",
  "assessment": {
    "strengths": [],
    "weaknesses": [],
    "scores": {
      "subject_clarity": 0.0,
      "visual_hierarchy": 0.0,
      "product_prominence": 0.0,
      "human_prominence": 0.0,
      "background_clutter": 0.0,
      "text_readability": 0.0,
      "negative_space": 0.0,
      "mobile_legibility": 0.0,
      "authenticity": 0.0,
      "curiosity_or_value": 0.0,
      "thumbnail_potential": 0.0
    }
  },
  "asset_recommendations": [
    {
      "type": "product_cutout",
      "priority": "high",
      "reason": "..."
    }
  ],
  "composition": {
    "primary_subject": "...",
    "secondary_subject": "...",
    "layout": "...",
    "crop_instruction": "...",
    "background_treatment": "...",
    "overlay_text": "...",
    "text_position": "...",
    "text_treatment": "...",
    "remove_or_suppress": []
  },
  "source_assets": {
    "frame": "...",
    "uploaded_assets": []
  },
  "edit_prompt": "...",
  "fallback_plan": "...",
  "advice": {
    "overall": "...",
    "strengths": [],
    "weaknesses": [],
    "recommended_uploads": [],
    "highest_value_change": "..."
  }
}
```

## UI Requirements

For each title or thumbnail strategy, show:

- strategy label, for example `Curiosity / Browse`;
- proposed overlay text;
- thumbnail preview;
- why this works;
- current limitations;
- best improvement;
- contextual upload actions;
- use-current-assets action;
- source frame controls;
- copyable edit prompt.

Example:

```text
Curiosity / Browse

GOOD ENOUGH TO GIG?

[thumbnail preview]

Why this works
The XVive unit is the visual anchor while the question adds live-performance stakes not already stated in the title.

Current limitations
The product is relatively small and the shelving adds background clutter.

Best improvement
Upload a clean product photo or transparent PNG.

[Upload product] [Upload person] [Choose another frame] [Use current assets]

Source frame: [View] [Download]
Edit prompt: [Copy]
```

## Implementation Plan

### Phase 1: Assessment Contract

Deliverables:

- Add an asset readiness assessment schema.
- Add asset role classification for uploaded images.
- Add diagnostic scores and plain-language strengths/weaknesses.
- Add recommended upload objects with type, priority, and reason.
- Persist assessment output with the thumbnail package.

Acceptance criteria:

- A video-only thumbnail request returns an assessment before final rendering.
- Uploaded assets are classified into one of the supported asset roles.
- Recommendations are contextual and can be empty.
- The system identifies the highest-value improvement when one exists.

### Phase 2: Composition Plan

Deliverables:

- Add a thumbnail composition plan schema.
- Generate the plan from title strategy, selected frame, assessment, uploaded assets, and style intent.
- Pass the plan into the renderer or image-edit prompt generator.
- Store the final plan alongside the generated thumbnail.

Acceptance criteria:

- Every rendered thumbnail has a composition plan.
- The plan includes primary subject, secondary subject, layout, crop, background treatment, text treatment, and removal/suppression guidance.
- The renderer can fall back to video-only composition when no uploaded assets exist.

### Phase 3: UX Flow

Deliverables:

- Add the asset readiness step to the thumbnail workflow.
- Add contextual upload prompts.
- Add `Use current assets` as a first-class action.
- Add the three-option style intent control.
- Add a compact critique panel for generated candidates.

Acceptance criteria:

- Users can continue without uploading anything.
- The app never blocks rendering because an optional asset is missing.
- Upload prompts explain why the requested asset helps.
- The critique appears for each candidate and includes strengths, weaknesses, and best improvement.

### Phase 4: Safety and Truthfulness Rules

Deliverables:

- Encode allowed editorial transformations.
- Encode prohibited factual transformations.
- Add prompt guardrails for product identity, branding, results, reactions, and before/after states.
- Add tests or fixtures for common risky cases.

Acceptance criteria:

- Prompts explicitly forbid factual invention.
- Uploaded product assets are not replaced with different products.
- Before/after or result imagery is only used when source material supports it.
- The assistant can explain when it refuses or avoids a misleading composition.

### Phase 5: Persistence and Export

Deliverables:

- Save advice, assessment, composition plan, source assets, edit prompt, and fallback plan with each thumbnail package.
- Expose `Why this thumbnail?` and `How to improve it` in the UI.
- Include source frame view/download controls and copyable edit prompt.

Acceptance criteria:

- Reopening a thumbnail package shows the same rationale and advice.
- Exported metadata contains the assessment and composition plan.
- The user can copy the edit prompt used to create the thumbnail.

## Suggested Engineering Tasks

- Define `ThumbnailAssessment`, `AssetRecommendation`, `ThumbnailCompositionPlan`, and `ThumbnailAdvice` types.
- Add server-side validation for the new schemas.
- Add an asset classifier for uploaded images.
- Update thumbnail generation prompt construction to create assessment first, composition plan second, render prompt third.
- Update thumbnail package persistence to store assessment, plan, advice, source frame, uploaded asset references, and edit prompt.
- Add UI components for readiness summary, contextual upload prompt, style intent selector, candidate critique, and saved rationale.
- Add fallback behaviour for video-only, partial asset, and low-quality-frame cases.
- Add fixtures covering product review, tutorial, before/after, screenshot-heavy, and presenter-led videos.
- Add regression tests for factual transformation constraints.

## QA Scenarios

- Video-only product review with a small product in a busy background.
- Presenter-led video where the person is clear but product/result is weak.
- Tutorial video with a screenshot or chart uploaded as supporting material.
- Before/after video where only the before state is visible in the frame.
- User uploads a logo that should support branding but not dominate the thumbnail.
- User uploads an unrelated image that should be ignored or deprioritised.
- Bold style selected with factual constraints still enforced.
- No text recommended because the frame/title pairing is already clear.

## Open Questions

- Image-aware assessment and asset classification use the configured Ollama vision model where available, with local fallback behavior.
- The renderer supports compositing selected uploaded supporting assets; whether multiple assets materially improve results remains a visual QA question.
- Diagnostic scores are stored for internal use; the UI presents plain-language assessment and advice.
- How many candidate thumbnails should be generated per title strategy after the planning step?
- Should users be able to edit the composition plan directly, or only via simple controls?

These are product tuning questions rather than blockers for the remaining implementation items above.

## Definition of Done

- The thumbnail workflow includes assessment, optional contextual asset upload, composition planning, rendering, critique, and persisted advice.
- Users can complete the flow with only a video frame.
- Better assets produce meaningfully better plans and prompts.
- The system explains why a thumbnail works and how it could be improved.
- Editorial transformations are allowed while factual transformations are guarded against.
- The final experience feels like a guided thumbnail assistant rather than a one-shot generator.
