from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .config import Settings
from .models import Scene, StoryPackage, StoryReview

DEMO_LINES = [
    "At exactly midnight, every mirror in Mara's silent apartment showed tomorrow instead of her reflection.",
    "In the kitchen mirror, she watched herself spill coffee beside a newspaper dated Tuesday morning.",
    "The front-page headline reported a citywide blackout, but one building remained brightly lit: her own.",
    "She covered every mirror. Behind each cloth, tomorrow's frightened Mara began knocking from the other side.",
    "One uncovered mirror showed her holding a note she had never written: Do not turn on the hallway light.",
    "The hallway switch suddenly clicked by itself, and every future image went completely, impossibly dark.",
    "Then the bedroom mirror slowly returned, showing yesterday. Mara saw herself installing the mirrors while deeply asleep.",
    "On the cold glass, yesterday's Mara wrote one final warning: Tonight, let me wake up instead of you.",
]

CURATED_LINES = [
    "At 03:00, the station’s matter fabricator produced a cracked helmet labeled KEIRA NOLL—while Keira was still wearing it.",
    "The fabricator borrowed tagged objects from exactly one minute ahead, reshaping them into requested parts.",
    "Its receipt named her helmet as the source, at airlock seven, 03:01.",
    "Then Keira received an emergency repair order for that airlock. The report came from the fabricator.",
    "She canceled the repair, but the countdown continued: once begun, a retrieval followed the source’s inventory tag, not its location.",
    "If she wore the tagged helmet at 03:01, it would be pulled backward, exposing her beside the open hatch.",
    "Keira peeled the tag from her helmet and sealed it inside the fabricator’s removable control core.",
    "At 03:01, the core vanished. The cracked helmet on the tray collapsed into circuitry—the future matter it had borrowed all along.",
]

EIGHTH_SHADOW_LINES = [
    "At noon on Orison-4, the seven survey towers cast eight shadows, though the white sun never moved.",
    "The extra shadow slid uphill and stopped beneath biologist Sera Venn’s boots.",
    "Her lamp passed through it. The darkness was a carpet of airborne cells, each turning black when it absorbed oxygen.",
    "Sera followed the moving patch to a hairline crack in the habitat’s buried air pipe.",
    "The colony was not attacking; it was feeding on the leak and concentrating wherever oxygen escaped.",
    "She patched the pipe, but the cells gathered again, forming a dark trail toward the crew’s rover.",
    "The others ordered quarantine. Sera crossed the barrier instead and found a second fracture beneath the rover’s fuel-cell housing.",
    "She sealed it before ignition. The eighth shadow dissolved into seven—the planet had never copied a tower; it had outlined their escaping air.",
]

EIGHTH_SHADOW_FINAL_LINES = [
    "At noon on Orison-4, the seven survey towers cast eight shadows, though the white sun never moved.",
    "The extra shadow slid uphill, crossed bare rock, and pooled beneath biologist Sera Venn’s habitat.",
    "Inside a sealed sampler, its airborne cells stayed transparent without oxygen, then darkened the instant Sera added one measured breath.",
    "The crew assumed infestation and started the ultraviolet sterilizer’s countdown.",
    "Sera noticed the colony thickened along the buried air line, never around people. It was following a chemical gradient.",
    "She invoked the station’s science quarantine, pausing sterilization long enough to excavate the darkest ground.",
    "Beneath it, leaked oxygen streamed from a cracked pipe onto a hot battery cable scheduled to energize in seconds.",
    "Sera sealed the pipe before ignition. The planet’s first life turned clear, and the eighth shadow vanished—it had drawn the danger humans could not see.",
]


def demo_story() -> StoryPackage:
    scenes = [
        Scene(
            narration=line,
            visual_prompt=(
                "Same fictional woman Mara, age 29, short dark hair, charcoal sweater, modern small apartment, "
                f"scene {index + 1}: {line}"
            ),
            on_screen_emphasis="",
        )
        for index, line in enumerate(DEMO_LINES)
    ]
    return StoryPackage(
        format="short",
        title="Every Mirror Showed Tomorrow",
        premise="A woman discovers her mirrors are windows into tomorrow, but yesterday is trying to take her place.",
        hook=DEMO_LINES[0],
        scenes=scenes,
        description="A one-minute original science-fiction mystery about reflections, tomorrow, and the self waiting behind the glass.",
        hashtags=["#ShortStory", "#ScienceFiction", "#Mystery"],
    )


