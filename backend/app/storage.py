"""Persistence layer — saves recipes into our Supabase (Postgres) database.

Isolates all database access behind simple functions (save_recipe, ...), so the
rest of the app never deals with Supabase directly. Uses the secret key, which
runs server-side only and bypasses Row Level Security.
"""

import functools
import hashlib
import os
import threading
from pathlib import Path

import requests
from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")


# Module-level singleton: created once, reused for every request. Creating a
# fresh client per call skipped connection pooling — each request re-paid the
# (currently ~11s) TLS/connect cost and leaked the connection, stalling the
# server. One shared client keeps the pool warm (~0.2s per query).
_client_instance: Client | None = None


def _client() -> Client:
    """Return the shared Supabase client, creating it once on first use."""
    global _client_instance
    if _client_instance is None:
        if not (SUPABASE_URL and SUPABASE_KEY):
            raise RuntimeError("SUPABASE_URL / SUPABASE_KEY missing in backend/.env")
        _client_instance = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client_instance


# The shared client uses one synchronous HTTP/2 connection, which isn't safe
# under concurrent use from FastAPI's threadpool (Windows raises WinError 10035
# when two requests race). Serialize all DB access through one lock — fine at
# personal-app scale. RLock so a decorated fn can call another without deadlock.
_lock = threading.RLock()


