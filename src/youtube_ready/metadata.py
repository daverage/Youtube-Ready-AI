from __future__ import annotations

import json
import re
from importlib.util import find_spec
from collections.abc import Callable

from pydantic import BaseModel, Field, ValidationError

from youtube_ready.chapters import fallback_chapters, normalize_chapters
from youtube_ready.models import Chapter, PackagingBrief, PublishingMetadata, TitleSuggestion, Transcript

MAX_SOURCE_CHARS = 48_000
SUMMARY_CHUNK_CHARS = 18_000
PREFERRED_TITLE_MAX = 70
YOUTUBE_TITLE_MAX = 100
MAX_HASHTAGS = 8

_MLX_MODEL_CACHE: dict[str, tuple] = {}
_MLX_VLM_MODEL_RE = re.compile(r"gemma|llava|bakllava|vision|vlm|qwen.*vl|moondream|minicpm", re.IGNORECASE)


class LLMMetadata(BaseModel):
    titles: list[TitleSuggestion] = Field(default_factory=list)
    description: str = ""
    hashtags: list[str] = Field(default_factory=list)
    chapters: list[Chapter] = Field(default_factory=list)


def _call_ollama(model: str, prompt: str) -> dict:
    import ollama

    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        format="json",
        # The thumbnail-variant prompt asks for three variants, each carrying a
        # full nested assessment/composition/advice object — a large response.
        # Ollama's per-model default num_predict can be small enough to cut
        # that off mid-object, which then fails json.loads with a "line N
        # column 1" error and forces the (much lower quality) heuristic
        # fallback. Ask for enough headroom that a real request finishes.
        options={"num_predict": 8192},
    )
    return json.loads(_extract_json_object(response["message"]["content"]))


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)


def _balanced_json_spans(text: str) -> list[str]:
    """Every substring starting at a '{' and ending at its matching '}'."""
    spans = []
    for start, char in enumerate(text):
        if char != "{":
            continue
        depth = 0
        in_string = False
        escaped = False
        for end in range(start, len(text)):
            current = text[end]
            if in_string:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
            elif current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
                if depth == 0:
                    spans.append(text[start : end + 1])
                    break
    return spans


def _extract_json_object(text: str) -> str:
    """Extract the largest valid JSON object from model output."""
    text = text.strip()
    best = None
    for source in [*_JSON_FENCE_RE.findall(text), text]:
        for candidate in _balanced_json_spans(source):
            try:
                json.loads(candidate)
            except ValueError:
                continue
            if best is None or len(candidate) > len(best):
                best = candidate
    return best if best is not None else text


def _load_mlx(model: str) -> tuple:
    """Load an MLX model, returning (kind, bundle)."""
    if model in _MLX_MODEL_CACHE:
        return _MLX_MODEL_CACHE[model]

    errors: dict[str, str] = {}
    kinds = ("vlm",) if _MLX_VLM_MODEL_RE.search(model) else ("lm",)
    for kind in kinds:
        package = "mlx_lm" if kind == "lm" else "mlx_vlm"
        if find_spec(package) is None:
            errors[kind] = f"not installed (No module named {package!r})"
            continue
        try:
            if kind == "lm":
                from mlx_lm import load

                bundle = load(model)
            else:
                from mlx_vlm import load as load_vlm

                bundle = load_vlm(model)
        except ModuleNotFoundError as exc:
            errors[kind] = f"not installed ({exc})"
            continue
        except ImportError as exc:
            errors[kind] = str(exc)
            break
        except Exception as exc:  # noqa: BLE001
            errors[kind] = str(exc)
            continue
        _MLX_MODEL_CACHE[model] = (kind, bundle)
        return (kind, bundle)

    detail = "; ".join(f"{kind}: {message}" for kind, message in errors.items())
    loader = "mlx-vlm" if kinds == ("vlm",) else "mlx-lm"
    raise RuntimeError(
        f"could not load {model!r} with {loader} ({detail}). "
        "Install MLX support with `pip install -e '.[mlx]'` and run on an Apple Silicon session with Metal access."
    )


def _generate_mlx_lm(bundle: tuple, prompt: str) -> str:
    from mlx_lm import generate

    mlx_model, tokenizer = bundle
    if getattr(tokenizer, "chat_template", None):
        formatted = tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}], tokenize=False, add_generation_prompt=True
        )
    else:
        formatted = prompt
    return generate(mlx_model, tokenizer, prompt=formatted, max_tokens=4096, verbose=False)


