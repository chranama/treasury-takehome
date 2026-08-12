import hashlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Self

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.extraction import ImageMediaType, PreparedImage
from evals.manifest import EvaluationCaseV2

RENDERER_ID = "synthetic-label"
RENDERER_VERSION = "2"
FONT_IDENTITY = "pillow-embedded-aileron-regular"
CANVAS_COLOR = "#eee7d8"
PANEL_COLOR = "#fffdf7"
INK_COLOR = "#172b3a"
ACCENT_COLOR = "#9a6f2d"
MAX_RENDER_SIDE = 6_000
MAX_RENDER_PIXELS = 40_000_000


class RendererModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LayoutKind(StrEnum):
    SINGLE_PANEL = "single_panel"
    FRONT_BACK_COMPOSITE = "front_back_composite"


class FontWeight(StrEnum):
    REGULAR = "regular"
    BOLD = "bold"


class CanvasSpec(RendererModel):
    width: Annotated[int, Field(ge=800, le=MAX_RENDER_SIDE)] = 1_600
    height: Annotated[int, Field(ge=600, le=MAX_RENDER_SIDE)] = 1_200

    @model_validator(mode="after")
    def require_bounded_pixel_count(self) -> Self:
        if self.width * self.height > MAX_RENDER_PIXELS:
            raise ValueError(f"canvas cannot exceed {MAX_RENDER_PIXELS:,} pixels")
        return self


class TypographySpec(RendererModel):
    brand_size: Annotated[int, Field(ge=28, le=140)] = 72
    class_type_size: Annotated[int, Field(ge=20, le=90)] = 36
    detail_size: Annotated[int, Field(ge=18, le=80)] = 32
    warning_heading_size: Annotated[int, Field(ge=14, le=64)] = 26
    warning_body_size: Annotated[int, Field(ge=12, le=56)] = 21
    warning_line_spacing: Annotated[int, Field(ge=0, le=24)] = 6
    brand_weight: FontWeight = FontWeight.BOLD
    warning_heading_weight: FontWeight = FontWeight.BOLD
    warning_body_weight: FontWeight = FontWeight.REGULAR


class LayoutSpec(RendererModel):
    kind: LayoutKind = LayoutKind.FRONT_BACK_COMPOSITE
    outer_margin: Annotated[int, Field(ge=24, le=180)] = 64
    panel_gap: Annotated[int, Field(ge=16, le=160)] = 48
    back_panel_rotation_degrees: Annotated[float, Field(ge=-12, le=12)] = 0


class NormalizedBox(RendererModel):
    left: Annotated[float, Field(ge=0, le=1)]
    top: Annotated[float, Field(ge=0, le=1)]
    right: Annotated[float, Field(ge=0, le=1)]
    bottom: Annotated[float, Field(ge=0, le=1)]

    @model_validator(mode="after")
    def require_positive_area(self) -> Self:
        if self.left >= self.right or self.top >= self.bottom:
            raise ValueError("normalized box must have positive width and height")
        return self


class CropSpec(RendererModel):
    left: Annotated[float, Field(ge=0, le=0.3)] = 0
    top: Annotated[float, Field(ge=0, le=0.3)] = 0
    right: Annotated[float, Field(ge=0, le=0.3)] = 0
    bottom: Annotated[float, Field(ge=0, le=0.3)] = 0

    @model_validator(mode="after")
    def preserve_a_usable_image(self) -> Self:
        if self.left + self.right >= 0.5 or self.top + self.bottom >= 0.5:
            raise ValueError("crop must preserve more than half of each image dimension")
        return self


class DegradationSpec(RendererModel):
    contrast: Annotated[float, Field(ge=0.2, le=1.5)] = 1
    glare_box: NormalizedBox | None = None
    glare_opacity: Annotated[float, Field(ge=0, le=0.9)] = 0
    obstruction_box: NormalizedBox | None = None
    blur_radius: Annotated[float, Field(ge=0, le=12)] = 0
    rotation_degrees: Annotated[float, Field(ge=-20, le=20)] = 0
    crop: CropSpec = Field(default_factory=CropSpec)

    @model_validator(mode="after")
    def require_complete_glare_control(self) -> Self:
        if (self.glare_box is None) != (self.glare_opacity == 0):
            raise ValueError("glare requires both a box and a positive opacity")
        return self


