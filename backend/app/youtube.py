"""Fetch metadata (description, thumbnail, link) from a YouTube video URL.

Mirror of instagram.py / tiktok.py for YouTube: one module per source, and
main.py's _resolve_source() dispatches to it. Unlike those two, this does
not go through Apify — the official YouTube Data API v3 is free (10,000
units/day, one video lookup = 1 unit) and needs only an API key.
"""

import os
import re
from pathlib import Path

import requests
from dotenv import load_dotenv

# Load backend/.env regardless of where the script is run from.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3/videos"

# Best first; maxres/standard are missing on some videos.
_THUMB_SIZES = ("maxres", "standard", "high", "medium", "default")

# Every YouTube link shape carries the same 11-character video id:
#   youtube.com/watch?v=ID   (v= may sit after other params: ?list=X&v=ID)
#   youtu.be/ID              (what the app's Share button produces)
#   youtube.com/shorts/ID, /embed/ID, /live/ID
# Subdomains (m., music.) match too, because the pattern is unanchored.
_YOUTUBE_URL_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:[^#]*&)?v=|shorts/|embed/|live/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})"
)


def normalize_youtube_url(url: str) -> str:
    """Canonicalize any YouTube link to https://www.youtube.com/watch?v=<id>.

    One video must map to ONE URL (dedupe), whichever shape it arrived as —
    a Short shared as youtu.be and later pasted as /shorts/ is the same
    recipe. Tracking params (?si=, ?feature=) are dropped. Raises ValueError
    on anything that isn't a YouTube video link.
    """
    match = _YOUTUBE_URL_RE.search(url or "")
    if not match:
        raise ValueError(f"Not a valid YouTube video URL: {url!r}")
    return f"https://www.youtube.com/watch?v={match.group(1)}"


def fetch_caption(url: str) -> dict:
    """Return basic metadata for a YouTube video.

    Args:
        url: A canonical (normalized) YouTube video URL.

    Returns:
        Same contract as instagram.py / tiktok.py: caption, title, thumbnail,
        source_url, fetch_failed. caption = video title + description,
        because YouTube recipes live in the description. fetch_failed is True
        only when the API itself failed (network, bad key, quota exhausted),
        never because the video is private or removed.
    """
    if not YOUTUBE_API_KEY:
        raise RuntimeError("YOUTUBE_API_KEY is missing. Add it to backend/.env")

    video_id = _YOUTUBE_URL_RE.search(url).group(1)
    try:
        response = requests.get(
            YOUTUBE_API_URL,
            params={"part": "snippet", "id": video_id, "key": YOUTUBE_API_KEY},
            timeout=30,
        )
        response.raise_for_status()
        items = response.json().get("items") or []
    except requests.RequestException:
        # Network, bad key, or quota exhausted: our problem, not the video's.
        # /import answers 502 and the client's retry paths try again later.
        return {"caption": None, "title": None, "thumbnail": None,
                "source_url": url, "fetch_failed": True}

    if not items:
        # Private/removed video: the API answered fine, there is just nothing
        # to read — save it with null fields, as designed.
        return {"caption": None, "title": None, "thumbnail": None,
                "source_url": url, "fetch_failed": False}

    snippet = items[0]["snippet"]
    thumbs = snippet.get("thumbnails") or {}
    title = snippet.get("title")
    return {
        "caption": "\n\n".join(p for p in (title, snippet.get("description")) if p) or None,
        "title": title,
        "thumbnail": next((thumbs[s]["url"] for s in _THUMB_SIZES if s in thumbs), None),
        "source_url": url,
        "fetch_failed": False,
    }


# Lets us test this file directly from the command line:
#   py -X utf8 app/youtube.py "https://youtu.be/XXXXXXXXXXX"
if __name__ == "__main__":
    import sys

    # Windows terminals default to cp1252 and can't print emoji/Unicode captions.
    sys.stdout.reconfigure(encoding="utf-8")

    if len(sys.argv) < 2:
        print("Usage: python app/youtube.py <youtube_url>")
        sys.exit(1)

    canonical = normalize_youtube_url(sys.argv[1])
    print("CANONICAL:", canonical)
    data = fetch_caption(canonical)
    print("FAILED   :", data["fetch_failed"])
    print("TITLE    :", data["title"])
    print("THUMBNAIL:", data["thumbnail"])
    print("CAPTION  :\n", data["caption"])
