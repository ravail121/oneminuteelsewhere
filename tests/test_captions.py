from elsewhere.captions import caption_records, chunks, scene_durations, timestamp
from elsewhere.demo import demo_story
from elsewhere.models import NarrationAlignment, TimedWord


def test_chunks_respect_limit():
    result = chunks("one two three four five six seven", 3)
    assert " ".join(result) == "one two three four five six seven"
    assert all(len(part.split()) <= 3 for part in result)
    assert len(result[-1].split()) > 1


def test_timestamp():
    assert timestamp(61.234) == "00:01:01,234"


def test_captions_and_scenes_use_word_timestamps():
    story = demo_story()
    words = story.narration.split()
    timed = []
    cursor = 0.0
    for index, word in enumerate(words):
        length = 0.25 if index < 15 else 0.5
        timed.append(TimedWord(word=word, start=cursor, end=cursor + length))
        cursor += length
    alignment = NarrationAlignment(
        model="offline-test",
        transcript=story.narration,
        duration_seconds=cursor,
        words=timed,
    )
    records = caption_records(story.narration, alignment, 6)
    durations = scene_durations(story, alignment)
    assert records[0][0] == 0.0
    assert records[-1][1] == cursor
    assert durations[0] == 15 * 0.25
    assert abs(sum(durations) - cursor) < 1e-9
