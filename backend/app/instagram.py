"""Fetch metadata (caption, thumbnail, link) from an Instagram post URL.

This module isolates Instagram-fetching. We call the Apify Instagram Scraper
API (a hosted service that handles login/cookies/rate-limits for us) and ask
for a single post by its direct URL. We get clean JSON back containing the
caption — no scraping or authentication logic lives in our app.
"""

import os
import re
from pathlib import Path

import requests
from dotenv import load_dotenv

# Load backend/.env regardless of where the script is run from.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

APIFY_TOKEN = os.environ.get("APIFY_TOKEN")

# The "run-sync-get-dataset-items" endpoint runs the scraper and returns its
# results in a single HTTP call — simplest possible request/response.
APIFY_ACTOR = "apify~instagram-scraper"
APIFY_URL = f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items"

# Matches the post code in any Instagram URL shape: /reel/, /reels/, /p/, /tv/.
_INSTAGRAM_URL_RE = re.compile(r"instagram\.com/(reel|reels|p|tv)/([A-Za-z0-9_-]+)")


def normalize_instagram_url(url: str) -> str:
    """Canonicalize an Instagram URL to one stable, fetchable form.

    Strips tracking params (?igsh=, ?utm_source=) and unifies path shapes so
    the same post always maps to one URL — fixing Apify (/reels/ returned only
    the owner, no caption) and dedupe (each variant saved a separate recipe).
    Raises ValueError on anything that isn't an Instagram post link.
    """
    match = _INSTAGRAM_URL_RE.search(url or "")
    if not match:
        raise ValueError(f"Not a valid Instagram post URL: {url!r}")
    kind, code = match.group(1), match.group(2)
    if kind == "reels":
        kind = "reel"            # /reels/ (plural) breaks Apify; /reel/ works
    return f"https://www.instagram.com/{kind}/{code}/"


def fetch_caption(url: str) -> dict:
    """Return basic metadata for an Instagram post.

    Args:
        url: A public Instagram post/reel URL.

    Returns:
        A dict with: caption (post description, may be None), title, thumbnail
        (image URL), source_url (the original link), and fetch_failed — True
        only when Apify itself was unreachable, so the caller can tell
        "the service is down" apart from "this post has no caption".
    """
    if not APIFY_TOKEN:
        raise RuntimeError("APIFY_TOKEN is missing. Add it to backend/.env")

    # Tell the scraper exactly which post to fetch, and to stop after one.
    payload = {"directUrls": [url], "resultsType": "posts", "resultsLimit": 1}

    try:
        response = requests.post(
            APIFY_URL,
            params={"token": APIFY_TOKEN},
            json=payload,
            # 90s, not 120: Render cuts a request off at ~100s, so a longer
            # timeout here only means the platform kills us before our own
            # error handling ever gets to run.
            timeout=90,
        )
        response.raise_for_status()
        items = response.json()
    except requests.RequestException:
        # Apify unreachable/errored (outage, quota exhausted, timeout). This is
        # OUR problem, not the post's — flag it so /import can answer 502 and
        # let the client retry, instead of silently saving an empty recipe the
        # user then has to hunt down and delete.
        return {"caption": None, "title": None, "thumbnail": None,
                "source_url": url, "fetch_failed": True}

    if not items:
        # No data (e.g. private/removed post). Apify answered fine, the post
        # just has nothing for us — save it with null fields, as designed.
        return {"caption": None, "title": None, "thumbnail": None,
                "source_url": url, "fetch_failed": False}

    post = items[0]
    return {
        "caption": post.get("caption"),
        "title": post.get("ownerFullName") or post.get("ownerUsername"),
        "thumbnail": post.get("displayUrl"),
        "source_url": url,
        "fetch_failed": False,
    }


# Lets us test this file directly from the command line:
#   py app/instagram.py "https://www.instagram.com/reel/XXXX/"
if __name__ == "__main__":
    import sys

    # Windows terminals default to cp1252 and can't print emoji/Unicode captions.
    sys.stdout.reconfigure(encoding="utf-8")

    if len(sys.argv) < 2:
        print("Usage: python app/instagram.py <instagram_url>")
        sys.exit(1)

    data = fetch_caption(sys.argv[1])
    print("TITLE    :", data["title"])
    print("THUMBNAIL:", data["thumbnail"])
    print("CAPTION  :\n", data["caption"])
