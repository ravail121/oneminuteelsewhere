"""Unit tests for the permanent recurring-cast YouTube title/description/tag rules."""
import copy
import json
from pathlib import Path

import pytest

from elsewhere import youtube
from elsewhere.config import Settings, load_settings
from elsewhere.disclosure import (
    DISCLOSURE,
    MAX_DESCRIPTION_HASHTAGS,
    TITLE_HARD_LIMIT,
    TITLE_SOFT_LIMIT,
    combined_hashtags,
    detected_cast,
    is_recurring_cast_story,
    validate_youtube_title,
    youtube_tags,
    youtube_title,
)
from elsewhere.models import StoryPackage
from elsewhere.upload_titles import roman_upload_title

ROOT = Path(__file__).resolve().parents[1]


def cast_story(title="The Vanished Lighthouse Bell", story_category="Mystery", description=None,
               narration="Messi and Ronaldo solve the mystery of a vanished lighthouse bell together."):
    payload = json.loads((ROOT / "stories/wedding-photo-test.json").read_text())
    payload.update(title=title, story_category=story_category, youtube_disclosure=DISCLOSURE,
                   narration=narration,
                   description=description or "Messi and Ronaldo solve the mystery of a vanished lighthouse bell.")
    return StoryPackage.model_validate(payload)


def non_cast_story():
    payload = json.loads((ROOT / "stories/wedding-photo-test.json").read_text())
    return StoryPackage.model_validate(payload)


def settings():
    raw = copy.deepcopy(load_settings(ROOT / "config.yaml").raw)
    return Settings(ROOT, raw)


def test_cast_story_detection_survives_an_earlier_disclosure_wording():
    story = cast_story()
    assert is_recurring_cast_story(story)
    assert not is_recurring_cast_story(story.model_copy(update={"youtube_disclosure": ""}))
    assert not is_recurring_cast_story(story.model_copy(update={"youtube_disclosure": "Fictional entertainment, not real."}))
    assert not is_recurring_cast_story(non_cast_story())
    # A previous wording of the CURRENT four-name disclosure must still be recognized.
    reworded_four_name_disclosure = ("This is unofficial fiction for entertainment. Not endorsed by "
        "Lionel Messi, Cristiano Ronaldo, IShowSpeed or MrBeast, their clubs, channels or sponsors.")
    assert is_recurring_cast_story(story.model_copy(update={"youtube_disclosure": reworded_four_name_disclosure}))
    # An OLDER two-name-only disclosure genuinely does not feature the full roster, so it must
    # NOT be treated as a full-roster disclosure: that would falsely claim IShowSpeed/MrBeast.
    old_two_name_disclosure = ("This is an unofficial, fictional AI-generated story created for entertainment. "
                       "It is not affiliated with or endorsed by Lionel Messi, Cristiano Ronaldo, their clubs, sponsors, or representatives.")
    assert not is_recurring_cast_story(story.model_copy(update={"youtube_disclosure": old_two_name_disclosure}))


def test_detected_cast_finds_first_name_surname_or_stage_name_in_roster_order():
    assert detected_cast("Lionel found the box. Ronaldo helped.") == ["Messi", "Ronaldo"]
    assert detected_cast("MrBeast built it. IShowSpeed watched.") == ["IShowSpeed", "MrBeast"]
    assert detected_cast("Just a quiet afternoon.") == []
    assert detected_cast("Cristiano Ronaldo smiled.") == ["Ronaldo"]


@pytest.mark.parametrize("title", [
    "The Vanished Lighthouse Bell",
    "A Very Long Title That Goes On And On About A Lighthouse Bell Mystery",
    "The Small Cup",
    "Mirror Showed Tomorrow",
    "Messi and Ronaldo's Secret Garden",  # already mentions both names; must not double them
])
def test_generated_title_always_satisfies_every_permanent_rule(title):
    result = youtube_title(cast_story(title=title))
    validate_youtube_title(result)  # must not raise
    assert result.startswith("Messi and Ronaldo ")
    assert result.endswith("#Shorts")
    assert result.lower().count("messi") == 1
    assert result.lower().count("ronaldo") == 1
    assert len(result) <= TITLE_HARD_LIMIT
    for banned in ("official", "real", "leaked", "caught"):
        assert banned not in result.lower()