def demo_review() -> StoryReview:
    return StoryReview(
        score=9,
        approved=True,
        originality_risk="low",
        policy_risk="low",
        problems=[],
        correction_notes="Dry-run fixture approved.",
    )


def curated_story() -> StoryPackage:
    continuity = (
        "cinematic realistic science-fiction, vertical portrait composition, orbital station "
        "fabrication bay with brushed steel walls and cyan work lights, fictional engineer "
        "Keira Noll age 34 with copper-brown skin and close-cropped black hair, orange utility "
        "jumpsuit, white ceramic matter fabricator with a circular cyan aperture, consistent face "
        "and costume, no legible text, no logos, no watermark"
    )
    scene_details = [
        "wide view, Keira wears an intact white EVA helmet while staring at a separate cracked duplicate helmet resting inside the printer tray",
        "medium view, tagged repair tools seem to stretch through the glowing fabricator aperture as a clock mechanism indicates a one-minute interval",
        "over-shoulder view, Keira studies a physical helmet inventory tag beside an abstract airlock diagram and red countdown on the console",
        "tense corridor view, Keira receives an alert as sealed airlock seven flashes red behind her and the fabricator glows in the distance",
        "close investigative view, Keira traces the alert cable into the fabricator while its retrieval countdown continues despite her canceled order",
        "dramatic view beside the hatch, Keira imagines her helmet being pulled backward through a cyan temporal distortion toward the fabricator",
        "decisive close view, Keira peels the small physical inventory tag from her helmet and locks it inside the removable fabricator control core",
        "aftermath wide view, the control-core socket is suddenly empty while the cracked helmet collapses into exposed circuitry and Keira remains safe",
    ]
    scenes = [
        Scene(
            narration=line,
            visual_prompt=f"{continuity}; {detail}",
            on_screen_emphasis="",
        )
        for line, detail in zip(CURATED_LINES, scene_details, strict=True)
    ]
    return StoryPackage(
        format="short",
        title="The Helmet Borrowed Tomorrow",
        premise=(
            "An orbital engineer discovers that a matter fabricator borrows its raw material "
            "from one minute ahead—and her helmet is its next source."
        ),
        hook=CURATED_LINES[0],
        scenes=scenes,
        description=(
            "A one-minute original science-fiction mystery about an engineer, a matter "
            "fabricator, and an inventory tag connected to tomorrow."
        ),
        hashtags=["#ScienceFiction", "#Mystery", "#ShortStory"],
    )


def eighth_shadow_story() -> StoryPackage:
    continuity = (
        "cinematic realistic science-fiction, vertical portrait composition, bright white noon "
        "on fictional exoplanet Orison-4, pale lavender desert, seven slender survey towers, "
        "fictional biologist Sera Venn age 38 with deep brown skin and long black braids tied "
        "back, ivory exploration suit with teal panels and clear helmet, consistent face and "
        "costume, crisp natural light, no legible text, no logos, no watermark"
    )
    scene_details = [
        "wide establishing view, seven towers stand beneath the fixed sun but eight sharply separated shadows stretch across the pale ground",
        "low medium view, one detached black patch crawls uphill against the other shadows and pools beneath Sera's boots as she freezes",
        "close scientific view, Sera shines a handheld lamp through the patch, revealing millions of tiny airborne cells darkening around oxygen vapor",
        "tracking view, Sera follows the living dark carpet to a hairline fracture above a buried habitat air pipe, faint vapor escaping",
        "close view, Sera kneels to seal the cracked pipe while the black cellular colony concentrates harmlessly around the last oxygen wisps",
        "wide view, the repaired ground clears but the cells gather into a new dark trail leading from Sera toward a parked six-wheel rover",
        "tense view, Sera steps across a glowing quarantine barrier and reaches beneath the rover fuel-cell housing while distant crew gesture warnings",
        "resolved wide view, Sera seals the second fracture before ignition and the extra dark patch dissolves, leaving exactly seven tower shadows",
    ]
    scenes = [
        Scene(
            narration=line,
            visual_prompt=f"{continuity}; {detail}",
            on_screen_emphasis="",
        )
        for line, detail in zip(EIGHTH_SHADOW_LINES, scene_details, strict=True)
    ]
    return StoryPackage(
        format="short",
        title="The Eighth Shadow",
        premise=(
            "A biologist on a bright exoplanet discovers that an impossible moving shadow is "
            "a native colony tracing the crew's invisible air leaks."
        ),
        hook=EIGHTH_SHADOW_LINES[0],
        scenes=scenes,
        description=(
            "A one-minute original science-fiction mystery about seven towers, an eighth "
            "shadow, and the alien life that notices what the crew cannot see."
        ),
        hashtags=["#ScienceFiction", "#Mystery", "#ShortStory"],
    )


