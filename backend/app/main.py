"""FastAPI application — the HTTP entry point for the Digital CookBook backend.

It exposes the recipe pipeline as a web API so the frontend (and later the
phone's Share button) can use it. The heavy lifting lives in the imported
modules; this file just wires them together behind endpoints.
"""

from fastapi import Depends, FastAPI, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.instagram import fetch_caption as fetch_instagram
from app.instagram import normalize_instagram_url
from app.parser import parse_recipe
from app.tiktok import fetch_caption as fetch_tiktok
from app.tiktok import normalize_tiktok_url
from app.storage import (
    add_friend,
    clear_recipe_photo,
    create_tag,
    delete_recipe,
    delete_tag,
    delete_user,
    find_recipe_by_url,
    list_friends,
    list_recipes,
    list_shares,
    list_tags,
    register_user,
    remove_friend,
    resolve_share,
    save_recipe,
    set_recipe_photo,
    share_recipe,
    store_thumbnail,
    update_recipe,
    user_exists,
)

app = FastAPI(title="Digital CookBook API")

# Allow the browser frontend (a different origin) to call this API.
# Any origin, deliberately: the API is public until Phase 5 adds auth, and
# CORS wouldn't stop non-browser clients (curl, iOS Shortcut) anyway.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# Identity (5-3, family-trust model): who is calling = the X-User header, an
# email the frontend will store after a one-time login screen. No password —
# the threat model is "typo", not "attacker". Upgrading to real auth later
# only changes this one function.
DEFAULT_OWNER = "omergrinwald14@gmail.com"


def current_user(x_user: str = Header(default=DEFAULT_OWNER)) -> str:
    """The requesting user, from the X-User header — trimmed and lowercased.

    Normalizing HERE, at the single seam every endpoint depends on, is the
    whole point: before this, half the code called .lower() and half didn't,
    so a header of "Dana@Gmail.com" wrote recipes under one spelling and
    looked its friends and share offers up under another. One rule, one place.

    Missing/blank header falls back to DEFAULT_OWNER so clients that predate
    login (queued shares, iOS Shortcut) keep working during the migration;
    the default goes away once every client sends the header.
    """
    return x_user.strip().lower() or DEFAULT_OWNER


class ImportRequest(BaseModel):
    """Shape of the JSON body for POST /import. Pydantic validates it for us."""
    url: str


class TagRequest(BaseModel):
    """Body for POST /tags — the new tag's name."""
    name: str


class Ingredient(BaseModel):
    """One ingredient — same shape the parser emits, so edits stay compatible."""
    name: str
    quantity: float | None = None
    unit: str | None = None


class RecipePatch(BaseModel):
    """Body for PATCH /recipes/{id} — any subset of fields to update.

    `tags` is a full replacement list of tag names ([] = clear them all);
    omitting it leaves the recipe's tags untouched.
    """
    is_favorite: bool | None = None
    is_up_next: bool | None = None
    tags: list[str] | None = None
    title: str | None = None
    ingredients: list[Ingredient] | None = None
    steps: list[str] | None = None


class RecipeCreate(BaseModel):
    """Body for POST /recipes — a manually typed recipe (no video source)."""
    title: str
    ingredients: list[Ingredient] | None = None
    steps: list[str] | None = None
    tags: list[str] = []


@app.get("/")
def health() -> dict:
    """Simple health check — confirms the server is running."""
    return {"status": "ok"}


@app.get("/tags")
def get_tags(user: str = Depends(current_user)) -> list[dict]:
    """List the caller's tags for browsing."""
    return list_tags(owner=user)


@app.post("/tags", status_code=201)
def add_tag(body: TagRequest, user: str = Depends(current_user)) -> dict:
    """Create a tag and return the stored row (id + name).

    Validation lives here at the endpoint: blank names are rejected with 400
    before we touch the DB. 201 = 'Created', the correct status for a POST
    that makes a new resource. "Untagged"/"Unknown" are reserved filter
    values, so a tag can't take those names.
    """
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Tag name cannot be empty.")
    if name in ("Untagged", "Unknown"):
        raise HTTPException(status_code=400, detail=f'"{name}" is a reserved name.')
    try:
        return create_tag(name, owner=user)
    except ValueError:
        # 409 Conflict = "the resource already exists" — the standard answer
        # to a duplicate create, distinct from 400 (malformed input).
        raise HTTPException(status_code=409, detail=f'Tag "{name}" already exists.')


@app.delete("/tags/{tag_id}")
def remove_tag(tag_id: int, user: str = Depends(current_user)) -> dict:
    """Delete one of the caller's tags; its recipes fall back to Untagged."""
    try:
        delete_tag(tag_id, owner=user)
    except LookupError:
        raise HTTPException(status_code=404, detail="Tag not found.")
    return {"status": "deleted", "id": tag_id}


