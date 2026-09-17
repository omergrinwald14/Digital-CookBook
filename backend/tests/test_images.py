"""Thumbnail shrinking.

Contract: make the image smaller when that is possible, and NEVER raise.
Storing an oversized picture is a performance problem; failing an import over
one is a correctness problem, so every failure path returns the input intact.
"""
import io

from PIL import Image

from app.storage import THUMB_MAX_DIM, _shrink_image


def _jpeg(width, height, colour=(200, 120, 60)):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buf, "JPEG", quality=95)
    return buf.getvalue()


def test_a_large_photo_is_scaled_within_the_cap():
    data, ctype = _shrink_image(_jpeg(3000, 4000), "image/jpeg")
    assert ctype == "image/jpeg"
    assert max(Image.open(io.BytesIO(data)).size) == THUMB_MAX_DIM


def test_aspect_ratio_survives():
    data, _ = _shrink_image(_jpeg(2000, 1000), "image/jpeg")
    w, h = Image.open(io.BytesIO(data)).size
    assert w / h == 2.0


def test_the_result_is_actually_smaller():
    original = _jpeg(3000, 4000)
    shrunk, _ = _shrink_image(original, "image/jpeg")
    assert len(shrunk) < len(original)


def test_a_transparent_png_becomes_a_jpeg_rather_than_failing():
    """JPEG has no alpha channel — the RGBA mode must be converted, not crash."""
    buf = io.BytesIO()
    Image.new("RGBA", (1500, 1500), (10, 20, 30, 128)).save(buf, "PNG")
    data, ctype = _shrink_image(buf.getvalue(), "image/png")
    assert ctype == "image/jpeg"
    assert Image.open(io.BytesIO(data)).mode == "RGB"


def test_garbage_bytes_are_returned_untouched():
    junk = b"this is definitely not an image"
    assert _shrink_image(junk, "image/jpeg") == (junk, "image/jpeg")


def test_empty_input_is_returned_untouched():
    assert _shrink_image(b"", "image/jpeg") == (b"", "image/jpeg")


def test_an_already_tiny_image_is_never_made_bigger():
    original = _jpeg(40, 40)
    shrunk, _ = _shrink_image(original, "image/jpeg")
    assert len(shrunk) <= len(original)
