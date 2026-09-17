"""Free scene partitioning of the authoritative narration; never writes a story."""
import re

from .disclosure import require_content_only
from .models import Scene


def narration_scenes(text, count=8):
    require_content_only(text)
    words = text.split()
    if len(words) < count * 4:
        raise ValueError("The script needs enough words for eight scenes.")
    sentence_ends = [i for i, word in enumerate(words, 1) if re.search(r'''[.!?।؟][”"']*$''', word)]
    cuts = [0]
    for index in range(1, count):
        minimum = cuts[-1] + 4
        maximum = len(words) - (count - index) * 4
        choices = [end for end in sentence_ends if minimum <= end <= maximum]
        target = round(index * len(words) / count)
        cuts.append(min(choices, key=lambda end: abs(end - target)) if choices else min(maximum, max(minimum, target)))
    cuts.append(len(words))
    return [Scene(narration=" ".join(words[cuts[i]:cuts[i + 1]]),
                  visual_prompt="Pending fresh visual planning from the exact approved narration.")
            for i in range(count)]


def repair_scene_partition(story):
    """Replace incomplete model scene scaffolding, not the narration or title."""
    partition = " ".join(s.narration for s in story.scenes).split()
    if len(story.scenes) == 8 and partition == story.narration.split():
        # The model sometimes drifts the separately-generated hook slightly from
        # scene 1's actual wording. hook has no downstream use beyond this exact
        # equality check, so keep it in sync instead of blocking on the mismatch.
        if story.hook == story.scenes[0].narration:
            return story
        return story.model_copy(update={"hook": story.scenes[0].narration})
    scenes = narration_scenes(story.narration)
    return story.model_copy(update={"scenes": scenes, "hook": scenes[0].narration, "continuity_bible": ""})
