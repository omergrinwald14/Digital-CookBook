"""Fetch metadata (caption, thumbnail, link) from a Facebook reel URL.

Mirror of instagram.py / tiktok.py for Facebook, through Apify (Facebook has
no free official API for reading public posts). One module per source, and
main.py's _resolve_source() dispatches to it. Cost: ~$0.006 per reel, paid
from the Apify FREE plan's $5 monthly credit (hard-capped, never billed).

Scope: reels only — the format recipes are shared in. Regular video posts
(/videos/, /watch) can be added later.
"""

import os
import re
from pathlib import Path

import requests
from dotenv import load_dotenv

# Load backend/.env regardless of where the script is run from.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

APIFY_TOKEN = os.environ.get("APIFY_TOKEN")

# Apify's own Facebook actor (~149k users). Takes a single reel URL and
# answered in ~4s in the spike, same single-call pattern as Instagram.
APIFY_ACTOR = "apify~facebook-posts-scraper"
APIFY_URL = f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items"

# Canonical reel: facebook.com/reel/<numeric id>. Subdomains (www., m., web.)
# match too, because the pattern is unanchored.
_FB_REEL_RE = re.compile(r"facebook\.com/reel/(\d+)")
# What the app's Share button hands out: facebook.com/share/r/<code>. The
# code hides the reel id, so it has to be resolved through its redirect.
_FB_SHORT_RE = re.compile(r"facebook\.com/share/r/[A-Za-z0-9_-]+")


def normalize_facebook_url(url: str) -> str:
    """Canonicalize any Facebook reel link to https://www.facebook.com/reel/<id>/.

    Share links (/share/r/<code>) are followed once to learn the reel id —
    needed both for Apify and for dedupe (one reel = one stable URL).
    Tracking params (?rdid=, ?share_url=) are dropped. Raises ValueError on
    anything that isn't a Facebook reel link.
    """
    match = _FB_REEL_RE.search(url or "")
    if not match:
        short = _FB_SHORT_RE.search(url or "")
        if not short:
            raise ValueError(f"Not a valid Facebook reel URL: {url!r}")
        try:
            # requests' default user agent on purpose: Facebook answers a
            # browser user agent with HTTP 400 instead of the redirect.
            # stream=True: we only want the final URL, not the page body.
            resp = requests.get(
                f"https://www.{short.group(0)}/", allow_redirects=True,
                timeout=15, stream=True,
            )
            resp.close()
            match = _FB_REEL_RE.search(resp.url)
        except requests.RequestException:
            match = None
        if not match:
            raise ValueError(f"Could not resolve Facebook share link: {url!r}")
    return f"https://www.facebook.com/reel/{match.group(1)}/"


def fetch_caption(url: str) -> dict:
    """Return basic metadata for a Facebook reel.

    Args:
        url: A canonical (normalized) Facebook reel URL.

    Returns:
        Same contract as instagram.py / tiktok.py: caption, title, thumbnail,
        source_url, fetch_failed. caption = the post text plus Facebook's own
        AI summary of the video, which carries the method when the caption
        is a single line. fetch_failed is True only when Apify itself was
        unreachable, never because the reel is private or removed.
    """
    if not APIFY_TOKEN:
        raise RuntimeError("APIFY_TOKEN is missing. Add it to backend/.env")

    payload = {"startUrls": [{"url": url}], "resultsLimit": 1}
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
        # Same contract as instagram.py: Apify is down, not the reel.
        return {"caption": None, "title": None, "thumbnail": None,
                "source_url": url, "fetch_failed": True}

    if not items or "error" in items[0]:
        # No data (private/removed reel, or the actor reported an error).
        return {"caption": None, "title": None, "thumbnail": None,
                "source_url": url, "fetch_failed": False}

    post = items[0]
    reel = post.get("short_form_video_context") or {}
    video = reel.get("video") or {}
    caption = (post.get("message") or {}).get("text") or post.get("text")
    summary = (video.get("seo_llm_content") or {}).get("summary")
    parts = (caption, f"Video summary: {summary}" if summary else None)
    return {
        "caption": "\n\n".join(p for p in parts if p) or None,
        "title": (reel.get("video_owner") or {}).get("name"),
        "thumbnail": ((reel.get("playback_video") or {}).get("thumbnailImage") or {}).get("uri")
                     or video.get("first_frame_thumbnail"),
        "source_url": url,
        "fetch_failed": False,
    }


# Lets us test this file directly from the command line:
#   py -X utf8 app/facebook.py "https://www.facebook.com/share/r/XXXXXXXXXX/"
if __name__ == "__main__":
    import sys

    # Windows terminals default to cp1252 and can't print emoji/Unicode captions.
    sys.stdout.reconfigure(encoding="utf-8")

    if len(sys.argv) < 2:
        print("Usage: python app/facebook.py <facebook_reel_url>")
        sys.exit(1)

    canonical = normalize_facebook_url(sys.argv[1])
    print("CANONICAL:", canonical)
    data = fetch_caption(canonical)
    print("FAILED   :", data["fetch_failed"])
    print("TITLE    :", data["title"])
    print("THUMBNAIL:", data["thumbnail"])
    print("CAPTION  :\n", data["caption"])
