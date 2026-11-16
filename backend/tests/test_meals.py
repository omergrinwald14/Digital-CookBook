"""Meals: the pure reshaping step between the database embed and the API.

Like the rest of this suite these are unit tests over pure logic — the
ownership checks in add_meal_recipe need real rows and are not covered here.
"""
from app.storage import _flatten_meal


def test_recipes_come_out_in_the_order_they_were_added():
    row = {
        "id": 1, "name": "Friday dinner",
        "meal_recipes": [
            {"added_at": "2026-11-16T10:05:00+00:00", "recipes": {"id": 7, "title": "Dessert"}},
            {"added_at": "2026-11-16T10:01:00+00:00", "recipes": {"id": 3, "title": "Soup"}},
        ],
    }
    out = _flatten_meal(row)
    assert [r["title"] for r in out["recipes"]] == ["Soup", "Dessert"]


def test_the_raw_embed_key_does_not_leak_to_the_client():
    out = _flatten_meal({"id": 1, "name": "x", "meal_recipes": []})
    assert "meal_recipes" not in out
    assert out["recipes"] == []


def test_a_meal_with_no_embed_at_all_is_just_empty():
    assert _flatten_meal({"id": 1, "name": "x"})["recipes"] == []
    assert _flatten_meal({"id": 1, "name": "x", "meal_recipes": None})["recipes"] == []


def test_a_join_row_without_its_recipe_is_skipped():
    row = {
        "id": 1, "name": "x",
        "meal_recipes": [
            {"added_at": "2026-11-16T10:00:00+00:00", "recipes": None},
            {"added_at": "2026-11-16T10:01:00+00:00", "recipes": {"id": 3}},
        ],
    }
    assert _flatten_meal(row)["recipes"] == [{"id": 3}]