class ArtworkSpec(RendererModel):
    canvas: CanvasSpec = Field(default_factory=CanvasSpec)
    layout: LayoutSpec = Field(default_factory=LayoutSpec)
    typography: TypographySpec = Field(default_factory=TypographySpec)
    brand_names: Annotated[list[str], Field(min_length=1, max_length=3)]
    class_types: Annotated[list[str], Field(min_length=1, max_length=3)]
    alcohol_contents: Annotated[list[str], Field(min_length=1, max_length=3)]
    net_contents: Annotated[list[str], Field(min_length=1, max_length=3)]
    government_warning: str | None
    degradation: DegradationSpec = Field(default_factory=DegradationSpec)

    @field_validator("brand_names", "class_types", "alcohol_contents", "net_contents")
    @classmethod
    def require_unique_nonblank_text(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("artwork text values must not be blank")
        if len(values) != len(set(values)):
            raise ValueError("artwork text values must be unique")
        return values

    @field_validator("government_warning")
    @classmethod
    def require_warning_heading_separator(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or ":" not in value):
            raise ValueError("Government Warning must be null or contain a heading separator")
        return value


@dataclass(frozen=True, slots=True)
class RenderedArtifact:
    image: PreparedImage
    sha256: str
    renderer_id: str = RENDERER_ID
    renderer_version: str = RENDERER_VERSION
    font_identity: str = FONT_IDENTITY


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def _stroke_width(weight: FontWeight, size: int) -> int:
    return max(1, round(size / 36)) if weight == FontWeight.BOLD else 0


def _draw_text(
    draw: ImageDraw.ImageDraw,
    position: tuple[int, int],
    text: str,
    *,
    size: int,
    weight: FontWeight = FontWeight.REGULAR,
    fill: str = INK_COLOR,
) -> None:
    stroke_width = _stroke_width(weight, size)
    draw.text(
        position,
        text,
        font=_font(size),
        fill=fill,
        stroke_width=stroke_width,
        stroke_fill=fill if stroke_width else None,
    )


def _wrapped_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        box = draw.textbbox((0, 0), candidate, font=font)
        if box[2] - box[0] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _draw_wrapped_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    position: tuple[int, int],
    max_width: int,
    size: int,
    weight: FontWeight = FontWeight.REGULAR,
    fill: str = INK_COLOR,
    spacing: int = 6,
) -> int:
    font = _font(size)
    stroke_width = _stroke_width(weight, size)
    x, y = position
    for line in _wrapped_lines(draw, text, font=font, max_width=max_width):
        draw.text(
            (x, y),
            line,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=fill if stroke_width else None,
        )
        box = draw.textbbox((x, y), line, font=font, stroke_width=stroke_width)
        y += box[3] - box[1] + spacing
    return y