def eighth_shadow_final_story() -> StoryPackage:
    continuity = (
        "cinematic realistic science-fiction, vertical portrait composition, bright white noon "
        "on fictional exoplanet Orison-4, pale lavender desert, seven slender survey towers, "
        "fictional biologist Sera Venn age 38 with deep brown skin and long black braids tied "
        "back, ivory exploration suit with teal panels and clear helmet, consistent face and "
        "costume, crisp natural light, no legible text, no logos, no watermark"
    )
    scene_details = [
        "wide establishing view, seven towers stand beneath the fixed sun but eight sharply separated dark shapes stretch across the pale ground",
        "low tracking view, one detached black patch crawls uphill across bare rock and pools beneath Sera's compact habitat module",
        "laboratory close view, Sera studies a sealed transparent sampler where tiny airborne cells turn visibly black only around a measured oxygen bubble",
        "tense interior view, crew behind Sera activate bright ultraviolet sterilization lamps while an abstract countdown glows without readable symbols",
        "overhead investigative view, the black colony forms its densest narrow trail directly above one buried habitat air line and avoids nearby crew",
        "decisive medium view, Sera engages a sealed science-quarantine control and kneels with tools above the darkest section of ground",
        "dramatic cutaway view, Sera exposes a cracked oxygen pipe leaking toward a heat-glowing battery cable as distant equipment powers up",
        "resolved wide view, Sera clamps the pipe, the harmless cells turn transparent, and exactly seven tower shadows remain around the safe crew",
    ]
    scenes = [
        Scene(
            narration=line,
            visual_prompt=f"{continuity}; {detail}",
            on_screen_emphasis="",
        )
        for line, detail in zip(EIGHTH_SHADOW_FINAL_LINES, scene_details, strict=True)
    ]
    return StoryPackage(
        format="short",
        title="The Eighth Shadow",
        premise=(
            "A biologist discovers that an impossible shadow is a native cellular colony "
            "making a lethal oxygen leak visible before the station powers up."
        ),
        hook=EIGHTH_SHADOW_FINAL_LINES[0],
        scenes=scenes,
        description=(
            "A one-minute original science-fiction mystery about seven towers, an eighth "
            "shadow, and alien life that reveals an invisible danger."
        ),
        hashtags=["#ScienceFiction", "#Mystery", "#ShortStory"],
    )


def create_demo_images(story: StoryPackage, settings: Settings, job_dir: Path) -> list[Path]:
    spec = settings.formats[story.format]
    width, height = int(spec["width"]), int(spec["height"])
    try:
        font_path = "/System/Library/Fonts/Helvetica.ttc"
        font = ImageFont.truetype(font_path, 54)
        small = ImageFont.truetype(font_path, 32)
    except OSError:
        font = small = ImageFont.load_default()
    files: list[Path] = []
    for index, scene in enumerate(story.scenes):
        image = Image.new("RGB", (width, height), "#030617")
        draw = ImageDraw.Draw(image)
        for y in range(height):
            blend = y / max(1, height - 1)
            draw.line((0, y, width, y), fill=(3 + int(8 * blend), 6 + int(10 * blend), 23 + int(30 * blend)))
        cx, cy = width // 2, int(height * 0.42)
        for radius in range(320, 30, -8):
            alpha = (320 - radius) / 320
            color = (30 + int(40 * alpha), 120 + int(80 * alpha), 210 + int(45 * alpha))
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=color, width=4)
        draw.text((70, 90), "ONE MINUTE ELSEWHERE", font=small, fill="#7DE9FF")
        draw.text((70, int(height * 0.72)), f"SCENE {index + 1}", font=font, fill="white")
        words = scene.narration.split()
        lines = [" ".join(words[i:i + 7]) for i in range(0, len(words), 7)]
        draw.multiline_text((70, int(height * 0.78)), "\n".join(lines), font=small, fill="#BEC9E8", spacing=10)
        path = job_dir / f"scene-{index + 1:02d}.png"
        image.save(path)
        files.append(path)
    return files