def _generate_mlx_vlm(bundle: tuple, prompt: str, images: list[str] | None = None) -> str:
    from mlx_vlm import generate
    from mlx_vlm.prompt_utils import apply_chat_template

    mlx_model, processor = bundle
    formatted = apply_chat_template(processor, mlx_model.config, prompt, num_images=len(images or []))
    result = generate(mlx_model, processor, formatted, image=images or None, max_tokens=4096, verbose=False)
    return result.text if hasattr(result, "text") else str(result)


def _call_mlx(model: str, prompt: str) -> dict:
    """Run a local Hugging Face model directly in-process via Apple's MLX framework."""
    kind, bundle = _load_mlx(model)
    text = _generate_mlx_vlm(bundle, prompt) if kind == "vlm" else _generate_mlx_lm(bundle, prompt)
    return json.loads(_extract_json_object(text))


def list_ollama_models() -> list[str]:
    import ollama

    response = ollama.list()
    models = response.get("models", []) if isinstance(response, dict) else response.models
    names = []
    for entry in models:
        name = entry.get("model") or entry.get("name") if isinstance(entry, dict) else getattr(entry, "model", None)
        if name:
            names.append(name)
    return sorted(names)


def list_mlx_models() -> list[str]:
    """List models already downloaded to the local Hugging Face cache."""
    try:
        from huggingface_hub import scan_cache_dir
    except ModuleNotFoundError as exc:
        raise RuntimeError("mlx-lm is not installed; run `pip install -e '.[mlx]'` (Apple Silicon only)") from exc
    cache_info = scan_cache_dir()
    return sorted(repo.repo_id for repo in cache_info.repos if repo.repo_type == "model")


def list_models(backend: str) -> list[str]:
    if backend == "ollama":
        return list_ollama_models()
    if backend == "mlx":
        return list_mlx_models()
    raise ValueError(f"unknown metadata backend {backend!r}; choose from: ollama, mlx")


def _backend_call(backend: str) -> Callable[[str, str], dict]:
    if backend == "ollama":
        return _call_ollama
    if backend == "mlx":
        return _call_mlx
    raise ValueError(f"unknown metadata backend {backend!r}; choose from: ollama, mlx")


def _timed_transcript(transcript: Transcript) -> str:
    return "\n".join(f"[{segment.start:.1f}-{segment.end:.1f}] {segment.text}" for segment in transcript.segments)


def _summarize_long_transcript(source: str, model: str, call: Callable[[str, str], dict]) -> str:
    """Compress long source while preserving both navigation and packaging evidence."""
    summaries: list[str] = []
    for start in range(0, len(source), SUMMARY_CHUNK_CHARS):
        chunk = source[start : start + SUMMARY_CHUNK_CHARS]
        data = call(
            model,
            "You are compressing a timestamped video transcript for later YouTube metadata and chapter generation.\n"
            "Use ONLY information present in the supplied transcript chunk.\n\n"
            "Return exactly one valid JSON object and nothing else.\n"
            "Schema: {\"summary\":\"...\"}\n\n"
            "Preserve compactly:\n"
            "- every meaningful topic transition and its exact timestamp\n"
            "- important names, entities, products, people and terminology\n"
            "- questions asked and problems being solved\n"
            "- claims, comparisons, demonstrations, evidence and examples\n"
            "- surprising or counter-intuitive points\n"
            "- recommendations, results and conclusions\n"
            "- anything that explains why a viewer would care\n"
            "Do not add interpretation that is not supported by the transcript.\n\n"
            f"TRANSCRIPT CHUNK:\n{chunk}",
        )
        summaries.append(str(data.get("summary", "")))
    return "\n".join(summaries)


