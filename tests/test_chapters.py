from youtube_ready.chapters import chapters_are_valid, fallback_chapters, format_chapter_time, normalize_chapters
from youtube_ready.models import Chapter, Transcript, TranscriptSegment


def transcript(duration: float = 620) -> Transcript:
    return Transcript(
        language="en",
        duration=duration,
        segments=[
            TranscriptSegment(start=0, end=20, text="Welcome and overview"),
            TranscriptSegment(start=200, end=230, text="Setting up the project"),
            TranscriptSegment(start=400, end=430, text="Testing the final result"),
        ],
    )


def test_normalize_repairs_invalid_model_chapters():
    proposed = [
        Chapter(start=4, title="Intro"),
        Chapter(start=9, title="Too soon"),
        Chapter(start=205, title="Setup"),
        Chapter(start=615, title="Too late"),
    ]
    result = normalize_chapters(proposed, transcript())
    assert chapters_are_valid(result, 620)
    assert result[0].start == 0


def test_short_video_has_no_invalid_chapters():
    assert fallback_chapters(transcript(29.9)) == []


def test_hour_timestamp_format():
    assert format_chapter_time(3661) == "1:01:01"

