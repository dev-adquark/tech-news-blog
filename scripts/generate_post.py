#!/usr/bin/env python3
"""
Calls the freeblogapi /generate/sync endpoint and writes a new Jekyll post.

NOTE: The exact request/response field names below are a best-guess default
based on common "generate blog post" API shapes. If your actual API at
https://freeblogapi.onrender.com/docs uses different field names, update:
  - REQUEST_BODY (what we send)
  - the parsing of `data` below (what we expect back)
to match the real schema shown in the Swagger docs.
"""

import os
import re
import sys
import json
import datetime
import urllib.request
import urllib.error

API_URL = "https://freeblogapi.onrender.com/generate/sync"
API_KEY = os.environ.get("FREEBLOGAPI_KEY", "")  # set as a repo secret if the API requires one

TOPIC = "the latest technology news and trends today"

REQUEST_BODY = {
    "topic": TOPIC,
    "prompt": f"Write a short, engaging blog post about {TOPIC}.",
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
        headers["Authorization"] = f"Bearer {API_KEY}"

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
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            write_debug(f"RESPONSE STATUS: {resp.status}")
            write_debug(f"RESPONSE BODY: {body}")
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

    # Best-guess extraction — adjust keys if the real API response differs.
    title = data.get("title") or f"Tech News — {datetime.date.today().isoformat()}"
    content = data.get("content") or data.get("text") or data.get("post") or json.dumps(data)

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
