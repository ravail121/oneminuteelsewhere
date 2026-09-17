from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"


def font(size: int, bold: bool = False):
    filename = "NimbusSans-Bold.otf" if bold else "NimbusSans-Regular.otf"
    candidates = [
        Path("/usr/share/fonts/opentype/urw-base35") / filename,
        Path("/usr/share/fonts/truetype/nimbus") / filename,
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def banner() -> None:
    width, height = 2560, 1440
    base = Image.new("RGB", (width, height), "#020513")
    draw = ImageDraw.Draw(base)
    for y in range(height):
        ratio = y / height
        draw.line(
            (0, y, width, y),
            fill=(2 + int(5 * ratio), 5 + int(8 * ratio), 19 + int(25 * ratio)),
        )
    glow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((650, 180, 1900, 1430), fill=(42, 178, 255, 72))
    glow = glow.filter(ImageFilter.GaussianBlur(170))
    base = Image.alpha_composite(base.convert("RGBA"), glow)
    icon = Image.open(ASSETS / "channel-icon.png").convert("RGB")
    icon = icon.resize((360, 360), Image.Resampling.LANCZOS)
    mask = Image.new("L", icon.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, 359, 359), fill=255)
    icon_rgba = Image.new("RGBA", icon.size)
    icon_rgba.paste(icon, mask=mask)
    base.alpha_composite(icon_rgba, (565, 540))
    draw = ImageDraw.Draw(base)
    draw.text((980, 555), "ONE MINUTE", font=font(104, True), fill="#FFFFFF")
    draw.text((980, 665), "ELSEWHERE", font=font(104, True), fill="#7DE9FF")
    draw.text(
        (985, 800),
        "ONE MINUTE. ANOTHER REALITY.",
        font=font(38),
        fill="#B7C7E7",
    )
    base.convert("RGB").save(ASSETS / "youtube-banner.jpg", quality=94, subsampling=0)


def avatar_and_watermark() -> None:
    icon = Image.open(ASSETS / "channel-icon.png").convert("RGB")
    icon.resize((800, 800), Image.Resampling.LANCZOS).save(
        ASSETS / "youtube-avatar.jpg", quality=95
    )
    icon.resize((150, 150), Image.Resampling.LANCZOS).save(
        ASSETS / "youtube-watermark.png"
    )


if __name__ == "__main__":
    banner()
    avatar_and_watermark()
