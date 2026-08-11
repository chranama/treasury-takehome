import asyncio
import logging
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile
from PIL import Image, PngImagePlugin

from app.extraction import (
    ExtractionError,
    FakeExtractionAdapter,
    FakeExtractionFailure,
    ImageMediaType,
)
from app.storage import (
    DEFAULT_IMAGE_LIMITS,
    ImageIntakeError,
    ImageIntakeErrorKind,
    ImageLimits,
    prepare_uploaded_image,
)


def encoded_image(
    image_format: str,
    *,
    size: tuple[int, int] = (40, 24),
    exif: Image.Exif | None = None,
    pnginfo: PngImagePlugin.PngInfo | None = None,
) -> bytes:
    image = Image.new("RGB", size, color=(40, 90, 140))
    output = BytesIO()
    save_options: dict[str, object] = {}
    if exif is not None:
        save_options["exif"] = exif
    if pnginfo is not None:
        save_options["pnginfo"] = pnginfo
    image.save(output, format=image_format, **save_options)
    image.close()
    return output.getvalue()


def animated_png() -> bytes:
    first = Image.new("RGB", (20, 20), color="red")
    second = Image.new("RGB", (20, 20), color="blue")
    output = BytesIO()
    first.save(
        output,
        format="PNG",
        save_all=True,
        append_images=[second],
        duration=100,
        loop=0,
    )
    first.close()
    second.close()
    return output.getvalue()


def make_upload(
    content: bytes,
    *,
    filename: str = "label.bin",
    content_type: str = "application/octet-stream",
) -> UploadFile:
    return UploadFile(
        file=BytesIO(content),
        filename=filename,
        headers={"content-type": content_type},
        size=len(content),
    )


def assert_temp_dir_empty(temp_dir: Path) -> None:
    assert temp_dir.is_dir()
    assert list(temp_dir.iterdir()) == []


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("max_upload_bytes", "maximum upload bytes must be positive"),
        ("max_pixels", "maximum pixels must be positive"),
        ("max_side_pixels", "maximum side length must be positive"),
    ],
)
def test_image_limits_require_positive_values(field: str, message: str) -> None:
    values = {
        "max_upload_bytes": 1,
        "max_pixels": 1,
        "max_side_pixels": 1,
    }
    values[field] = 0

    with pytest.raises(ValueError, match=message):
        ImageLimits(**values)


@pytest.mark.parametrize(
    ("image_format", "filename", "content_type"),
    [
        ("JPEG", "label.png", "image/png"),
        ("PNG", "label.pdf", "application/pdf"),
        ("WEBP", "label.txt", "text/plain"),
    ],
)
def test_valid_formats_are_identified_from_bytes_and_normalized_without_metadata(
    image_format: str,
    filename: str,
    content_type: str,
    tmp_path: Path,
) -> None:
    temp_dir = tmp_path / "uploads"
    upload = make_upload(
        encoded_image(image_format),
        filename=filename,
        content_type=content_type,
    )
    prepared_path: Path | None = None

    async def run() -> None:
        nonlocal prepared_path
        async with prepare_uploaded_image(upload, temp_dir=temp_dir) as prepared:
            prepared_path = prepared.path
            assert prepared.media_type == ImageMediaType.PNG
            assert prepared.width == 40
            assert prepared.height == 24
            assert prepared.byte_count == prepared.path.stat().st_size
            assert prepared.path.is_file()
            assert prepared.path.name != filename
            assert list(temp_dir.iterdir()) == [prepared.path]
            with Image.open(prepared.path) as normalized:
                normalized.load()
                assert normalized.format == "PNG"
                assert normalized.info == {}

    asyncio.run(run())

    assert prepared_path is not None
    assert not prepared_path.exists()
    assert upload.file.closed
    assert_temp_dir_empty(temp_dir)


def test_exif_orientation_is_applied_and_metadata_removed(tmp_path: Path) -> None:
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "private-description"
    upload = make_upload(encoded_image("JPEG", size=(40, 20), exif=exif))
    temp_dir = tmp_path / "uploads"

    async def run() -> None:
        async with prepare_uploaded_image(upload, temp_dir=temp_dir) as prepared:
            assert (prepared.width, prepared.height) == (20, 40)
            with Image.open(prepared.path) as normalized:
                normalized.load()
                assert normalized.getexif() == {}
                assert "private-description" not in repr(normalized.info)

    asyncio.run(run())
    assert_temp_dir_empty(temp_dir)


def test_png_text_metadata_is_removed(tmp_path: Path) -> None:
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Comment", "private-comment")
    upload = make_upload(encoded_image("PNG", pnginfo=metadata))
    temp_dir = tmp_path / "uploads"

    async def run() -> None:
        async with prepare_uploaded_image(upload, temp_dir=temp_dir) as prepared:
            with Image.open(prepared.path) as normalized:
                normalized.load()
                assert "Comment" not in normalized.info
                assert "private-comment" not in repr(normalized.info)

    asyncio.run(run())
    assert_temp_dir_empty(temp_dir)


