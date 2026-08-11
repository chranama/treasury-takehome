import os
import tempfile
import warnings
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from pathlib import Path

from anyio import open_file, to_thread
from fastapi import UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError

from app.extraction.contract import ImageMediaType, PreparedImage

UPLOAD_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class ImageLimits:
    max_upload_bytes: int = 10 * 1024 * 1024
    max_pixels: int = 40_000_000
    max_side_pixels: int = 6_000

    def __post_init__(self) -> None:
        if self.max_upload_bytes <= 0:
            raise ValueError("maximum upload bytes must be positive")
        if self.max_pixels <= 0:
            raise ValueError("maximum pixels must be positive")
        if self.max_side_pixels <= 0:
            raise ValueError("maximum side length must be positive")


DEFAULT_IMAGE_LIMITS = ImageLimits()


class ImageIntakeErrorKind(StrEnum):
    EMPTY_FILE = "empty_file"
    UPLOAD_TOO_LARGE = "upload_too_large"
    UNSUPPORTED_FORMAT = "unsupported_format"
    CORRUPT_IMAGE = "corrupt_image"
    ANIMATED_IMAGE = "animated_image"
    DIMENSIONS_EXCEEDED = "dimensions_exceeded"
    DECOMPRESSION_BOMB = "decompression_bomb"


_SAFE_MESSAGES: dict[ImageIntakeErrorKind, str] = {
    ImageIntakeErrorKind.EMPTY_FILE: "Choose a non-empty JPEG, PNG, or WebP image.",
    ImageIntakeErrorKind.UPLOAD_TOO_LARGE: "Choose an image no larger than 10 MB.",
    ImageIntakeErrorKind.UNSUPPORTED_FORMAT: "Choose a JPEG, PNG, or WebP image.",
    ImageIntakeErrorKind.CORRUPT_IMAGE: "The image could not be decoded. Choose a valid image.",
    ImageIntakeErrorKind.ANIMATED_IMAGE: "Animated images are not supported.",
    ImageIntakeErrorKind.DIMENSIONS_EXCEEDED: (
        "Choose an image no larger than 40 megapixels or 6,000 pixels on either side."
    ),
    ImageIntakeErrorKind.DECOMPRESSION_BOMB: "The decoded image dimensions are unsafe.",
}


class ImageIntakeError(ValueError):
    """Safe image-validation failure that never includes uploaded content or filenames."""

    def __init__(self, kind: ImageIntakeErrorKind) -> None:
        self.kind = kind
        self.safe_message = _SAFE_MESSAGES[kind]
        super().__init__(self.safe_message)


def _private_temp_path(temp_dir: Path, *, suffix: str) -> Path:
    temp_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(prefix="review-", suffix=suffix, dir=temp_dir)
    os.close(descriptor)
    return Path(raw_path)


async def _stream_upload(
    upload: UploadFile,
    destination: Path,
    *,
    max_bytes: int,
) -> tuple[int, bytes]:
    byte_count = 0
    signature = bytearray()
    async with await open_file(destination, "wb") as output:
        while chunk := await upload.read(UPLOAD_CHUNK_BYTES):
            byte_count += len(chunk)
            if byte_count > max_bytes:
                raise ImageIntakeError(ImageIntakeErrorKind.UPLOAD_TOO_LARGE)
            if len(signature) < 16:
                signature.extend(chunk[: 16 - len(signature)])
            await output.write(chunk)

    if byte_count == 0:
        raise ImageIntakeError(ImageIntakeErrorKind.EMPTY_FILE)
    return byte_count, bytes(signature)


def _detect_media_type(signature: bytes) -> ImageMediaType:
    if signature.startswith(b"\xff\xd8\xff"):
        return ImageMediaType.JPEG
    if signature.startswith(b"\x89PNG\r\n\x1a\n"):
        return ImageMediaType.PNG
    if len(signature) >= 12 and signature[:4] == b"RIFF" and signature[8:12] == b"WEBP":
        return ImageMediaType.WEBP
    raise ImageIntakeError(ImageIntakeErrorKind.UNSUPPORTED_FORMAT)


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in {"LA", "PA", "RGBA"} or (
        image.mode == "P" and "transparency" in image.info
    )


def _normalize_image(
    source: Path,
    destination: Path,
    detected_media_type: ImageMediaType,
    limits: ImageLimits,
) -> PreparedImage:
    expected_format = {
        ImageMediaType.JPEG: "JPEG",
        ImageMediaType.PNG: "PNG",
        ImageMediaType.WEBP: "WEBP",
    }[detected_media_type]

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as image:
                if image.format != expected_format:
                    raise ImageIntakeError(ImageIntakeErrorKind.CORRUPT_IMAGE)
                if getattr(image, "n_frames", 1) != 1 or getattr(image, "is_animated", False):
                    raise ImageIntakeError(ImageIntakeErrorKind.ANIMATED_IMAGE)

                width, height = image.size
                if (
                    width <= 0
                    or height <= 0
                    or width > limits.max_side_pixels
                    or height > limits.max_side_pixels
                    or width * height > limits.max_pixels
                ):
                    raise ImageIntakeError(ImageIntakeErrorKind.DIMENSIONS_EXCEEDED)

                image.load()
                oriented: Image.Image | None = None
                converted: Image.Image | None = None
                clean: Image.Image | None = None
                try:
                    oriented = ImageOps.exif_transpose(image)
                    output_mode = "RGBA" if _has_alpha(oriented) else "RGB"
                    converted = oriented.convert(output_mode)
                    clean = Image.new(output_mode, converted.size)
                    clean.paste(converted)
                    clean.save(destination, format="PNG")
                    prepared_width, prepared_height = clean.size
                finally:
                    if clean is not None:
                        clean.close()
                    if converted is not None:
                        converted.close()
                    if oriented is not None and oriented is not image:
                        oriented.close()
    except ImageIntakeError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ImageIntakeError(ImageIntakeErrorKind.DECOMPRESSION_BOMB) from error
    except (OSError, SyntaxError, UnidentifiedImageError, ValueError) as error:
        raise ImageIntakeError(ImageIntakeErrorKind.CORRUPT_IMAGE) from error

    return PreparedImage(
        path=destination,
        media_type=ImageMediaType.PNG,
        width=prepared_width,
        height=prepared_height,
        byte_count=destination.stat().st_size,
    )


def _unlink_if_present(path: Path | None) -> None:
    if path is not None:
        path.unlink(missing_ok=True)


@asynccontextmanager
async def prepare_uploaded_image(
    upload: UploadFile,
    *,
    temp_dir: Path,
    limits: ImageLimits = DEFAULT_IMAGE_LIMITS,
) -> AsyncGenerator[PreparedImage, None]:
    """Validate one multipart upload and own its temporary files through use."""

    raw_path: Path | None = None
    prepared_path: Path | None = None
    try:
        raw_path = _private_temp_path(temp_dir, suffix=".upload")
        try:
            _, signature = await _stream_upload(
                upload,
                raw_path,
                max_bytes=limits.max_upload_bytes,
            )
        finally:
            await upload.close()

        detected_media_type = _detect_media_type(signature)
        prepared_path = _private_temp_path(temp_dir, suffix=".png")
        prepared = await to_thread.run_sync(
            partial(
                _normalize_image,
                raw_path,
                prepared_path,
                detected_media_type,
                limits,
            )
        )
        _unlink_if_present(raw_path)
        raw_path = None
        yield prepared
    finally:
        _unlink_if_present(raw_path)
        _unlink_if_present(prepared_path)
