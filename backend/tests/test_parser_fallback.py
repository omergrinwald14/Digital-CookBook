"""parse_recipe falls back to the lighter model when the main one is out of quota."""

import pytest
from google.genai import errors

from app import parser


class _Resp:
    text = '{"title": "Soup", "ingredients": [], "steps": [], "tags": []}'


class _FakeModels:
    def __init__(self, fail_code):
        self.fail_code = fail_code
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append(model)
        if model == parser.MODEL and self.fail_code:
            raise errors.ClientError(self.fail_code, {"error": {"message": "x"}})
        return _Resp()


def _use_fake(monkeypatch, fail_code):
    fake = _FakeModels(fail_code)
    monkeypatch.setattr(parser, "_client", lambda: type("C", (), {"models": fake})())
    monkeypatch.setattr(parser, "GEMINI_API_KEY", "test")
    return fake


def test_quota_error_retries_on_fallback_model(monkeypatch):
    fake = _use_fake(monkeypatch, 429)
    assert parser.parse_recipe("soup caption", [])["title"] == "Soup"
    assert fake.calls == [parser.MODEL, parser.FALLBACK_MODEL]


def test_other_client_errors_are_not_retried(monkeypatch):
    fake = _use_fake(monkeypatch, 400)
    with pytest.raises(errors.ClientError):
        parser.parse_recipe("soup caption", [])
    assert fake.calls == [parser.MODEL]


def test_no_error_uses_main_model_only(monkeypatch):
    fake = _use_fake(monkeypatch, None)
    parser.parse_recipe("soup caption", [])
    assert fake.calls == [parser.MODEL]
