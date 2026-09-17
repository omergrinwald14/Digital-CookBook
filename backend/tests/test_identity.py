"""Identity normalization.

This is the seam every endpoint depends on. Before it normalized, recipes were
stored under the raw header while friends and shares were looked up
lowercased, so one mixed-case header split an account in two.
"""
import pytest

from app.main import DEFAULT_OWNER, current_user


@pytest.mark.parametrize("header", [
    "omer@example.com",
    "Omer@Example.com",
    "OMER@EXAMPLE.COM",
    "  omer@example.com  ",
    "\tOmer@EXAMPLE.com\n",
])
def test_every_spelling_resolves_to_one_identity(header):
    assert current_user(header) == "omer@example.com"


@pytest.mark.parametrize("header", ["", "   ", "\t\n"])
def test_blank_header_falls_back_to_the_default_owner(header):
    assert current_user(header) == DEFAULT_OWNER


def test_the_default_owner_is_itself_normalized():
    """Otherwise the fallback could not match rows the normalized path writes."""
    assert DEFAULT_OWNER == DEFAULT_OWNER.strip().lower()
