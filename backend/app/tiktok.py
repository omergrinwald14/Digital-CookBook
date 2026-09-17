"""Fetch metadata (caption, thumbnail, link) from a TikTok video URL.

Mirror of instagram.py for TikTok: we call the Apify TikTok Scraper (hosted,
same account/token) and ask for a single post by URL. The parser downstream
only needs a caption, so this module's job is just URL -> metadata dict.
"""

import os
import re
from pathlib import Path

import requests
from dotenv import load_dotenv

# Load backend/.env regardless of where the script is run from.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

APIFY_TOKEN = os.environ.get("APIFY_TOKEN")

# Same single-call pattern as Instagram: run the scraper, get items back.
APIFY_ACTOR = "clockworks~tiktok-scraper"
APIFY_URL = f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items"

# Post URL: tiktok.com/@user/video/123 (or /photo/ for carousels). The
# username may be EMPTY (@/video/…) — that's what short links resolve to —
# so we match it loosely and drop it from the canonical form: the numeric
# post id alone identifies the video, and one post must map to ONE URL
# (dedupe) no matter which link shape it arrived as.
_TIKTOK_URL_RE = re.compile(r"tiktok\.com/@[^/?#]*/(video|photo)/(\d+)")
# Share-sheet short links: vm.tiktok.com/X, vt.tiktok.com/X, tiktok.com/t/X.
_TIKTOK_SHORT_RE = re.compile(r"(?:v[mt]\.tiktok\.com|tiktok\.com/t)/[A-Za-z0-9]+")


def normalize_tiktok_url(url: str) -> str:
    """Canonicalize any TikTok link to https://www.tiktok.com/@user/<kind>/<id>.

    Short share links (vm./vt./tiktok.com/t/) hide the real post id, so we
    follow their redirect once to learn the canonical URL — needed both for
    Apify and for dedupe (one post = one stable URL). Tracking params are
    dropped. Raises ValueError on anything that isn't a TikTok post link.
    """
    match = _TIKTOK_URL_RE.search(url or "")
    if not match:
        short = _TIKTOK_SHORT_RE.search(url or "")
        if not short:
            raise ValueError(f"Not a valid TikTok post URL: {url!r}")
        try:
            # stream=True: we only want the final URL, not the page body.
            resp = requests.get(
                f"https://{short.group(0)}", allow_redirects=True,
                timeout=15, stream=True,
            )
            resp.close()
            match = _TIKTOK_URL_RE.search(resp.url)
        except requests.RequestException:
            match = None
        if not match:
            raise ValueError(f"Could not resolve TikTok short link: {url!r}")
    kind, post_id = match.groups()
    return f"https://www.tiktok.com/@/{kind}/{post_id}"


def fetch_caption(url: str) -> dict:
    """Return basic metadata for a TikTok post.

    Args:
        url: A canonical (normalized) public TikTok post URL.

    Returns:
        A dict with: caption (post description, may be None), title (author
        display name), thumbnail (cover image URL), source_url, and
        fetch_failed — True only when Apify itself was unreachable (same
        contract as instagram.py).
    """
    if not APIFY_TOKEN:
        raise RuntimeError("APIFY_TOKEN is missing. Add it to backend/.env")

    payload = {"postURLs": [url], "resultsPerPage": 1}

    try:
        response = requests.post(
            APIFY_URL,
            params={"token": APIFY_TOKEN},
            json=payload,
            timeout=90,  # see instagram.py: Render kills the request at ~100s
        )
        response.raise_for_status()
        items = response.json()
    except requests.RequestException:
        # Same contract as instagram.py: Apify is down, not the post.
        return {"caption": None, "title": None, "thumbnail": None,
                "source_url": url, "fetch_failed": True}

    if not items or "error" in items[0]:
        # No data (private/removed post, or the actor reported an error).
        return {"caption": None, "title": None, "thumbnail": None,
                "source_url": url, "fetch_failed": False}

    post = items[0]
    author = post.get("authorMeta") or {}
    video = post.get("videoMeta") or {}
    return {
        "caption": post.get("text"),
        "title": author.get("nickName") or author.get("name"),
        "thumbnail": video.get("coverUrl"),
        "source_url": url,
        "fetch_failed": False,
    }


# Lets us test this file directly from the command line:
#   py -X utf8 app/tiktok.py "https://vt.tiktok.com/XXXX/"
if __name__ == "__main__":
    import sys

    # Windows terminals default to cp1252 and can't print emoji/Unicode captions.
    sys.stdout.reconfigure(encoding="utf-8")

    if len(sys.argv) < 2:
        print("Usage: python app/tiktok.py <tiktok_url>")
        sys.exit(1)

    canonical = normalize_tiktok_url(sys.argv[1])
    print("CANONICAL:", canonical)
    data = fetch_caption(canonical)
    print("TITLE    :", data["title"])
    print("THUMBNAIL:", data["thumbnail"])
    print("CAPTION  :\n", data["caption"])