def _new_panel(size: tuple[int, int]) -> Image.Image:
    panel = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(panel)
    draw.rounded_rectangle(
        (4, 4, size[0] - 5, size[1] - 5),
        radius=max(16, min(size) // 24),
        fill=PANEL_COLOR,
        outline=INK_COLOR,
        width=max(3, min(size) // 260),
    )
    return panel


def _draw_front_panel(panel: Image.Image, artwork: ArtworkSpec) -> None:
    draw = ImageDraw.Draw(panel)
    typography = artwork.typography
    margin = max(36, panel.width // 16)
    available_width = panel.width - 2 * margin
    y = max(44, panel.height // 16)

    for index, brand in enumerate(artwork.brand_names):
        if index:
            _draw_text(
                draw,
                (margin, y),
                "OR",
                size=max(18, typography.class_type_size - 8),
                fill=ACCENT_COLOR,
            )
            y += max(30, typography.class_type_size)
        y = _draw_wrapped_text(
            draw,
            brand,
            position=(margin, y),
            max_width=available_width,
            size=typography.brand_size,
            weight=typography.brand_weight,
            spacing=4,
        )
        y += 12

    draw.line((margin, y, panel.width - margin, y), fill=ACCENT_COLOR, width=4)
    y += 28
    for class_type in artwork.class_types:
        y = _draw_wrapped_text(
            draw,
            class_type,
            position=(margin, y),
            max_width=available_width,
            size=typography.class_type_size,
            spacing=6,
        )
        y += 10

    y += 18
    for alcohol in artwork.alcohol_contents:
        _draw_text(draw, (margin, y), alcohol, size=typography.detail_size)
        y += typography.detail_size + 16

    y = max(y + 20, panel.height - margin - typography.detail_size - 14)
    quantity_width = available_width // len(artwork.net_contents)
    for index, quantity in enumerate(artwork.net_contents):
        _draw_text(
            draw,
            (margin + index * quantity_width, y),
            quantity,
            size=typography.detail_size,
            weight=FontWeight.BOLD,
        )


def _draw_warning(panel: Image.Image, artwork: ArtworkSpec, *, top_fraction: float = 0.1) -> None:
    if artwork.government_warning is None:
        return
    draw = ImageDraw.Draw(panel)
    typography = artwork.typography
    margin = max(36, panel.width // 15)
    max_width = panel.width - 2 * margin
    y = max(40, round(panel.height * top_fraction))
    heading, body = artwork.government_warning.split(":", 1)
    _draw_text(
        draw,
        (margin, y),
        f"{heading}:",
        size=typography.warning_heading_size,
        weight=typography.warning_heading_weight,
        fill="#111111",
    )
    y += typography.warning_heading_size + 18
    _draw_wrapped_text(
        draw,
        body.strip(),
        position=(margin, y),
        max_width=max_width,
        size=typography.warning_body_size,
        weight=typography.warning_body_weight,
        fill="#111111",
        spacing=typography.warning_line_spacing,
    )


def _draw_single_panel(panel: Image.Image, artwork: ArtworkSpec) -> None:
    _draw_front_panel(panel, artwork)
    _draw_warning(panel, artwork, top_fraction=0.55)


def _place_panel(
    canvas: Image.Image,
    panel: Image.Image,
    *,
    position: tuple[int, int],
    rotation_degrees: float = 0,
) -> None:
    placed = panel
    destination = position
    if rotation_degrees:
        placed = panel.rotate(
            rotation_degrees,
            resample=Image.Resampling.BICUBIC,
            expand=True,
            fillcolor=(0, 0, 0, 0),
        )
        placed.thumbnail(panel.size, Image.Resampling.LANCZOS)
        destination = (
            position[0] + (panel.width - placed.width) // 2,
            position[1] + (panel.height - placed.height) // 2,
        )
    canvas.alpha_composite(placed, dest=destination)
    if placed is not panel:
        placed.close()


def _render_layout(artwork: ArtworkSpec) -> Image.Image:
    canvas = Image.new(
        "RGBA",
        (artwork.canvas.width, artwork.canvas.height),
        CANVAS_COLOR,
    )
    margin = artwork.layout.outer_margin
    available_width = canvas.width - 2 * margin
    available_height = canvas.height - 2 * margin

    if artwork.layout.kind == LayoutKind.SINGLE_PANEL:
        panel = _new_panel((available_width, available_height))
        _draw_single_panel(panel, artwork)
        _place_panel(canvas, panel, position=(margin, margin))
        panel.close()
        return canvas

    gap = artwork.layout.panel_gap
    front_width = round((available_width - gap) * 0.43)
    back_width = available_width - gap - front_width
    front = _new_panel((front_width, available_height))
    back = _new_panel((back_width, available_height))
    _draw_front_panel(front, artwork)
    _draw_warning(back, artwork)
    _place_panel(canvas, front, position=(margin, margin))
    _place_panel(
        canvas,
        back,
        position=(margin + front_width + gap, margin),
        rotation_degrees=artwork.layout.back_panel_rotation_degrees,
    )
    front.close()
    back.close()
    return canvas


def _pixel_box(image: Image.Image, box: NormalizedBox) -> tuple[int, int, int, int]:
    return (
        round(box.left * image.width),
        round(box.top * image.height),
        round(box.right * image.width),
        round(box.bottom * image.height),
    )


def _apply_degradation(image: Image.Image, degradation: DegradationSpec) -> Image.Image:
    current = image.convert("RGB")
    image.close()

    if degradation.contrast != 1:
        changed = ImageEnhance.Contrast(current).enhance(degradation.contrast)
        current.close()
        current = changed

    if degradation.glare_box is not None:
        overlay = Image.new("RGBA", current.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        draw.rounded_rectangle(
            _pixel_box(current, degradation.glare_box),
            radius=max(8, min(current.size) // 40),
            fill=(255, 255, 255, round(255 * degradation.glare_opacity)),
        )
        base = current.convert("RGBA")
        changed = Image.alpha_composite(base, overlay).convert("RGB")
        base.close()
        overlay.close()
        current.close()
        current = changed

    if degradation.obstruction_box is not None:
        draw = ImageDraw.Draw(current)
        draw.rectangle(_pixel_box(current, degradation.obstruction_box), fill="#4b4b4b")

    if degradation.blur_radius:
        changed = current.filter(ImageFilter.GaussianBlur(degradation.blur_radius))
        current.close()
        current = changed

    if degradation.rotation_degrees:
        changed = current.rotate(
            degradation.rotation_degrees,
            resample=Image.Resampling.BICUBIC,
            expand=False,
            fillcolor=CANVAS_COLOR,
        )
        current.close()
        current = changed

    crop = degradation.crop
    if any((crop.left, crop.top, crop.right, crop.bottom)):
        left = round(crop.left * current.width)
        top = round(crop.top * current.height)
        right = current.width - round(crop.right * current.width)
        bottom = current.height - round(crop.bottom * current.height)
        changed = current.crop((left, top, right, bottom))
        current.close()
        current = changed
    return current


def render_artwork(artwork: ArtworkSpec, destination: Path) -> RenderedArtifact:
    destination.parent.mkdir(parents=True, exist_ok=True)
    rendered = _apply_degradation(_render_layout(artwork), artwork.degradation)
    rendered.save(destination, format="PNG", optimize=False, compress_level=9)
    width, height = rendered.size
    rendered.close()
    content = destination.read_bytes()
    prepared = PreparedImage(
        path=destination,
        media_type=ImageMediaType.PNG,
        width=width,
        height=height,
        byte_count=len(content),
    )
    return RenderedArtifact(image=prepared, sha256=hashlib.sha256(content).hexdigest())


def _require_matching_ground_truth(case: EvaluationCaseV2, artwork: ArtworkSpec) -> None:
    expected = case.expected_visible_text
    if expected is None:
        raise ValueError("rendered hosted cases require expected visible text")
    source_text = {
        "brand_name": artwork.brand_names,
        "class_type": artwork.class_types,
        "alcohol_content": artwork.alcohol_contents,
        "net_contents": artwork.net_contents,
        "government_warning": artwork.government_warning,
        "warning_heading": (
            artwork.government_warning.split(":", 1)[0]
            if artwork.government_warning is not None
            else None
        ),
    }
    expected_text = expected.model_dump()
    if source_text != expected_text:
        raise ValueError(
            "artwork source text must equal independently reviewed visible-text ground truth"
        )


def render_case(case: EvaluationCaseV2, destination: Path) -> RenderedArtifact:
    renderer = case.renderer
    if renderer is None:
        raise ValueError("case does not declare a renderer")
    identity = (renderer.id, renderer.version, renderer.font_identity)
    expected_identity = (RENDERER_ID, RENDERER_VERSION, FONT_IDENTITY)
    if identity != expected_identity:
        raise ValueError("case renderer identity is not supported by this renderer")
    if renderer.seed is not None:
        raise ValueError("synthetic-label v2 has no random effects; renderer seed must be null")

    artwork = ArtworkSpec.model_validate(case.artwork)
    _require_matching_ground_truth(case, artwork)
    if len(case.artifacts) != 1 or case.artifacts[0].media_type != ImageMediaType.PNG:
        raise ValueError("synthetic-label cases require exactly one PNG artifact")
    if destination.name != case.artifacts[0].filename:
        raise ValueError("render destination must use the manifest artifact filename")

    rendered = render_artwork(artwork, destination)
    if rendered.sha256 != case.artifacts[0].sha256:
        destination.unlink(missing_ok=True)
        raise ValueError("rendered artifact hash does not match the manifest")
    return rendered