def _brief_prompt(source: str, context: str | None) -> str:
    return f"""You are extracting a topic-neutral YouTube packaging brief from a video transcript.

The transcript is the factual source of truth. Do not invent anything. Creator context may explain audience/channel context but must not introduce claims about what happens in this video.

Return exactly one valid JSON object and nothing else.

SCHEMA
{{"core_topic":"...","video_type":"review|tutorial|comparison|story|experiment|news|opinion|interview|entertainment|other","viewer_intent":"search|browse|mixed","target_viewer":"...","primary_subjects":["..."],"viewer_problem":"...","viewer_payoff":"...","primary_claim":"...","result_or_conclusion":"...","surprising_points":["..."],"questions_answered":["..."],"stakes":"...","contrasts":["..."],"proof_points":["..."],"search_phrases":["..."],"visual_subjects":["..."],"must_not_imply":["..."]}}

RULES
- Extract the central subject and the strongest truthful viewer payoff.
- Classify video_type by what the video actually does, not by topic.
- viewer_intent is search when the viewer likely arrives with a specific question/task, browse when the appeal is primarily discovery/story/novelty, otherwise mixed.
- search_phrases are natural ways a viewer could describe THIS supported topic. They are terminology suggestions, not claims about actual search volume.
- surprising_points must genuinely be surprising, counter-intuitive or notable in the source; otherwise return [].
- result_or_conclusion must be empty if the source does not reach one.
- stakes should explain why the outcome matters, and may be empty.
- visual_subjects should name concrete people/objects/screens/scenes actually discussed or shown according to the transcript.
- must_not_imply should list tempting but unsupported conclusions or claims a title/thumbnail should avoid.
- Keep fields concise. Prefer empty strings/lists to invented material.

CREATOR CONTEXT
{context or '(none supplied)'}

SOURCE
{source}"""


def build_packaging_brief(
    transcript: Transcript,
    *,
    model: str | None,
    context: str | None = None,
    backend: str = "ollama",
    call: Callable[[str, str], dict] | None = None,
    source: str | None = None,
) -> PackagingBrief:
    """Build a reusable packaging brief. Safe to call independently; existing callers need not change."""
    if not model:
        return PackagingBrief(core_topic=_title_seed(transcript), primary_subjects=[_title_seed(transcript)])
    call = call or _backend_call(backend)
    source = source or _timed_transcript(transcript)
    if len(source) > MAX_SOURCE_CHARS:
        source = _summarize_long_transcript(source, model, call)
    try:
        return PackagingBrief.model_validate(call(model, _brief_prompt(source, context)))
    except (Exception, ValidationError):
        return PackagingBrief(core_topic=_title_seed(transcript), primary_subjects=[_title_seed(transcript)])