def test_generated_title_names_only_this_storys_own_one_or_two_cast_members():
    solo = youtube_title(cast_story(title="A Big Treehouse Project", narration="MrBeast built a huge treehouse."))
    assert solo.startswith("MrBeast ") and "Messi" not in solo and "Ronaldo" not in solo and "IShowSpeed" not in solo
    pair = youtube_title(cast_story(title="A Fast Lap", narration="IShowSpeed and MrBeast raced go-karts."))
    assert pair.startswith("IShowSpeed and MrBeast ")


def test_short_hook_reads_naturally_and_stays_under_the_soft_limit():
    result = youtube_title(cast_story(title="The Cup With Two Paths"))
    assert result == "Messi and Ronaldo The Cup With Two Paths #Shorts"
    assert len(result) <= TITLE_SOFT_LIMIT


def test_different_stories_produce_different_titles():
    a = youtube_title(cast_story(title="The Cup With Two Paths"))
    b = youtube_title(cast_story(title="The Vanished Lighthouse Bell"))
    assert a != b


def test_hook_override_replaces_only_the_variable_part_of_the_title():
    story = cast_story(title="The Cup With Two Paths")
    assert youtube_title(story, hook_override="Built A Time Machine") == "Messi and Ronaldo Built A Time Machine #Shorts"


def test_a_single_cast_member_title_is_valid():
    validate_youtube_title("MrBeast Builds a Treehouse #Shorts")  # must not raise


@pytest.mark.parametrize("title", [
    "Ronaldo and Messi Official Video #Shorts",
    "Messi and Ronaldo Leaked Footage #Shorts",
    "Messi and Ronaldo Caught on Camera #Shorts",
    "Messi and Ronaldo Real Life Story #Shorts",
    "Messi and Ronaldo and Messi Again #Shorts",  # a name repeated
    "A Story About Messi and Ronaldo #Shorts",  # not near the beginning
    "Messi and Ronaldo Save The Day",  # missing #Shorts
    "M" * 90 + " Messi and Ronaldo #Shorts",  # exceeds the hard length limit
    "Messi, Ronaldo and IShowSpeed Play Together #Shorts",  # more than two cast members
])
def test_validate_youtube_title_rejects_every_forbidden_pattern(title):
    with pytest.raises(ValueError):
        validate_youtube_title(title)


def test_tags_include_every_permanent_base_tag_plus_a_category_tag_deduplicated():
    config = settings()
    tags = youtube_tags(config, cast_story(story_category="Funny"))
    assert set(config.publishing["tags"]) <= set(tags)
    assert "funny story" in tags
    assert len(tags) == len({t.lower() for t in tags})


def test_tags_include_only_this_storys_own_cast_members():
    config = settings()
    tags = youtube_tags(config, cast_story(narration="MrBeast built a huge treehouse."))
    assert "MrBeast" in tags and "MrBeast story" in tags
    assert "Messi" not in tags and "Ronaldo" not in tags and "IShowSpeed" not in tags
    paired = youtube_tags(config, cast_story(narration="Messi and Ronaldo fixed the cup."))
    assert "Messi and Ronaldo" in paired


def test_tags_respect_the_500_character_budget():
    config = settings()
    tags = youtube_tags(config, cast_story(), extra=["x" * 60] * 20)
    assert sum(len(t) for t in tags) + max(0, len(tags) - 1) <= 500


def test_extra_tag_with_a_banned_word_is_rejected():
    config = settings()
    with pytest.raises(ValueError):
        youtube_tags(config, cast_story(), extra=["official merch"])