def _synchronized(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _lock:
            return fn(*args, **kwargs)
    return wrapper


def _tag_id(client: Client, name: str | None, owner: str) -> int | None:
    """Map OWNER's tag NAME to its id. Returns None for Untagged/no match.

    Owner-scoped: two users can each have a "Pasta" with different ids.
    This enforces the "null -> Untagged" rule at the database boundary.
    "Unknown" stays reserved too until the parser emits tags (7-10).
    """
    if not name or name in ("Untagged", "Unknown"):
        return None
    result = (
        client.table("tags")
        .select("id")
        .eq("name", name)
        .eq("owner", owner)
        .limit(1)
        .execute()
    )
    return result.data[0]["id"] if result.data else None


@_synchronized
def list_tags(*, owner: str) -> list[dict]:
    """Return OWNER's tags (id + name), sorted by name.

    A plain SELECT — the read counterpart to save_recipe's INSERT.
    """
    client = _client()
    result = (
        client.table("tags")
        .select("id, name")
        .eq("owner", owner)
        .order("name")
        .execute()
    )
    return result.data


@_synchronized
def create_tag(name: str, *, owner: str) -> dict:
    """Insert a new tag for OWNER and return the stored row (id + name).

    The write counterpart to list_tags. Duplicates are per owner (each
    user can have their own "Pasta"); they raise ValueError (endpoint turns it
    into a 409). The module lock makes the check-then-insert atomic, mirroring
    save_recipe's dedupe pattern.
    """
    client = _client()
    if _tag_id(client, name, owner) is not None:
        raise ValueError(f"tag {name!r} already exists")
    result = (
        client.table("tags").insert({"name": name, "owner": owner}).execute()
    )
    return result.data[0]


@_synchronized
def delete_tag(tag_id: int, *, owner: str) -> None:
    """Delete one of OWNER's tags; its recipes fall back to Untagged.

    Ownership is checked FIRST — before any mutation — so a wrong/foreign id
    can't detach another user's recipes (LookupError -> 404 at the endpoint).
    Then two ordered steps: detach recipes (set tag_id = null) so the
    foreign key won't block the delete, then remove the tag row. This
    enforces the plan's "null -> Untagged" rule instead of cascading deletes
    (which destroy recipes) or failing on the FK constraint.
    """
    client = _client()
    owned = (
        client.table("tags")
        .select("id")
        .eq("id", tag_id)
        .eq("owner", owner)
        .limit(1)
        .execute()
    )
    if not owned.data:
        raise LookupError(f"tag {tag_id} not found")
    client.table("recipes").update({"tag_id": None}).eq(
        "tag_id", tag_id
    ).execute()
    client.table("tags").delete().eq("id", tag_id).execute()


@_synchronized
def list_recipes(
    tags: list[str] | None = None,
    collection: str | None = None,
    *,
    owner: str,
) -> list[dict]:
    """Return one owner's recipes (newest first), optionally filtered.

    `owner` scopes every query — users only ever see their own rows (5-3).
    `tags` is a list of tag names combined with AND — a recipe must carry
    EVERY one. The special value "Untagged" (used alone by the frontend)
    filters to recipes with no recipe_tags rows. `collection` filters by a
    cross-cutting flag: "favorites" -> is_favorite, "up_next" -> is_up_next.
    Each returned recipe carries "tags": a name-sorted list of
    {id, name} dicts read from the join table (empty list = Untagged).
    """
    client = _client()
    query = (
        client.table("recipes")
        .select("*, recipe_tags(tags(id, name))")
        .eq("owner", owner)
        .order("created_at", desc=True)
    )
    if tags and "Untagged" in tags:
        # Embed anti-join: keep only recipes with NO recipe_tags rows.
        query = query.is_("recipe_tags", "null")
    elif tags:
        # Two-step filter: names -> tag ids -> recipe ids. A filtered !inner
        # embed would hide the recipe's OTHER tags in the response, so plain
        # queries + Python beat one clever query here.
        tag_ids = set()
        for name in tags:
            tid = _tag_id(client, name, owner)
            if tid is None:
                return []      # unknown tag name -> nothing can match ALL
            tag_ids.add(tid)
        rows = (
            client.table("recipe_tags")
            .select("recipe_id, tag_id")
            .in_("tag_id", list(tag_ids))
            .execute()
        )
        # AND-intersection (PostgREST has no GROUP BY/HAVING): a recipe
        # qualifies only when it matched every requested tag id.
        matched: dict[int, set] = {}
        for row in rows.data:
            matched.setdefault(row["recipe_id"], set()).add(row["tag_id"])
        ids = [rid for rid, got in matched.items() if len(got) == len(tag_ids)]
        if not ids:
            return []          # .in_("id", []) would error — short-circuit
        query = query.in_("id", ids)
    if collection == "favorites":
        query = query.eq("is_favorite", True)
    elif collection == "up_next":
        query = query.eq("is_up_next", True)
    recipes = query.execute().data
    # Flatten the nested embed ([{tags: {id, name}}, ...]) into a simple
    # name-sorted list the frontend reads as recipe.tags.
    for r in recipes:
        r["tags"] = sorted(
            (rt["tags"] for rt in r.pop("recipe_tags", []) if rt.get("tags")),
            key=lambda t: t["name"],
        )
    return recipes


def store_thumbnail(source_url: str, thumbnail_url: str | None) -> str | None:
    """Copy an Instagram thumbnail into our own Supabase Storage bucket.

    Instagram's CDN URLs expire after days and browsers refuse to embed them
    (Cross-Origin-Resource-Policy), so at import time we download the image
    server-side and keep a permanent copy. Returns our public URL, or None on
    any failure — an import must never crash over a missing picture.

    The download runs OUTSIDE the lock — it can take up to 30s and doesn't
    touch the shared client; only the Supabase upload needs serializing.
    """
    if not thumbnail_url:
        return None
    try:
        image = requests.get(thumbnail_url, timeout=30)
        image.raise_for_status()
        # Stable filename per post: re-importing overwrites instead of piling up.
        name = hashlib.md5(source_url.encode()).hexdigest() + ".jpg"
        with _lock:
            client = _client()
            client.storage.from_("thumbnails").upload(
                name,
                image.content,
                file_options={"content-type": "image/jpeg", "upsert": "true"},
            )
            return client.storage.from_("thumbnails").get_public_url(name)
    except Exception:
        return None


@_synchronized
def set_recipe_photo(recipe_id: int, content: bytes, content_type: str, *, owner: str) -> dict:
    """Store an uploaded cover photo and point the recipe's thumbnail at it.

    Stable name per recipe: re-uploading replaces the old photo instead of
    piling up files. Ownership-checked like every other per-recipe write.
    """
    client = _client()
    found = (client.table("recipes").select("id")
             .eq("id", recipe_id).eq("owner", owner).execute())
    if not found.data:
        raise LookupError(f"Recipe {recipe_id} not found")
    name = f"manual-{recipe_id}"
    client.storage.from_("thumbnails").upload(
        name, content,
        file_options={"content-type": content_type, "upsert": "true"},
    )
    url = client.storage.from_("thumbnails").get_public_url(name)
    # .eq("owner") on the write as well as the check above: the ownership
    # test and the update are two separate round trips, so the filter is what
    # actually guarantees we never write to someone else's row.
    result = (client.table("recipes").update({"thumbnail": url})
              .eq("id", recipe_id).eq("owner", owner).execute())
    return result.data[0]


@_synchronized
def find_recipe_by_url(source_url: str, *, owner: str) -> dict | None:
    """Return OWNER's stored recipe for a source_url, or None if not saved yet.

    Dedupe is per user: two family members can each save the same reel into
    their own cookbook. Lets /import short-circuit on a known duplicate BEFORE
    paying for the Apify fetch + Gemini parse (save_recipe also uses it as a
    last-line guard).
    """
    client = _client()
    result = (
        client.table("recipes")
        .select("*")
        .eq("source_url", source_url)
        .eq("owner", owner)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


@_synchronized
def save_recipe(recipe: dict, *, owner: str) -> dict:
    """Insert a parsed recipe for OWNER and return the stored row (new id).

    Idempotent on (source_url, owner): importing the same reel twice
    (double-submit, share + paste, or a Background Sync retry) returns the
    existing row instead of creating a duplicate — but different owners each
    get their own copy. The module lock makes this check-then-insert atomic
    (RLock, so the nested find_recipe_by_url call is fine).

    Args:
        recipe: dict with title, tags (list of names), ingredients, steps,
                source_url, thumbnail — the shape returned by POST /import.
        owner:  the saving user's identity, stamped onto the row.
    """
    client = _client()

    source_url = recipe.get("source_url")
    if source_url:
        existing = find_recipe_by_url(source_url, owner=owner)
        if existing:
            return existing          # already saved — return it, don't duplicate

    row = {
        "title": recipe.get("title"),
        "source_url": recipe.get("source_url"),
        "thumbnail": recipe.get("thumbnail"),
        "ingredients": recipe.get("ingredients"),  # list -> stored as jsonb
        "steps": recipe.get("steps"),              # list -> stored as jsonb
        "owner": owner,
    }
    result = client.table("recipes").insert(row).execute()
    stored = result.data[0]
    # Tags land ONLY in the join table now (7-10); recipes.tag_id is dead
    # weight until 7-11 drops it. Unknown names and repeats drop out.
    tag_rows, seen = [], set()
    for name in recipe.get("tags") or []:
        tid = _tag_id(client, name, owner)
        if tid is not None and tid not in seen:
            seen.add(tid)
            tag_rows.append({"recipe_id": stored["id"], "tag_id": tid})
    if tag_rows:
        client.table("recipe_tags").insert(tag_rows).execute()
    return stored


@_synchronized
def update_recipe(
    recipe_id: int,
    *,
    owner: str,
    is_favorite: bool | None = None,
    is_up_next: bool | None = None,
    tags: list[str] | None = None,
    title: str | None = None,
    ingredients: list | None = None,
    steps: list | None = None,
) -> dict:
    """Partial-update a recipe and return the updated row (with its tags).

    Only the fields passed (non-None) are written, so a caller can toggle a
    collection flag OR re-tag independently. `tags` is a full REPLACEMENT
    list of tag names — [] clears them all; unrecognized names are skipped.
    Keyword-only args prevent positional mix-ups.
    """
    client = _client()
    updates: dict = {}
    if is_favorite is not None:
        updates["is_favorite"] = is_favorite
    if is_up_next is not None:
        updates["is_up_next"] = is_up_next
    if title is not None:
        updates["title"] = title
    if ingredients is not None:
        updates["ingredients"] = ingredients   # list -> stored as jsonb
    if steps is not None:
        updates["steps"] = steps               # list -> stored as jsonb
    if not updates and tags is None:
        raise ValueError("no fields to update")

    if updates:
        result = (
            client.table("recipes")
            .update(updates)
            .eq("id", recipe_id)
            .eq("owner", owner)
            .execute()
        )
        if not result.data:
            # LookupError (not HTTPException): storage stays HTTP-agnostic;
            # the endpoint layer translates this into a 404. Someone else's
            # recipe id takes this same path — a 404, not a hint it exists.
            raise LookupError(f"recipe {recipe_id} not found")
        row = result.data[0]
    else:
        # Tags-only PATCH: no row update to prove ownership, so check it
        # explicitly before touching the join table.
        found = (
            client.table("recipes")
            .select("*")
            .eq("id", recipe_id)
            .eq("owner", owner)
            .limit(1)
            .execute()
        )
        if not found.data:
            raise LookupError(f"recipe {recipe_id} not found")
        row = found.data[0]

    if tags is not None:
        # Full replacement: swap ALL of this recipe's join rows for the new
        # set. Names are resolved per owner; unknowns and repeats drop out.
        client.table("recipe_tags").delete().eq("recipe_id", recipe_id).execute()
        tag_rows, seen = [], set()
        for name in tags:
            tid = _tag_id(client, name, owner)
            if tid is not None and tid not in seen:
                seen.add(tid)
                tag_rows.append({"recipe_id": recipe_id, "tag_id": tid})
        if tag_rows:
            client.table("recipe_tags").insert(tag_rows).execute()

    # Return a fresh tags list (same shape as list_recipes) so callers can
    # trust the response instead of guessing what the replacement produced.
    joined = (
        client.table("recipe_tags")
        .select("tags(id, name)")
        .eq("recipe_id", recipe_id)
        .execute()
    )
    row["tags"] = sorted(
        (j["tags"] for j in joined.data if j.get("tags")),
        key=lambda t: t["name"],
    )
    return row


@_synchronized
def delete_user(owner: str) -> dict:
    """Erase every row belonging to OWNER; returns counts per table.

    Recipes go first — they reference tags via the FK, so deleting
    tags first would be blocked. Thumbnails in Storage are left behind
    as harmless orphans (stable names, overwritten on any re-import).

    shared_recipes needs explicit cleanup: only its recipe_id column has a
    cascading FK, so deleting the user's recipes clears the offers they SENT
    but leaves the offers they RECEIVED pointing at an account that no longer
    exists — rows nothing would ever delete again.
    """
    client = _client()
    inbox = (client.table("shared_recipes").delete()
             .eq("to_owner", owner).execute())
    recipes = client.table("recipes").delete().eq("owner", owner).execute()
    tags = client.table("tags").delete().eq("owner", owner).execute()
    # Registry row last: its FK cascades erase the user from every friends
    # list (theirs and other people's).
    users = client.table("users").delete().eq("email", owner).execute()
    return {"recipes": len(recipes.data), "tags": len(tags.data),
            "users": len(users.data), "shares": len(inbox.data)}


@_synchronized
def delete_recipe(recipe_id: int, *, owner: str) -> bool:
    """Delete one of OWNER's recipes by id; True if a row actually went.

    Recipes are leaf rows (nothing references them), so this is a straight
    DELETE — simpler than delete_tag, which first detaches its recipes.
    Returning the outcome lets the endpoint answer 404 instead of reporting
    success for an id that never existed (or belongs to someone else).
    """
    client = _client()
    result = (client.table("recipes").delete()
              .eq("id", recipe_id).eq("owner", owner).execute())
    return bool(result.data)


@_synchronized
def register_user(email: str) -> dict:
    """Upsert EMAIL into the users registry (idempotent — login calls this)."""
    result = _client().table("users").upsert(
        {"email": email}, on_conflict="email"
    ).execute()
    return result.data[0]


@_synchronized
def user_exists(email: str) -> bool:
    """Is EMAIL in the users registry? (friend/recipient validation)."""
    rows = _client().table("users").select("email").eq("email", email).execute()
    return bool(rows.data)


@_synchronized
def list_friends(*, owner: str) -> list[dict]:
    """OWNER's friends (their personal share address book)."""
    result = (_client().table("friends").select("friend_email, created_at")
              .eq("owner", owner).order("friend_email").execute())
    return result.data


@_synchronized
def add_friend(friend_email: str, *, owner: str) -> dict:
    """Add FRIEND_EMAIL to OWNER's list. Must be a registered user.

    Upsert: adding an existing friend is a harmless no-op, not an error.
    """
    if not user_exists(friend_email):
        raise LookupError(f"{friend_email} is not a registered user")
    result = _client().table("friends").upsert(
        {"owner": owner, "friend_email": friend_email},
        on_conflict="owner,friend_email",
    ).execute()
    return result.data[0]


@_synchronized
def remove_friend(friend_email: str, *, owner: str) -> None:
    """Drop FRIEND_EMAIL from OWNER's list (unknown email is a no-op)."""
    (_client().table("friends").delete()
     .eq("owner", owner).eq("friend_email", friend_email).execute())


@_synchronized
def share_recipe(recipe_id: int, *, from_owner: str, to_owner: str) -> dict:
    """Offer one of FROM_OWNER's recipes to TO_OWNER (status: pending).

    Upsert on (recipe_id, to_owner): re-sharing revives a dismissed offer
    instead of stacking duplicate rows.
    """
    client = _client()
    found = (client.table("recipes").select("id")
             .eq("id", recipe_id).eq("owner", from_owner).execute())
    if not found.data:
        raise LookupError(f"Recipe {recipe_id} not found")
    result = client.table("shared_recipes").upsert(
        {"recipe_id": recipe_id, "from_owner": from_owner,
         "to_owner": to_owner, "status": "pending"},
        on_conflict="recipe_id,to_owner",
    ).execute()
    return result.data[0]


@_synchronized
def list_shares(*, owner: str) -> list[dict]:
    """Pending share offers for OWNER, each with a preview of the recipe."""
    result = (
        _client().table("shared_recipes")
        .select("id, from_owner, created_at,"
                " recipes(title, thumbnail, ingredients, steps)")
        .eq("to_owner", owner)
        .eq("status", "pending")
        .order("created_at", desc=True)
        .execute()
    )
    return result.data


@_synchronized
def resolve_share(share_id: int, *, owner: str, accept: bool) -> dict:
    """Accept (copy the recipe into OWNER's cookbook) or dismiss an offer.

    The copy goes through save_recipe, so URL-dedupe applies and tags map
    by name (names the recipient doesn't own are skipped).
    """
    client = _client()
    found = (client.table("shared_recipes").select("*")
             .eq("id", share_id).eq("to_owner", owner)
             .eq("status", "pending").execute())
    if not found.data:
        raise LookupError(f"Share {share_id} not found")
    share = found.data[0]
    copied = None
    if accept:
        src = (client.table("recipes").select("*, recipe_tags(tags(name))")
               .eq("id", share["recipe_id"]).execute()).data[0]
        copied = save_recipe(
            {
                "title": src["title"],
                "source_url": src["source_url"],
                "thumbnail": src["thumbnail"],
                "ingredients": src["ingredients"],
                "steps": src["steps"],
                "tags": [rt["tags"]["name"] for rt in src["recipe_tags"]
                         if rt.get("tags")],
            },
            owner=owner,
        )
    client.table("shared_recipes").update(
        {"status": "accepted" if accept else "dismissed"}
    ).eq("id", share_id).execute()
    return copied or {"status": "dismissed", "id": share_id}


# Direct test:  py app/storage.py  — inserts one sample recipe.
if __name__ == "__main__":
    import json
    import sys

    sys.stdout.reconfigure(encoding="utf-8")

    sample = {
        "title": "Test — Spaghetti alla Nerano",
        "tags": ["Pasta"],
        "ingredients": [{"name": "Spaghetti", "quantity": 320, "unit": "g"}],
        "steps": ["Fry zucchini.", "Cook pasta.", "Toss with provolone."],
        "source_url": "https://www.instagram.com/reel/DZ1ydoFKh1p/",
        "thumbnail": None,
    }
    stored = save_recipe(sample, owner="omergrinwald14@gmail.com")
    print("Saved row:")
    print(json.dumps(stored, indent=2, ensure_ascii=False))
