import json
import textwrap
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.comparison import (
    GOVERNMENT_WARNING_TEXT,
    CheckName,
    CheckStatus,
    ExpectedNetContents,
    ExpectedReview,
    NetContentsUnit,
    OverallOutcome,
)
from app.extraction import ImageMediaType, PreparedImage


@dataclass(frozen=True, slots=True)
class Artwork:
    brand_name: str
    class_type: str
    alcohol_content: str
    net_contents: str
    warning_variant: Literal["required", "altered"]
    unreadable: bool


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    id: str
    artwork: Artwork
    expected: ExpectedReview
    expected_outcome: OverallOutcome
    required_checks: dict[CheckName, CheckStatus]
    requires_uncertainty: bool


@dataclass(frozen=True, slots=True)
class EvaluationManifest:
    revision: str
    cases: list[EvaluationCase]


def load_manifest(path: Path) -> EvaluationManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases: list[EvaluationCase] = []
    for item in payload["cases"]:
        artwork = Artwork(**item["artwork"])
        expected_payload = item["expected"]
        expected = ExpectedReview(
            brand_name=expected_payload["brand_name"],
            class_type=expected_payload["class_type"],
            abv=Decimal(expected_payload["abv"]),
            net_contents=ExpectedNetContents(
                value=Decimal(expected_payload["net_contents"]),
                unit=NetContentsUnit(expected_payload["net_contents_unit"]),
            ),
        )
        cases.append(
            EvaluationCase(
                id=item["id"],
                artwork=artwork,
                expected=expected,
                expected_outcome=OverallOutcome(item["expected_outcome"]),
                required_checks={
                    CheckName(name): CheckStatus(status)
                    for name, status in item["required_checks"].items()
                },
                requires_uncertainty=item["requires_uncertainty"],
            )
        )
    return EvaluationManifest(revision=payload["revision"], cases=cases)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def _draw_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    position: tuple[int, int],
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    width: int,
    fill: str,
    spacing: int = 8,
) -> int:
    lines = textwrap.wrap(text, width=width)
    x, y = position
    for line in lines:
        draw.text((x, y), line, fill=fill, font=font)
        box = draw.textbbox((x, y), line, font=font)
        y += box[3] - box[1] + spacing
    return y


def render_fixture(case: EvaluationCase, destination: Path) -> PreparedImage:
    destination.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1_600, 1_200), color="#f2ead8")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (90, 70, 1_510, 1_130),
        radius=30,
        fill="#fffdf7",
        outline="#283f50",
        width=6,
    )
    draw.text((150, 125), case.artwork.brand_name, fill="#173247", font=_font(84))
    draw.text((150, 245), case.artwork.class_type, fill="#283f50", font=_font(48))
    draw.line((150, 330, 1_450, 330), fill="#ad8a4b", width=4)
    draw.text((150, 380), case.artwork.alcohol_content, fill="#162734", font=_font(52))
    draw.text((1_050, 380), case.artwork.net_contents, fill="#162734", font=_font(52))

    warning = GOVERNMENT_WARNING_TEXT
    if case.artwork.warning_variant == "altered":
        warning = warning.replace("may cause health problems", "might cause health problems")
    heading, body = warning.split(":", 1)
    draw.text(
        (150, 590),
        f"{heading}:",
        fill="#111111",
        font=_font(38),
        stroke_width=1,
        stroke_fill="#111111",
    )
    _draw_lines(
        draw,
        body.strip(),
        position=(150, 650),
        font=_font(32),
        width=80,
        fill="#111111",
        spacing=10,
    )

    if case.artwork.unreadable:
        tiny = image.resize((100, 75), Image.Resampling.BILINEAR)
        tiny = tiny.filter(ImageFilter.GaussianBlur(radius=3))
        blurred = tiny.resize(image.size, Image.Resampling.BILINEAR)
        image.close()
        tiny.close()
        image = blurred

    image.save(destination, format="PNG", optimize=False)
    width, height = image.size
    image.close()
    return PreparedImage(
        path=destination,
        media_type=ImageMediaType.PNG,
        width=width,
        height=height,
        byte_count=destination.stat().st_size,
    )