def test_combined_hashtags_leads_with_shorts_and_this_storys_own_cast():
    hashtags = combined_hashtags(["#ClayArt", "#FriendshipStory"], present=["Messi", "Ronaldo"])
    assert hashtags[0] == "#Shorts"
    assert {"#Messi", "#LeoMessi", "#Ronaldo", "#CristianoRonaldo", "#CR7"} <= set(hashtags[1:6])
    assert "#ClayArt" in hashtags and "#FriendshipStory" in hashtags
    assert len(hashtags) == len({h.lower() for h in hashtags})  # no duplicates
    assert len(hashtags) <= MAX_DESCRIPTION_HASHTAGS


def test_combined_hashtags_only_include_this_storys_actual_cast():
    hashtags = combined_hashtags([], present=["MrBeast"])
    assert "#MrBeast" in hashtags
    assert "#Messi" not in hashtags and "#Ronaldo" not in hashtags and "#IShowSpeed" not in hashtags


def test_combined_hashtags_add_football_extras_only_for_messi_or_ronaldo():
    no_football = combined_hashtags([], present=["IShowSpeed", "MrBeast"])
    assert "#Football" not in no_football and "#Soccer" not in no_football and "#GOAT" not in no_football
    with_football = combined_hashtags([], present=["Messi"])
    assert "#Football" in with_football and "#Soccer" in with_football and "#GOAT" in with_football


def test_combined_hashtags_survive_the_cap_even_with_many_story_hashtags():
    many = [f"#StoryTag{i}" for i in range(20)]
    hashtags = combined_hashtags(many, present=["Messi", "Ronaldo"])
    assert len(hashtags) == MAX_DESCRIPTION_HASHTAGS
    assert {"#Shorts", "#Messi", "#Ronaldo"} <= set(hashtags)


def test_combined_hashtags_never_include_real_club_sponsor_or_other_athlete_names():
    hashtags = " ".join(combined_hashtags(["#GreatDay"], present=["Messi", "Ronaldo", "IShowSpeed", "MrBeast"])).lower()
    for forbidden in ("realmadrid", "barcelona", "barça", "psg", "manchesterunited", "intermiami",
                      "alnassr", "championsleague", "ucl", "fifa", "uefa", "ballondor",
                      "nike", "adidas", "neymar", "feastables", "mrbeastburger", "beastgames", "shopmrbeast"):
        assert forbidden not in hashtags.replace(" ", "")


def test_combined_hashtags_deduplicates_case_insensitively():
    hashtags = combined_hashtags(["#shorts", "#MESSI"], present=["Messi"])
    assert hashtags.count("#Shorts") + hashtags.count("#shorts") == 1
    assert hashtags.count("#Messi") + hashtags.count("#MESSI") == 1


def test_upload_body_description_hashtags_are_messi_ronaldo_and_researched_viral_set():
    config = settings()
    story = cast_story(title="The Cup With Two Paths")
    body = youtube.upload_body(config, story, False)
    last_line = body["snippet"]["description"].splitlines()[-1]
    assert last_line.startswith("#Shorts #Messi #LeoMessi #Ronaldo #CristianoRonaldo #CR7")


def test_non_cast_story_keeps_its_own_plain_hashtags_only():
    config = settings()
    story = non_cast_story()
    body = youtube.upload_body(config, story, False)
    last_line = body["snippet"]["description"].splitlines()[-1]
    assert last_line == " ".join(story.hashtags)
    assert "#Messi" not in last_line


