"""Opt-in contract saved with new projects; never retrofits existing stories."""
import re

from .disclosure import DISCLOSURE, detected_cast, strip_disclosure

CAST = ("Lionel Messi", "Cristiano Ronaldo", "IShowSpeed", "MrBeast")
FLAGS = dict.fromkeys(("contains_ai_generated_content", "contains_synthetic_public_figures",
                       "youtube_altered_content_disclosure_required", "unofficial_fictional_parody"), True)
VISUAL_IDENTITIES = (
    "Lionel Messi: recognizable adult male likeness, shorter compact athletic build, light-to-medium skin, "
    "short dark brown hair, neat brown beard, brown eyes, familiar rounded facial contours. "
    "Cristiano Ronaldo: recognizable adult male likeness, taller lean muscular athletic build, "
    "tan skin, short neatly styled dark hair, brown eyes, clean-shaven angular face and strong jaw. "
    "IShowSpeed: recognizable young adult male likeness, slim energetic athletic build, warm brown skin, "
    "short dark hair, expressive animated face, youthful features. "
    "MrBeast: recognizable young adult male likeness, average athletic build, light skin, "
    "short brown hair, clean-shaven approachable face, youthful features. "
    "This is the complete permanent roster of four; only draw the one or two of them chosen for the "
    "current story. Preserve whichever faces, hair, relative heights and body proportions are chosen, "
    "identically, in all eight scenes of that story. "
)
STYLE = ("polished cinematic slightly stylized illustration; clearly drawn, never a fake photograph or "
         "documentary frame. Funny, exaggerated, clickable Shorts-thumbnail energy in every image: big "
         "expressive reactions, dynamic poses, bright vivid colors and comedic exaggeration — while keeping "
         "every character recognizable and dignified, never mocking or humiliating. Give faces big, "
         "exaggerated comedic emotion in every scene image — genuine delighted laughter (even joyful tears-"
         "of-laughter), wide-eyed shock, huge grins, playful surprise — real reaction-face energy, the kind "
         "that reads instantly even as a thumbnail. This is always joyful or comedic emotion, never real "
         "sadness, distress or crying from unhappiness (see RULES on depicting a real recognizable person). "
         "Vary the camera angle, framing, composition, action and expression from scene to scene so no two "
         "of the eight images in a story look alike; repeating the same pose or shot is a missed "
         "opportunity, not a safe choice.")
LIFE_VARIETY = """Permanent life-variety direction for NEW stories:
This story's cast is the one or two chosen recurring identities (see RULES); invent a fresh fictional
life for whichever one or two are chosen, for this specific story.
Vary professions, social roles, age styling, outfits, grooming, props and locations across stories.
They may be young adults, middle-aged or elderly; never imply these fictional lives really happened.
Their recognizable facial identities remain; the adult references describe identity anchors, not
one compulsory haircut, beard or age. Choose age-appropriate hair, facial hair and posture once
for the story, then preserve that chosen appearance across all eight scenes.
Give each chosen character his own distinct unbranded outfit and getup suited to his role: specify
garment types, colors, footwear, accessories and useful work tools. Avoid the same casual clothes every time.
Explore many areas of life: work, study, home, travel, friendship, art, food, farming, repairs,
community life, retirement and harmless fantasy. These are examples, not a fixed rotation.
Use different jobs naturally, such as bakers, gardeners, painters, teachers, shopkeepers,
mechanics or travelers. Do not repeatedly make them footballers, neighbors or the same profession.
Keep the USER-SELECTED story category. Variety in life situations must not change the selected
genre or force a twist, danger or suspense into an emotional, funny or inspirational story.
Make the situation, central object, relationship, conflict and ending genuinely different from
recent stories; changing only a shirt color or job title is not enough.
Store the chosen age styling, profession, full outfits and character looks in continuity_bible.
Put the fictional roles in creative_fingerprint.characters, alongside the chosen identities.
Repeat the COMPLETE chosen character description(s) for this story's one or two cast members in
EVERY image prompt. A given scene image may show just one of them in close-up, or both together,
whichever suits that beat — not every scene needs the full chosen cast crammed into one frame.
Different stories have different looks; scenes within one story keep the same chosen looks.
Keep narration very simple and action-led; visual wardrobe details belong in visual planning,
not long spoken descriptions. Never rewrite an already approved story to impose a new role or age.
All existing public-figure, harmless-fiction, unbranded-clothing and narrator-only voice rules remain.
"""
RULES = """Permanent recurring-cast roster of four: Lionel Messi, Cristiano Ronaldo, IShowSpeed and MrBeast,
as recognizable synthetic public figures. Cristiano Ronaldo is mandatory in every single story — never
omit him. Optionally pair him with exactly one more from Messi, IShowSpeed or MrBeast, your choice;
never all four, never anyone outside this roster, never a story without Ronaldo. Vary whether Ronaldo
appears alone or paired, and vary which partner you pick, across stories rather than defaulting to the
same pairing every time.
Give whichever one or two you choose a genuine, meaningful part in the story, not a silent cameo.
Every story must include one clear, satisfying beat where Ronaldo does something impressively better
than expected — a skill, feat or clever solution directly tied to that story's plot and conflict, not
a random aside — and other characters visibly react to it (delight, awe, big reactions). Make this the
story's single most memorable, most shareable moment; it must feel earned by what actually happens in
the story, not just stated.
No other named or speaking character; background people only when necessary, anonymous and silent.
Entirely harmless unofficial fiction, never a claim about real events, scandals or personal allegations.
No endorsements of any product, business, party, religion, investment, medicine or channel.
No sexual or hateful content, graphic violence, serious crimes, drugs or humiliating depictions.
No real club, team, kit, logo, sponsor, trophy, trademarked branding, or real product/show name tied
to anyone on the roster (for example: no real football clubs, no Feastables, MrBeast Burger or Beast Games).
Use simple unbranded clothing appropriate to each fictional role, fixed within each story.
Vary everyday roles, settings and harmless fantastical situations; do not repeatedly tell football
stories or repeatedly tell internet-challenge/prize-stunt stories just because of who is in the cast.
Keep the selected category but adapt its stakes to harmless fiction: Crime requires intentional harmless
fictional wrongdoing, such as a playful theft, trick or secret plan, with clues and a clear solution;
an accidental mistake alone is not Crime. Thriller means safe urgency, Horror means playful fantastical
unease, Dark Twist means harmless surprise.
No dangerous allegations or degrading portrayals just to satisfy a genre. Everyone retains dignity.
Never depict a recurring cast member alone with visible sadness, distress or isolation, in narration
or in any scene image; image moderation for a real recognizable person is far stricter on that
composition. If a story features two, keep them together for emotional or vulnerable beats, or if one
must appear alone, keep their expression calm, composed and dignified rather than visibly sad.
Only the selected AI narrator performs all lines; never clone, impersonate or imitate any real voice or accent.
"""


