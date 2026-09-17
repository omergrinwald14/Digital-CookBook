"""Turn a raw recipe caption into structured data using Google Gemini.

This module isolates all LLM logic. It receives the messy caption text plus the
user's fixed tag list, and returns clean structured JSON:
ingredients (name/quantity/unit), ordered steps, a title, and 1-2 tags that
are ALWAYS drawn from the allowed list ([] when none fits).
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
MODEL = "gemini-2.5-flash"  # fast, free-tier-friendly model

# Module-level singleton, same reasoning as storage.py's Supabase client:
# building a fresh client per call re-pays the TLS handshake and connection
# setup on every import, and throws away the connection afterwards.
_client_instance = None


def _client():
    """Return the shared Gemini client, creating it once on first use."""
    global _client_instance
    if _client_instance is None:
        _client_instance = genai.Client(api_key=GEMINI_API_KEY)
    return _client_instance


def _build_prompt(caption: str, tags: list[str]) -> str:
    """Construct the instruction we send to Gemini.

    The prompt pins down (a) the exact JSON shape and (b) the allowed tag
    list, so the model cannot invent tags or formats — "constrained output".
    """
    tag_list = ", ".join(tags)
    return f"""You parse cooking-video captions into structured recipes.

From the caption below, extract:
- "title": a short dish name (string)
- "ingredients": a list of objects, each {{"name": str, "quantity": number or null, "unit": str or null}}
- "steps": an ordered list of short instruction strings
- "tags": a JSON array of 1 or 2 tag names chosen ONLY from this allowed
  list: [{tag_list}]. If only one clearly fits, return just that one.
  If none clearly fits, return [].

Rules:
- Return ONLY valid JSON, no commentary.
- LANGUAGE: write "title", ingredient names, and "steps" in the SAME language
  as the caption. Do NOT translate the recipe content.
- TAG MATCHING: the allowed tag names above may be written in a
  different language than the caption. Match by MEANING (translate mentally),
  then output each tag string EXACTLY as it appears in the allowed list.
  Example: a Hebrew caption "קציצות בקר" matches the allowed tag "Meatballs".
- If the caption contains no actual recipe, return:
  {{"title": null, "ingredients": null, "steps": null, "tags": []}}

CAPTION:
\"\"\"{caption}\"\"\"
"""


def parse_recipe(caption: str, tags: list[str]) -> dict:
    """Parse a caption into a structured recipe dict.

    Args:
        caption: The raw Instagram caption text.
        tags: The user's fixed tag list (e.g. ["Pasta", "Soup"]).

    Returns:
        Dict with keys: title, ingredients, steps, tags. The tags list holds
        at most 2 names, every one guaranteed to be from `tags` ([] = none fit).
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is missing. Add it to backend/.env")

    if not caption:
        # No caption to parse → follow the no-tags/null fallback rule.
        return {"title": None, "ingredients": None, "steps": None, "tags": []}

    response = _client().models.generate_content(
        model=MODEL,
        contents=_build_prompt(caption, tags),
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )

    # Malformed model output (bad/empty JSON, or JSON that isn't an object)
    # degrades to the same null/"Unknown" fallback as a missing caption —
    # never a 500. Real API errors (network/quota) still raise, so a
    # Background Sync retry can succeed later.
    try:
        data = json.loads(response.text or "")
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        return {"title": None, "ingredients": None, "steps": None, "tags": []}

    # Safety net: enforce our rules even if the model strays — members of
    # the allowed list only, at most 2, [] for anything malformed.
    raw = data.get("tags")
    data["tags"] = [t for t in raw if t in tags][:2] if isinstance(raw, list) else []

    return data


# Direct test:  py app/parser.py
if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")

    sample_caption = (
        "Spaghetti alla Nerano. Fry thin zucchini rounds in olive oil with garlic "
        "until golden. Cook 320g spaghetti until al dente. Toss with butter, the "
        "fried zucchini, pasta water and grated provolone to make a creamy sauce. "
        "Top with basil. #pasta #italianfood"
    )
    sample_tags = ["Pasta", "Soup", "Salad", "Dessert", "Chicken", "Breakfast"]

    result = parse_recipe(sample_caption, sample_tags)
    print(json.dumps(result, indent=2, ensure_ascii=False))
