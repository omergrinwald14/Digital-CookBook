"""The parser's safety net.

The prompt tells Gemini to pick tags only from the allowed list, but a prompt
is a request, not a guarantee. These tests cover what happens when the model
ignores it — the app must never invent a tag the user does not own, and must
never 500 on malformed output.
"""
import pytest

from app import parser

ALLOWED = ["Pasta", "Soup", "Vegan"]


class _FakeModels:
    def __init__(self, text):
        self._text = text

    def generate_content(self, **kwargs):
        return type("Response", (), {"text": self._text})()


class _FakeClient:
    def __init__(self, text):
        self.models = _FakeModels(text)


@pytest.fixture
def gemini_returns(monkeypatch):
    """Point parse_recipe at a canned model response instead of the network."""
    def _set(text):
        monkeypatch.setattr(parser, "_client", lambda: _FakeClient(text))
        monkeypatch.setattr(parser, "GEMINI_API_KEY", "test-key")
    return _set


def test_a_normal_response_passes_through(gemini_returns):
    gemini_returns('{"title": "Cacio e Pepe", "ingredients": [], '
                   '"steps": ["Boil"], "tags": ["Pasta"]}')
    out = parser.parse_recipe("some caption", ALLOWED)
    assert out["title"] == "Cacio e Pepe"
    assert out["tags"] == ["Pasta"]


def test_invented_tags_are_dropped(gemini_returns):
    gemini_returns('{"title": "X", "tags": ["Pasta", "TotallyMadeUp"]}')
    assert parser.parse_recipe("caption", ALLOWED)["tags"] == ["Pasta"]


def test_no_more_than_two_tags_survive(gemini_returns):
    gemini_returns('{"title": "X", "tags": ["Pasta", "Soup", "Vegan"]}')
    assert parser.parse_recipe("caption", ALLOWED)["tags"] == ["Pasta", "Soup"]


@pytest.mark.parametrize("tags_json", ['"Pasta"', "null", "123", "{}"])
def test_a_tags_field_that_is_not_a_list_becomes_empty(gemini_returns, tags_json):
    gemini_returns('{"title": "X", "tags": %s}' % tags_json)
    assert parser.parse_recipe("caption", ALLOWED)["tags"] == []


@pytest.mark.parametrize("bad", ["", "not json at all", "[1, 2, 3]", "null"])
def test_unusable_output_degrades_instead_of_raising(gemini_returns, bad):
    """A bad response must look like "no recipe found", never a 500."""
    gemini_returns(bad)
    out = parser.parse_recipe("caption", ALLOWED)
    assert out == {"title": None, "ingredients": None, "steps": None, "tags": []}


def test_an_empty_caption_never_calls_the_model(monkeypatch):
    def explode():
        raise AssertionError("the model must not be called for an empty caption")
    monkeypatch.setattr(parser, "_client", explode)
    monkeypatch.setattr(parser, "GEMINI_API_KEY", "test-key")
    assert parser.parse_recipe("", ALLOWED)["tags"] == []