def enabled(settings):
    return settings.raw.get("recurring_cast", {}).get("version") == 1


def contract(settings):
    if not enabled(settings):
        return ""
    result = RULES + "\n" + VISUAL_IDENTITIES + "\nStyle: " + STYLE
    if settings.raw.get("recurring_cast", {}).get("life_variety_version") == 1:
        result += "\n" + LIFE_VARIETY
    return result


def with_disclosure(story):
    """Separate only known boilerplate; retain the model's raw response elsewhere."""
    scenes = []
    for scene in story.scenes:
        spoken = strip_disclosure(scene.narration)
        if spoken:
            scenes.append(scene.model_copy(update={"narration": spoken,
                "visual_prompt": strip_disclosure(scene.visual_prompt),
                "on_screen_emphasis": strip_disclosure(scene.on_screen_emphasis)}))
    return story.model_copy(update={"narration": strip_disclosure(story.narration),
        "scenes": scenes, "hook": strip_disclosure(story.hook),
        "continuity_bible": strip_disclosure(story.continuity_bible),
        "description": strip_disclosure(story.description), "youtube_disclosure": DISCLOSURE})


# The speaking-character allowlist is deliberately looser: any token that plainly refers back
# to a roster member (including common nicknames) must never be flagged as a new character.
_CAST_SPEAKER_TOKENS = {"Lionel", "Messi", "Cristiano", "Ronaldo", "IShowSpeed", "Speed", "MrBeast", "Beast"}


def local_cast_errors(story):
    """Conservative reference checks, not proof of semantic safety or likeness quality."""
    errors = []
    text = f"{story.title} {story.premise} {story.narration}"
    present = detected_cast(story.narration)
    if not present:
        errors.append("Recurring cast requires one or two roster members (Messi, Ronaldo, IShowSpeed, MrBeast) in the narration")
    elif len(present) > 2:
        errors.append(f"A single story may feature at most two roster members, found {len(present)}: {', '.join(present)}")
    prohibited = ("murder", "murdered", "rape", "raped", "cocaine", "heroin", "methamphetamine", "porn",
                  "sexual", "terrorist", "suicide", "torture", "tortured", "bloodbath", "scandal",
                  "adultery", "humiliate", "humiliated", "bribery", "fraud", "money laundering",
                  "Nike", "Adidas", "FIFA", "UEFA", "Ballon d'Or", "Real Madrid", "FC Barcelona",
                  "Manchester United", "Paris Saint-Germain", "Inter Miami", "Al Nassr",
                  "Feastables", "MrBeast Burger", "Beast Games", "Beast Philanthropy", "Team Trees", "Team Seas")
    for term in prohibited:
        if re.search(r"\b" + re.escape(term) + r"\b", text, re.IGNORECASE):
            errors.append(f"Prohibited public-figure portrayal or branding: {term}")
    if re.search(r"\b(?:endorses?|sponsored by|vote for|buy now|invest in|subscribe to|true story|really happened)\b", text, re.IGNORECASE):
        errors.append("Endorsements or real-event claims are not allowed for the recurring cast")
    # Pronouns are never proper names; only an actual capitalized name here is a real extra character.
    pronouns = {"He", "She", "It", "They", "We", "You", "I", "This", "That", "These", "Those"}
    for name in re.findall(r"\b([A-Z][a-z]+)\s+(?:said|asked|replied|whispered|shouted)\b", story.narration):
        if name not in {*_CAST_SPEAKER_TOKENS, *pronouns}:
            errors.append(f"Another speaking character is not allowed: {name}")
    return errors