def test_upload_body_uses_the_generated_title_and_category_tag_for_cast_stories():
    config = settings()
    story = cast_story(title="The Cup With Two Paths", story_category="Emotional")
    body = youtube.upload_body(config, story, False)
    assert body["snippet"]["title"] == "Messi and Ronaldo The Cup With Two Paths | Emotional Story #Shorts"
    assert "emotional story" in body["snippet"]["tags"]
    assert body["snippet"]["description"].count(DISCLOSURE) == 1
    description_hashtags = body["snippet"]["description"].splitlines()[-1]
    for tag in ("#Shorts", "#Messi", "#Ronaldo"):
        assert tag in description_hashtags
    assert all(tag in description_hashtags for tag in story.hashtags)
    assert body["snippet"]["categoryId"] == "24"
    assert body["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}


def test_upload_body_uses_a_single_cast_member_when_thats_all_the_story_has():
    config = settings()
    story = cast_story(title="A Big Treehouse Project", story_category="Funny",
                       narration="MrBeast built a huge treehouse for the whole block.")
    body = youtube.upload_body(config, story, False)
    assert body["snippet"]["title"].startswith("MrBeast ")
    assert "Messi" not in body["snippet"]["title"] and "Ronaldo" not in body["snippet"]["title"]
    assert "MrBeast" in body["snippet"]["tags"]
    assert "Messi" not in body["snippet"]["tags"] and "Ronaldo" not in body["snippet"]["tags"]


def test_upload_body_adds_story_label_without_imposing_cast_on_non_cast_stories():
    config = settings()
    story = non_cast_story()
    assert not is_recurring_cast_story(story)
    body = youtube.upload_body(config, story, False)
    assert body["snippet"]["title"] == story.title + " | Original Story #Shorts"
    assert body["snippet"]["tags"] == config.publishing["tags"]


def test_upload_body_edit_overrides_keep_the_permanent_prefix_and_fixed_disclosure():
    config = settings()
    story = cast_story(title="The Cup With Two Paths")
    body = youtube.upload_body(config, story, False, title_hook="Built A Time Machine",
        description_summary="A custom human-edited summary sentence.", extra_tags=["clay art"])
    assert body["snippet"]["title"] == "Messi and Ronaldo Built A Time Machine | Mystery Story #Shorts"
    assert body["snippet"]["description"].startswith("A custom human-edited summary sentence.")
    assert body["snippet"]["description"].count(DISCLOSURE) == 1
    assert "clay art" in body["snippet"]["tags"]


def test_upload_body_rejects_an_edited_title_hook_that_smuggles_a_banned_word():
    config = settings()
    story = cast_story(title="The Cup With Two Paths")
    with pytest.raises(youtube.YouTubeError):
        youtube.upload_body(config, story, False, title_hook="Official Leaked Footage")


@pytest.mark.parametrize("title", ["मेरी गेंद कहाँ गई", "میری گیند کہاں گئی", "என் பந்து எங்கே போனது", "消えた赤いボール", "My Ball Went Missing"])
def test_all_upload_titles_use_roman_script_and_category_without_altering_story(title):
    config = settings()
    config.raw["brand"]["language"] = "hi"
    config.raw["dashboard_brief"] = {"video_mode": "viral"}
    story = non_cast_story().model_copy(update={"title": title, "story_category": "Funny"})
    before = story.model_dump()
    body = youtube.upload_body(config, story, False)
    result = body["snippet"]["title"]
    assert result.isascii() and result.endswith(" | Viral Funny #Shorts")
    assert len(result) <= 100
    assert story.model_dump() == before
    assert body["snippet"]["defaultAudioLanguage"] == "hi"


def test_romanized_titles_are_bounded_deduplicated_and_keep_cast():
    config = settings()
    story = cast_story(title="मेरी बहुत प्यारी लाल गेंद कहाँ गई", story_category="Funny")
    result = youtube.upload_body(config, story, False)["snippet"]["title"]
    validate_youtube_title(result)
    assert result.isascii() and "Funny Story" in result
    result = roman_upload_title("Long title " * 100, "Mystery")
    assert len(result) <= 100 and result.endswith("Mystery Story #Shorts")
    assert roman_upload_title(result, "Mystery") == result
    assert "<" not in roman_upload_title("A <test> story", "Funny")
