#!/usr/bin/env python3
"""
Calls the freeblogapi /v1/generate endpoint and writes a new Jekyll post.

Confirmed request schema (from the real API):
  POST https://freeblogapi.onrender.com/v1/generate
  Header: X-API-Key: <key>
  Body: topic, keywords, targetAudience, language, region, tone,
        lengthStrategy, maxWords, maxH2, includeFaq, format
"""

import os
import re
import sys
import json
import datetime
import urllib.request
import urllib.error

API_URL = "https://freeblogapi.onrender.com/v1/generate"
API_KEY = os.environ.get("FREEBLOGAPI_KEY", "")

TOPIC = "AI-powered content marketing for small businesses"

REQUEST_BODY = {
    "topic": TOPIC,
    "keywords": ["AI marketing", "content strategy", "small business growth"],
    "targetAudience": "small business owners",
    "language": "en",
    "region": "US",
    "tone": "professional",
    "lengthStrategy": "standard",
    "maxWords": 500,
    "maxH2": 3,
    "includeFaq": True,
    "format": "markdown",
}


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s-]+", "-", text)
    return text[:60].strip("-") or "post"


def write_debug(text: str):
    with open("debug_log.txt", "a", encoding="utf-8") as f:
        f.write(text.rstrip("\n") + "\n\n")


def call_api():
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["X-API-Key"] = API_KEY

    req = urllib.request.Request(
        API_URL,
        data=json.dumps(REQUEST_BODY).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    write_debug(f"--- Run at {datetime.datetime.utcnow().isoformat()} UTC ---")
    write_debug(f"REQUEST URL: {API_URL}")
    write_debug(f"REQUEST BODY: {json.dumps(REQUEST_BODY)}")

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8")
            write_debug(f"RESPONSE STATUS: {resp.status}")
            write_debug(f"RESPONSE BODY: {body[:3000]}")
            return json.loads(body)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        write_debug(f"HTTPError {e.code}")
        write_debug(f"RESPONSE BODY: {err_body}")
        print(f"API HTTPError {e.code}: {err_body}", file=sys.stderr)
        raise
    except urllib.error.URLError as e:
        write_debug(f"URLError: {e.reason}")
        print(f"API URLError: {e.reason}", file=sys.stderr)
        raise


def main():
    data = call_api()

    # Response field names may vary — try common possibilities.
    title = (
        data.get("title")
        or data.get("headline")
        or f"Tech News — {datetime.date.today().isoformat()}"
    )
    content = (
        data.get("content")
        or data.get("markdown")
        or data.get("text")
        or data.get("post")
        or data.get("body")
        or json.dumps(data)
    )

    today = datetime.date.today()
    slug = slugify(title)
    filename = f"{today.isoformat()}-{slug}.md"
    filepath = os.path.join("_posts", filename)

    front_matter = (
        "---\n"
        f'title: "{title.replace(chr(34), chr(39))}"\n'
        f"date: {today.isoformat()}\n"
        "---\n\n"
    )

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(front_matter)
        f.write(content.strip() + "\n")

    print(f"Wrote {filepath}")


if __name__ == "__main__":
    main()

