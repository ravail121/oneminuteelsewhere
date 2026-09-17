import pytest
from pydantic import ValidationError

from elsewhere.demo import demo_story
from elsewhere.models import StoryPackage


def test_story_rejects_unknown_fields():
    payload = demo_story().model_dump()
    payload["unexpected"] = "malformed"
    with pytest.raises(ValidationError):
        StoryPackage.model_validate(payload)