def _prompt(
    source: str,
    duration: float,
    context: str | None,
    brief: PackagingBrief | None = None,
    channel_name: str | None = None,
) -> str:
    brief_json = (brief or PackagingBrief()).model_dump_json(indent=2)
    channel_hashtag_rule = (
        f"\n- Include one hashtag for the channel itself, derived from this channel name: {channel_name}."
        if channel_name and channel_name.strip()
        else ""
    )
    return f"""You are generating accurate YouTube publishing metadata.

The transcript/source below is the factual source of truth. The packaging brief is only a structured interpretation of that source. Never invent facts, products, specifications, people, claims, recommendations, results or topics.

Return exactly one valid JSON object and nothing else. Do not include markdown, reasoning or commentary.

SCHEMA
{{"titles":[{{"title":"...","reason":"..."}},{{"title":"...","reason":"..."}},{{"title":"...","reason":"..."}}],"description":"...","hashtags":["..."],"chapters":[{{"start":0,"title":"Introduction"}}]}}

TITLE STRATEGY
Return exactly three meaningfully different title hypotheses:
1. SEARCH-LED: clearest useful subject + explicit payoff/answer. Optimise for a viewer who already wants this information.
2. BROWSE-LED: recognisable subject + honest curiosity/tension. Create an information gap without withholding the subject or using vague clickbait.
3. OUTCOME/STAKES-LED: emphasise the supported result, consequence, transformation, comparison or why-it-matters angle. If no result/stakes exist, use the strongest distinct benefit-led angle instead.

TITLE RULES
- Every title must accurately set expectations for the actual video.
- Prefer roughly 45-65 characters when clarity allows; 70 is a useful soft ceiling, not a target.
- Never exceed {YOUTUBE_TITLE_MAX} characters.
- Put the recognisable subject, entity or viewer value early where natural because titles may be truncated.
- Use concrete nouns and specific outcomes/questions over generic phrases.
- Do not keyword-stuff, repeat synonymous phrases, use ALL CAPS, or add unsupported superlatives.
- Avoid generic clickbait such as "You Won't Believe", "This Changes Everything", "Secret", "Shocking" unless the exact claim is substantively supported and useful.
- Do not completely spoil a browse-led question when genuine curiosity is part of the value, but never hide the topic itself.
- The three titles must test different viewer motivations, not merely reword one sentence.
- Each reason must begin with "Search-led:", "Browse-led:", or "Outcome-led:" respectively and explain the hypothesis in <=15 words.

DESCRIPTION RULES
- Write a unique, natural description of about 3-5 useful sentences.
- The first 1-2 sentences must stand alone in the collapsed preview: state what the video is about and the viewer payoff.
- Mention the primary subject/key phrase naturally near the beginning when supported.
- Then summarise the most useful specific areas, comparisons, demonstrations, results or conclusions actually covered.
- Use relevant terminology and synonyms naturally, never as a keyword list.
- Do not invent links, specifications, claims, recommendations, sponsors or calls to action.

HASHTAG RULES
- Return 5-8 hashtags: a mix of specific and general, never all of one kind.
- Include one hashtag for every specific named product, brand, model, or entity actually mentioned (from the packaging brief's primary_subjects when present) - do not omit these.
- Include 2-3 broader, general-interest tags for the video's overall category/niche (e.g. the wider genre, activity, or field this video belongs to) so viewers browsing that general topic can discover it - these should be genuinely applicable, not invented trends.
- Do not invent trends or claim search popularity.
- Avoid generic filler such as #YouTube, #Video, #Viral, #Trending.
- Each must start with # and contain no spaces or punctuation besides underscores.
- Do not repeat equivalent tags.{channel_hashtag_rule}

CHAPTER HARD REQUIREMENTS
- The first chapter must start at exactly 0 seconds.
- Provide at least 3 chapters when the video duration and real topic transitions support them.
- Sort chapters by ascending numeric start time.
- Every chapter must last at least 10 seconds.
- The final chapter must begin at least 10 seconds before the video ends.
- Choose timestamps only where the source shows a genuine topic or section transition.
- Do not invent, approximate, or evenly distribute timestamps merely to create chapters.

CHAPTER TITLE STYLE
- Use short, self-contained topic labels in Title Case, normally 2-6 words.
- Describe WHAT the section is about rather than quoting the transcript.
- Do not start mid-thought or with filler words.
- Prefer useful labels over clever marketing copy.
- Prefer 4-10 chapters for longer videos and fewer for short videos.

VIDEO CONTEXT
Video duration: {duration:.1f} seconds.
Creator context: {context or '(none supplied)'}
Channel name: {channel_name.strip() if channel_name and channel_name.strip() else '(none supplied)'}

PACKAGING BRIEF
{brief_json}

SOURCE
{source}"""


def _title_seed(transcript: Transcript) -> str:
    text = " ".join(segment.text for segment in transcript.segments[:6])
    text = re.sub(
        r"^(?:okay|right|so|well|hello|hi|hey|welcome)[,.!\s]+",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    )
    sentence = re.split(r"[.!?]", text)[0].strip()
    words = sentence.split()[:10]
    return " ".join(words).strip(" ,.-") or "Untitled Video"


_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "for", "with", "is", "are",
    "this", "that", "it", "you", "your", "we", "i", "so", "as", "at", "be", "by", "from", "my",
    "how", "what", "why", "when", "into", "about", "our", "us", "will", "can", "just", "now",
}


def _hashtag_seed(transcript: Transcript) -> list[str]:
    text = " ".join(segment.text for segment in transcript.segments[:16])
    words = re.findall(r"[A-Za-z][A-Za-z0-9']*", text)
    seen: dict[str, None] = {}
    for word in words:
        cleaned = word.strip("'").lower()
        if len(cleaned) < 4 or cleaned in _STOPWORDS or not cleaned.isalpha():
            continue
        seen.setdefault(cleaned, None)
    return [f"#{word.capitalize()}" for word in list(seen)[: MAX_HASHTAGS - 1]]


def _trim_title(title: str, max_chars: int = YOUTUBE_TITLE_MAX) -> str:
    title = " ".join(title.strip().split())
    if len(title) <= max_chars:
        return title
    shortened = title[:max_chars].rsplit(" ", 1)[0].rstrip(" :;,-")
    return shortened or title[:max_chars]


