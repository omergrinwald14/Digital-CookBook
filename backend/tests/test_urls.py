"""URL normalization — the front door of the whole import pipeline.

Two separate jobs ride on these functions: Apify needs a shape it accepts
(/reels/ plural returned no caption), and dedupe needs ONE canonical string
per post, or the same reel saves twice under different-looking links.
"""
import pytest

from app.instagram import normalize_instagram_url
from app.tiktok import normalize_tiktok_url


@pytest.mark.parametrize("raw, expected", [
    # every shape Instagram hands out maps to the same canonical reel URL
    ("https://www.instagram.com/reel/ABC123/",
     "https://www.instagram.com/reel/ABC123/"),
    ("https://instagram.com/reel/ABC123",
     "https://www.instagram.com/reel/ABC123/"),
    # /reels/ (plural) is what the share sheet produces and what broke Apify
    ("https://www.instagram.com/reels/ABC123/",
     "https://www.instagram.com/reel/ABC123/"),
    # tracking junk must not create a second "different" post
    ("https://www.instagram.com/reel/ABC123/?igsh=MWxmN29kZnJpNzNoNw==",
     "https://www.instagram.com/reel/ABC123/"),
    ("https://www.instagram.com/reel/ABC123/?utm_source=ig_web_copy_link",
     "https://www.instagram.com/reel/ABC123/"),
    # posts and IGTV keep their own kind, they are not reels
    ("https://www.instagram.com/p/XYZ_789/", "https://www.instagram.com/p/XYZ_789/"),
    ("https://www.instagram.com/tv/XYZ-789/", "https://www.instagram.com/tv/XYZ-789/"),
])
def test_instagram_urls_canonicalize(raw, expected):
    assert normalize_instagram_url(raw) == expected


def test_instagram_variants_all_collapse_to_one_url():
    """The dedupe guarantee, stated directly."""
    variants = [
        "https://www.instagram.com/reel/ABC123/",
        "https://www.instagram.com/reels/ABC123/",
        "https://instagram.com/reel/ABC123",
        "https://www.instagram.com/reel/ABC123/?igsh=xyz",
    ]
    assert len({normalize_instagram_url(v) for v in variants}) == 1


@pytest.mark.parametrize("raw", [
    "", None, "not a url", "https://example.com/reel/ABC123/",
    "https://www.tiktok.com/@user/video/123",       # right shape, wrong site
    "https://www.instagram.com/someuser/",          # a profile, not a post
])
def test_instagram_rejects_non_posts(raw):
    with pytest.raises(ValueError):
        normalize_instagram_url(raw)


@pytest.mark.parametrize("raw, expected", [
    ("https://www.tiktok.com/@chef/video/7658754150695488775",
     "https://www.tiktok.com/@/video/7658754150695488775"),
    # the username is dropped on purpose: the numeric id identifies the post,
    # and short links resolve to an EMPTY username
    ("https://www.tiktok.com/@/video/7658754150695488775",
     "https://www.tiktok.com/@/video/7658754150695488775"),
    ("https://www.tiktok.com/@someone.else/photo/123456",
     "https://www.tiktok.com/@/photo/123456"),
    ("https://www.tiktok.com/@chef/video/7658754150695488775?is_from_webapp=1",
     "https://www.tiktok.com/@/video/7658754150695488775"),
])
def test_tiktok_urls_canonicalize(raw, expected):
    assert normalize_tiktok_url(raw) == expected


def test_tiktok_same_post_under_different_usernames_is_one_url():
    a = normalize_tiktok_url("https://www.tiktok.com/@chef/video/7658754150695488775")
    b = normalize_tiktok_url("https://www.tiktok.com/@/video/7658754150695488775")
    assert a == b


@pytest.mark.parametrize("raw", [
    "", None, "not a url", "https://www.instagram.com/reel/ABC123/",
])
def test_tiktok_rejects_non_posts(raw):
    with pytest.raises(ValueError):
        normalize_tiktok_url(raw)
