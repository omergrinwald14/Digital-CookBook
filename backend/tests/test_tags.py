"""Tag-name resolution, against a fake Supabase client.

_tag_ids replaced a per-name loop with one batched query. The loop was correct,
just wasteful — so the risk in that change was never "does it crash", it was
"does it still quietly drop the same things". These pin that behaviour down,
and count the queries so the batching cannot silently regress.
"""
import pytest

from app import storage

TAGS = [
    {"id": 1, "name": "Pasta",  "owner": "omer@x.com"},
    {"id": 2, "name": "Soup",   "owner": "omer@x.com"},
    {"id": 3, "name": "Vegan",  "owner": "omer@x.com"},
    {"id": 9, "name": "Pasta",  "owner": "hila@x.com"},   # same name, other owner
]


class _Query:
    """Records filters, then answers from the TAGS fixture on execute()."""

    def __init__(self, recorder):
        self.recorder = recorder
        self.owner = None
        self.names = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        if col == "owner":
            self.owner = val
        return self

    def in_(self, col, vals):
        if col == "name":
            self.names = list(vals)
        return self

    def execute(self):
        self.recorder.append({"owner": self.owner, "names": self.names})
        rows = [t for t in TAGS
                if t["owner"] == self.owner and t["name"] in (self.names or [])]
        return type("Result", (), {"data": [dict(r) for r in rows]})()


class _FakeClient:
    def __init__(self):
        self.queries = []

    def table(self, _name):
        return _Query(self.queries)


@pytest.fixture
def client():
    return _FakeClient()


def test_resolves_several_names_in_a_single_query(client):
    got = storage._tag_ids(client, ["Pasta", "Soup", "Vegan"], "omer@x.com")
    assert got == {"Pasta": 1, "Soup": 2, "Vegan": 3}
    assert len(client.queries) == 1, "batching regressed to one query per name"


def test_unknown_names_are_simply_absent(client):
    got = storage._tag_ids(client, ["Pasta", "NoSuchTag"], "omer@x.com")
    assert got == {"Pasta": 1}


@pytest.mark.parametrize("reserved", ["Untagged", "Unknown"])
def test_reserved_names_never_resolve(client, reserved):
    """They mean "no tag" to the frontend, so they must not map to a row."""
    assert storage._tag_ids(client, [reserved], "omer@x.com") == {}


def test_repeats_are_asked_for_once(client):
    storage._tag_ids(client, ["Pasta", "Pasta", "Pasta"], "omer@x.com")
    assert client.queries[0]["names"] == ["Pasta"]


@pytest.mark.parametrize("names", [None, [], ["Untagged"], [None, ""]])
def test_nothing_to_look_up_means_no_query_at_all(client, names):
    """PostgREST errors on .in_(col, []) — the short-circuit is load-bearing."""
    assert storage._tag_ids(client, names, "omer@x.com") == {}
    assert client.queries == []


def test_resolution_is_scoped_to_the_owner(client):
    """Two users can each own a "Pasta"; they must not collide."""
    assert storage._tag_ids(client, ["Pasta"], "omer@x.com") == {"Pasta": 1}
    assert storage._tag_ids(client, ["Pasta"], "hila@x.com") == {"Pasta": 9}


def test_someone_elses_tag_does_not_resolve(client):
    assert storage._tag_ids(client, ["Soup"], "hila@x.com") == {}


def test_single_lookup_wrapper_agrees_with_the_batch(client):
    assert storage._tag_id(client, "Pasta", "omer@x.com") == 1
    assert storage._tag_id(client, "NoSuchTag", "omer@x.com") is None
    assert storage._tag_id(client, "Untagged", "omer@x.com") is None
    assert storage._tag_id(client, None, "omer@x.com") is None