@app.get("/recipes")
def get_recipes(
    tag: list[str] | None = Query(None),
    collection: str | None = None,
    user: str = Depends(current_user),
) -> list[dict]:
    """List the caller's recipes, filtered by ?collection= or ?tag=.

    ?tag= repeats for multi-select (?tag=A&tag=B) and combines with AND —
    only recipes carrying every requested tag are returned.
    """
    return list_recipes(tag, collection, owner=user)


@app.post("/recipes", status_code=201)
def add_recipe(body: RecipeCreate, user: str = Depends(current_user)) -> dict:
    """Create a recipe typed in by hand — no fetch/parse, straight to storage."""
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="Title cannot be empty.")
    return save_recipe(
        {
            "title": body.title.strip(),
            "tags": body.tags,
            "ingredients": (
                [i.model_dump() for i in body.ingredients]
                if body.ingredients is not None else None
            ),
            "steps": body.steps,
            "source_url": None,
            "thumbnail": None,
        },
        owner=user,
    )


@app.post("/recipes/{recipe_id}/photo")
def upload_photo(recipe_id: int, photo: UploadFile,
                 user: str = Depends(current_user)) -> dict:
    """Attach an uploaded cover photo to one of the caller's recipes."""
    if not (photo.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image.")
    # Check the declared size FIRST. Reading before checking meant a 500 MB
    # upload was fully buffered into a 512 MB instance's memory just to be
    # rejected on the next line — the check has to happen before the read.
    max_bytes = 5 * 1024 * 1024
    if photo.size is not None and photo.size > max_bytes:
        raise HTTPException(status_code=400, detail="Image too large (max 5 MB).")
    content = photo.file.read(max_bytes + 1)   # bounded read, never unbounded
    if len(content) > max_bytes:
        raise HTTPException(status_code=400, detail="Image too large (max 5 MB).")
    try:
        return set_recipe_photo(recipe_id, content, photo.content_type, owner=user)
    except LookupError:
        raise HTTPException(status_code=404, detail="Recipe not found.")


@app.delete("/recipes/{recipe_id}/photo")
def remove_photo(recipe_id: int, user: str = Depends(current_user)) -> dict:
    """Remove a recipe's cover photo (the card falls back to no image)."""
    try:
        return clear_recipe_photo(recipe_id, owner=user)
    except LookupError:
        raise HTTPException(status_code=404, detail="Recipe not found.")


@app.patch("/recipes/{recipe_id}")
def patch_recipe(
    recipe_id: int, body: RecipePatch, user: str = Depends(current_user)
) -> dict:
    """Update a recipe: flags, tags, and/or edited title/ingredients/steps."""
    # exclude_unset = only fields the client actually sent, so this 400 check
    # doesn't need updating every time RecipePatch grows a field.
    if not body.model_dump(exclude_unset=True):
        raise HTTPException(status_code=400, detail="No fields provided.")
    if body.title is not None and not body.title.strip():
        raise HTTPException(status_code=400, detail="Title cannot be empty.")
    try:
        return update_recipe(
            recipe_id,
            owner=user,
            is_favorite=body.is_favorite,
            is_up_next=body.is_up_next,
            tags=body.tags,
            title=body.title.strip() if body.title else None,
            ingredients=(
                [i.model_dump() for i in body.ingredients]
                if body.ingredients is not None else None
            ),
            steps=body.steps,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Recipe not found.")


@app.delete("/recipes/{recipe_id}")
def remove_recipe(recipe_id: int, user: str = Depends(current_user)) -> dict:
    """Delete one of the caller's recipes by id."""
    if not delete_recipe(recipe_id, owner=user):
        # Matches DELETE /tags: an id that isn't ours gets the same 404 as one
        # that doesn't exist, so the response never confirms someone else's row.
        raise HTTPException(status_code=404, detail="Recipe not found.")
    return {"status": "deleted", "id": recipe_id}


class ShareRequest(BaseModel):
    """Body for POST /recipes/{id}/share — who receives the offer."""
    to: str


@app.post("/recipes/{recipe_id}/share", status_code=201)
def share_a_recipe(recipe_id: int, body: ShareRequest,
                   user: str = Depends(current_user)) -> dict:
    """Offer one of the caller's recipes to a registered user.

    A valid recipient is auto-added to the caller's friends list, so the
    share picker's drop-list builds itself.
    """
    to = body.to.strip().lower()
    if not to:
        raise HTTPException(status_code=400, detail="Recipient email required.")
    if to == user:
        raise HTTPException(status_code=400,
                            detail="You can't share a recipe with yourself.")
    if not user_exists(to):
        raise HTTPException(status_code=404,
                            detail=f"No user with the email {to}.")
    try:
        share = share_recipe(recipe_id, from_owner=user, to_owner=to)
    except LookupError:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    add_friend(to, owner=user)
    return share


@app.get("/shared")
def get_shares(user: str = Depends(current_user)) -> list[dict]:
    """The caller's inbox: pending share offers with recipe previews."""
    return list_shares(owner=user)


@app.post("/shared/{share_id}/accept")
def accept_share(share_id: int, user: str = Depends(current_user)) -> dict:
    """Copy the offered recipe into the caller's cookbook."""
    try:
        return resolve_share(share_id, owner=user, accept=True)
    except LookupError:
        raise HTTPException(status_code=404, detail="Share not found.")


@app.post("/shared/{share_id}/dismiss")
def dismiss_share(share_id: int, user: str = Depends(current_user)) -> dict:
    """Decline an offer (it disappears; a re-share revives it)."""
    try:
        return resolve_share(share_id, owner=user, accept=False)
    except LookupError:
        raise HTTPException(status_code=404, detail="Share not found.")


class FriendRequest(BaseModel):
    """Body for POST /friends — the friend's email."""
    email: str


@app.get("/friends")
def get_friends(user: str = Depends(current_user)) -> list[dict]:
    """The caller's friends list (share address book)."""
    return list_friends(owner=user)


@app.post("/friends", status_code=201)
def add_a_friend(body: FriendRequest, user: str = Depends(current_user)) -> dict:
    """Add a friend by email; they must be a registered user (404 otherwise)."""
    email = body.email.strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email required.")
    if email == user:
        raise HTTPException(status_code=400,
                            detail="You're already you — pick someone else.")
    try:
        return add_friend(email, owner=user)
    except LookupError:
        raise HTTPException(status_code=404,
                            detail=f"No user with the email {email}.")


@app.delete("/friends/{email}")
def remove_a_friend(email: str, user: str = Depends(current_user)) -> dict:
    """Remove a friend from the caller's list."""
    email = email.strip().lower()
    remove_friend(email, owner=user)
    return {"status": "deleted", "friend": email}


@app.post("/users", status_code=201)
def add_user(user: str = Depends(current_user)) -> dict:
    """Register the caller in the users registry (called at login)."""
    return register_user(user)


@app.delete("/users/{email}")
def remove_user(email: str, user: str = Depends(current_user)) -> dict:
    """Delete a user = erase all their rows (5-3f; backend-only for now).

    Self-service only: X-User must match the email being deleted, so one
    family member can't wipe another's cookbook (403 otherwise).
    """
    email = email.strip().lower()
    if email != user:
        raise HTTPException(
            status_code=403, detail="You can only delete your own data."
        )
    counts = delete_user(email)
    return {"status": "deleted", "user": email, **counts}


def _resolve_source(raw_url: str):
    """Map a raw shared link to (canonical URL, fetcher).

    One place per supported source: try each normalizer until one claims the
    URL. Adding a future source = one more except-branch here, nothing else.
    """
    try:
        return normalize_instagram_url(raw_url), fetch_instagram
    except ValueError:
        try:
            return normalize_tiktok_url(raw_url), fetch_tiktok
        except ValueError:
            raise ValueError(f"Not an Instagram or TikTok post link: {raw_url!r}")


@app.post("/import")
def import_recipe(body: ImportRequest, user: str = Depends(current_user)) -> dict:
    """Fetch an Instagram/TikTok post and return it as a structured recipe.

    Pipeline: URL -> fetch (Apify) -> parse_recipe (Gemini) -> recipe.
    """
    try:
        url, fetch = _resolve_source(body.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Early dedupe: a known URL returns the saved row immediately, skipping
    # the Apify fetch + Gemini parse (seconds + quota saved on every retry).
    existing = find_recipe_by_url(url, owner=user)
    if existing:
        return existing

    meta = fetch(url)
    if meta.get("fetch_failed"):
        # The scraper was unreachable — a temporary fault on our side, not a
        # verdict on the post. 502 (not 200-with-empty-fields) tells the
        # caller "try again": the service worker's queue already retries 5xx,
        # and the user doesn't end up with a blank recipe to clean up.
        raise HTTPException(
            status_code=502,
            detail="Couldn't reach the video service. Please try again in a minute.",
        )

    tag_names = [t["name"] for t in list_tags(owner=user)]
    recipe = parse_recipe(meta["caption"], tag_names)

    # Merge the two sources: parsed recipe fields + fetch metadata (link/thumb).
    merged = {
        "title": recipe.get("title") or meta.get("title"),
        "tags": recipe.get("tags"),
        "ingredients": recipe.get("ingredients"),
        "steps": recipe.get("steps"),
        "source_url": meta.get("source_url"),
        "thumbnail": store_thumbnail(meta.get("source_url"), meta.get("thumbnail")),
    }

    # Persist it and return the stored row (now with a real id + created_at).
    return save_recipe(merged, owner=user)
