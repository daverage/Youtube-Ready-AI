from youtube_ready.captions import build_cues, render_srt, render_vtt
from youtube_ready.models import Transcript, TranscriptSegment


def test_captions_are_plain_utf8_ready_and_split_long_cues():
    transcript = Transcript(
        language="en",
        duration=16,
        segments=[
            TranscriptSegment(
                start=0,
                end=16,
                text="<i>Hello</i> " + "useful words " * 12,
            )
        ],
    )
    cues = build_cues(transcript)
    assert len(cues) >= 3
    assert all(cue.end > cue.start for cue in cues)
    assert all(cues[index].end <= cues[index + 1].start for index in range(len(cues) - 1))
    assert "<i>" not in render_srt(transcript)
    assert render_vtt(transcript).startswith("WEBVTT\n")