def heuristic_metadata(
    transcript: Transcript, reason: str | None = None, *, channel_name: str | None = None
) -> PublishingMetadata:
    seed = _trim_title(_title_seed(transcript), PREFERRED_TITLE_MAX)
    opener = " ".join(segment.text.strip() for segment in transcript.segments[:4]).strip()
    description = opener[:600].rsplit(" ", 1)[0] if len(opener) > 600 else opener
    titles = [
        TitleSuggestion(title=seed, reason="Search-led: uses the clearest supported opening subject."),
        TitleSuggestion(title=_trim_title(f"{seed}: What Matters Most", PREFERRED_TITLE_MAX), reason="Browse-led: adds a restrained curiosity gap without inventing a claim."),
        TitleSuggestion(title=_trim_title(f"A Practical Look at {seed}", PREFERRED_TITLE_MAX), reason="Outcome-led: frames the supported topic around practical viewer value."),
    ]
    return PublishingMetadata(
        titles=titles,
        description=description or "A clear walkthrough of the topic covered in this video.",
        hashtags=_with_channel_hashtag(_hashtag_seed(transcript), channel_name),
        chapters=fallback_chapters(transcript),
        language=transcript.language,
        source="fallback" if reason else "heuristic",
        fallback_reason=reason,
    )


_HASHTAG_CLEAN_RE = re.compile(r"[^A-Za-z0-9_]")


def _normalize_hashtags(raw: list[str], *, max_tags: int = MAX_HASHTAGS) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for item in raw:
        cleaned = _HASHTAG_CLEAN_RE.sub("", str(item).strip().lstrip("#"))
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        tags.append(f"#{cleaned}")
        if len(tags) == max_tags:
            break
    return tags


def _channel_hashtag(channel_name: str | None) -> str | None:
    if not channel_name or not channel_name.strip():
        return None
    cleaned = _HASHTAG_CLEAN_RE.sub("", channel_name.strip())
    return f"#{cleaned}" if cleaned else None


def _with_channel_hashtag(hashtags: list[str], channel_name: str | None) -> list[str]:
    """Guarantee the channel's own hashtag is present, since a model may forget it
    even when asked; trims the lowest-priority generated tag rather than the
    channel tag if we're already at the cap."""
    channel_tag = _channel_hashtag(channel_name)
    if not channel_tag or channel_tag.lower() in {tag.lower() for tag in hashtags}:
        return hashtags
    combined = hashtags[: MAX_HASHTAGS - 1] + [channel_tag]
    return combined


def generate_metadata(
    transcript: Transcript,
    *,
    model: str | None,
    context: str | None = None,
    backend: str = "ollama",
    call: Callable[[str, str], dict] | None = None,
    channel_name: str | None = None,
) -> PublishingMetadata:
    call = call or _backend_call(backend)
    if not model:
        metadata = heuristic_metadata(transcript, channel_name=channel_name)
    else:
        try:
            source = _timed_transcript(transcript)
            if len(source) > MAX_SOURCE_CHARS:
                source = _summarize_long_transcript(source, model, call)

            brief = build_packaging_brief(
                transcript,
                model=model,
                context=context,
                backend=backend,
                call=call,
                source=source,
            )
            generated = LLMMetadata.model_validate(
                call(model, _prompt(source, transcript.duration, context, brief, channel_name))
            )
            titles = [
                TitleSuggestion(title=_trim_title(item.title), reason=item.reason.strip())
                for item in generated.titles[:3]
                if item.title.strip()
            ]
            if len(titles) < 2 or not generated.description.strip():
                raise ValueError("metadata model returned too little usable content")
            hashtags = _with_channel_hashtag(_normalize_hashtags(generated.hashtags), channel_name)
            metadata = PublishingMetadata(
                titles=titles,
                description=generated.description.strip(),
                hashtags=hashtags,
                chapters=normalize_chapters(generated.chapters, transcript),
                language=transcript.language,
                source="llm",
                model=model,
                backend=backend,
                packaging_brief=brief,
            )
        except (Exception, ValidationError) as exc:
            metadata = heuristic_metadata(
                transcript, reason=f"Local metadata model unavailable or invalid: {exc}", channel_name=channel_name
            )
            metadata.model = model
            metadata.backend = backend

    if not metadata.chapters:
        metadata.chapters_note = "This video is too short to satisfy YouTube's three-chapter, 10-second minimum rules."
    return metadata
