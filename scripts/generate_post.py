#!/usr/bin/env python3
"""
1. Fetches a live top tech headline from NewsAPI.org.
2. Sends that headline as the topic to freeblogapi's /v1/generate endpoint.
3. Writes the generated content as a new Jekyll post.

Confirmed freeblogapi request schema:
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
import urllib.parse

NEWSAPI_URL = "https://newsapi.org/v2/top-headlines"
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "")

GENERATE_URL = "https://freeblogapi.onrender.com/v1/generate"
FREEBLOGAPI_KEY = os.environ.get("FREEBLOGAPI_KEY", "")

FALLBACK_TOPIC = "the latest developments in technology"


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s-]+", "-", text)
    return text[:60].strip("-") or "post"


def write_debug(text: str):
    with open("debug_log.txt", "a", encoding="utf-8") as f:
        f.write(text.rstrip("\n") + "\n\n")


def http_get(url):
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, resp.read().decode("utf-8")


def http_post(url, body, headers):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.status, resp.read().decode("utf-8")


def fetch_headline():
    """Get today's top US tech headline from NewsAPI. Falls back if it fails."""
    params = {
        "category": "technology",
        "country": "us",
        "pageSize": 1,
        "apiKey": NEWSAPI_KEY,
    }
    url = f"{NEWSAPI_URL}?{urllib.parse.urlencode(params)}"
    write_debug(f"--- Run at {datetime.datetime.utcnow().isoformat()} UTC ---")
    write_debug(f"NEWSAPI URL: {NEWSAPI_URL} (params redacted key)")

    try:
        status, body = http_get(url)
        write_debug(f"NEWSAPI STATUS: {status}")
        write_debug(f"NEWSAPI BODY: {body[:2000]}")
        data = json.loads(body)
        articles = data.get("articles") or []
        if articles:
            article = articles[0]
            title = article.get("title") or FALLBACK_TOPIC
            description = article.get("description") or ""
            return title, description
    except Exception as e:
        write_debug(f"NEWSAPI ERROR: {e}")

    return FALLBACK_TOPIC, ""


def generate_post(topic, description):
    keywords = [w for w in re.findall(r"[A-Za-z]{4,}", topic)][:5] or ["technology", "tech news"]

    body = {
        "topic": topic,
        "keywords": keywords,
        "targetAudience": "tech-savvy readers",
        "language": "en",
        "region": "US",
        "tone": "professional",
        "lengthStrategy": "standard",
        "maxWords": 500,
        "maxH2": 3,
        "includeFaq": True,
        "format": "markdown",
    }
    if description:
        body["topic"] = f"{topic} — {description}"

    headers = {"Content-Type": "application/json"}
    if FREEBLOGAPI_KEY:
        headers["X-API-Key"] = FREEBLOGAPI_KEY

    write_debug(f"GENERATE URL: {GENERATE_URL}")
    write_debug(f"GENERATE BODY: {json.dumps(body)}")

    try:
        status, resp_body = http_post(GENERATE_URL, body, headers)
        write_debug(f"GENERATE STATUS: {status}")
        write_debug(f"GENERATE BODY: {resp_body[:3000]}")
        return json.loads(resp_body)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        write_debug(f"GENERATE HTTPError {e.code}: {err_body}")
        print(f"API HTTPError {e.code}: {err_body}", file=sys.stderr)
        raise
    except urllib.error.URLError as e:
        write_debug(f"GENERATE URLError: {e.reason}")
        print(f"API URLError: {e.reason}", file=sys.stderr)
        raise


def main():
    headline, description = fetch_headline()
    data = generate_post(headline, description)

    title = (
        data.get("title")
        or data.get("headline")
        or headline
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


