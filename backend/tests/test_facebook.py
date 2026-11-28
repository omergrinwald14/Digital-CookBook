"""facebook.fetch_caption — mapping Apify's reel reply onto our contract.

The fake item below is a trimmed copy of a REAL facebook-posts-scraper reply
(spike, 2026-11-28): the field names are deeply nested and undocumented, so
this pins down exactly which paths we read. No network, no Apify credit.
"""
import pytest
import requests

from app import facebook

URL = "https://www.facebook.com/reel/1541791004655979/"

REAL_SHAPE = {
    "facebookUrl": URL,
    "message": {"text": "Mixing is just as important as ingredients for brownies #baking"},
    "short_form_video_context": {
        "video": {
            "first_frame_thumbnail": "https://scontent.xx.fbcdn.net/first.jpg",
            "seo_llm_content": {"summary": "Butter and sugar are creamed, eggs added."},
        },
        "video_owner": {"name": "benjamin the baker"},
        "playback_video": {"thumbnailImage": {"uri": "https://scontent.xx.fbcdn.net/thumb.jpg"}},
    },
}


class _FakeResponse:
    def __init__(self, items):
        self._items = items

    def raise_for_status(self):
        pass

    def json(self):
        return self._items


@pytest.fixture
def apify(monkeypatch):
    """Point fetch_caption at a canned Apify reply instead of the network."""
    monkeypatch.setattr(facebook, "APIFY_TOKEN", "test-token")

    def answer_with(items):
        monkeypatch.setattr(facebook.requests, "post", lambda *a, **k: _FakeResponse(items))
    return answer_with


def test_reel_maps_onto_the_shared_contract(apify):
    apify([REAL_SHAPE])
    d = facebook.fetch_caption(URL)
    assert d["fetch_failed"] is False
    assert d["title"] == "benjamin the baker"
    assert d["thumbnail"] == "https://scontent.xx.fbcdn.net/thumb.jpg"
    assert d["source_url"] == URL
    # caption first, then Facebook's summary, labelled so Gemini can tell them apart
    assert d["caption"] == (
        "Mixing is just as important as ingredients for brownies #baking\n\n"
        "Video summary: Butter and sugar are creamed, eggs added."
    )


def test_missing_summary_and_thumbnail_image_fall_back(apify):
    item = {
        "message": {"text": "Just the caption"},
        "short_form_video_context": {
            "video": {"first_frame_thumbnail": "https://scontent.xx.fbcdn.net/first.jpg"},
        },
    }
    apify([item])
    d = facebook.fetch_caption(URL)
    assert d["caption"] == "Just the caption"
    assert d["thumbnail"] == "https://scontent.xx.fbcdn.net/first.jpg"
    assert d["title"] is None


@pytest.mark.parametrize("items", [[], [{"error": "not found"}]])
def test_private_or_removed_reel_saves_empty_not_failed(apify, items):
    """Apify answered fine, the reel just has nothing: null fields, no retry."""
    apify(items)
    d = facebook.fetch_caption(URL)
    assert d["fetch_failed"] is False
    assert d["caption"] is None


def test_apify_unreachable_is_flagged_for_retry(monkeypatch):
    """Outage/timeout -> fetch_failed, so /import answers 502 instead of saving a blank."""
    monkeypatch.setattr(facebook, "APIFY_TOKEN", "test-token")

    def boom(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(facebook.requests, "post", boom)
    assert facebook.fetch_caption(URL)["fetch_failed"] is True