def test_png_transparency_is_preserved(tmp_path: Path) -> None:
    source = Image.new("RGBA", (10, 10), color=(20, 40, 60, 80))
    output = BytesIO()
    source.save(output, format="PNG")
    source.close()
    upload = make_upload(output.getvalue())
    temp_dir = tmp_path / "uploads"

    async def run() -> None:
        async with prepare_uploaded_image(upload, temp_dir=temp_dir) as prepared:
            with Image.open(prepared.path) as normalized:
                normalized.load()
                assert normalized.mode == "RGBA"
                assert normalized.getpixel((0, 0)) == (20, 40, 60, 80)

    asyncio.run(run())
    assert_temp_dir_empty(temp_dir)


@pytest.mark.parametrize(
    ("content", "limits", "expected_kind"),
    [
        (b"", DEFAULT_IMAGE_LIMITS, ImageIntakeErrorKind.EMPTY_FILE),
        (
            b"\x89PNG\r\n\x1a\n" + b"x" * 200,
            ImageLimits(max_upload_bytes=100),
            ImageIntakeErrorKind.UPLOAD_TOO_LARGE,
        ),
        (b"%PDF-1.7\n", DEFAULT_IMAGE_LIMITS, ImageIntakeErrorKind.UNSUPPORTED_FORMAT),
        (
            b"\x89PNG\r\n\x1a\ncorrupt",
            DEFAULT_IMAGE_LIMITS,
            ImageIntakeErrorKind.CORRUPT_IMAGE,
        ),
        (animated_png(), DEFAULT_IMAGE_LIMITS, ImageIntakeErrorKind.ANIMATED_IMAGE),
        (
            encoded_image("PNG", size=(21, 10)),
            ImageLimits(max_side_pixels=20),
            ImageIntakeErrorKind.DIMENSIONS_EXCEEDED,
        ),
        (
            encoded_image("PNG", size=(11, 11)),
            ImageLimits(max_pixels=120, max_side_pixels=20),
            ImageIntakeErrorKind.DIMENSIONS_EXCEEDED,
        ),
    ],
)
def test_invalid_images_are_rejected_before_consumer_runs_and_cleaned_up(
    content: bytes,
    limits: ImageLimits,
    expected_kind: ImageIntakeErrorKind,
    tmp_path: Path,
) -> None:
    upload = make_upload(content)
    temp_dir = tmp_path / "uploads"
    consumer_called = False

    async def run() -> None:
        nonlocal consumer_called
        with pytest.raises(ImageIntakeError) as caught:
            async with prepare_uploaded_image(upload, temp_dir=temp_dir, limits=limits):
                consumer_called = True
        assert caught.value.kind == expected_kind
        assert caught.value.safe_message == str(caught.value)

    asyncio.run(run())

    assert consumer_called is False
    assert upload.file.closed
    assert_temp_dir_empty(temp_dir)


def test_decompression_bomb_warning_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = encoded_image("PNG", size=(9, 9))
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 50)
    upload = make_upload(content)
    temp_dir = tmp_path / "uploads"

    async def run() -> None:
        with pytest.raises(ImageIntakeError) as caught:
            async with prepare_uploaded_image(upload, temp_dir=temp_dir):
                pass
        assert caught.value.kind == ImageIntakeErrorKind.DECOMPRESSION_BOMB

    asyncio.run(run())
    assert_temp_dir_empty(temp_dir)


@pytest.mark.parametrize("exit_path", ["success", "timeout", "adapter_error", "application_error"])
def test_prepared_image_is_deleted_for_every_consumer_exit_path(
    exit_path: str,
    tmp_path: Path,
) -> None:
    upload = make_upload(encoded_image("PNG"))
    temp_dir = tmp_path / "uploads"
    prepared_path: Path | None = None

    async def run() -> None:
        nonlocal prepared_path
        async with prepare_uploaded_image(upload, temp_dir=temp_dir) as prepared:
            prepared_path = prepared.path
            if exit_path == "success":
                await FakeExtractionAdapter().extract(prepared)
            elif exit_path == "timeout":
                raise TimeoutError("test timeout")
            elif exit_path == "adapter_error":
                await FakeExtractionAdapter(failure=FakeExtractionFailure.INTERNAL_FAILURE).extract(
                    prepared
                )
            else:
                raise RuntimeError("test application failure")

    expected_error: type[BaseException] | None = {
        "success": None,
        "timeout": TimeoutError,
        "adapter_error": ExtractionError,
        "application_error": RuntimeError,
    }[exit_path]
    if expected_error is None:
        asyncio.run(run())
    else:
        with pytest.raises(expected_error):
            asyncio.run(run())

    assert prepared_path is not None
    assert not prepared_path.exists()
    assert_temp_dir_empty(temp_dir)


def test_uploaded_content_and_filename_are_not_logged(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_filename = "private-application-123.png"
    secret_content = b"\x89PNG\r\n\x1a\nPRIVATE-LABEL-CONTENT"
    upload = make_upload(secret_content, filename=secret_filename)
    temp_dir = tmp_path / "uploads"
    caplog.set_level(logging.DEBUG)

    async def run() -> None:
        with pytest.raises(ImageIntakeError):
            async with prepare_uploaded_image(upload, temp_dir=temp_dir):
                pass

    asyncio.run(run())

    assert secret_filename not in caplog.text
    assert "PRIVATE-LABEL-CONTENT" not in caplog.text
    assert_temp_dir_empty(temp_dir)
