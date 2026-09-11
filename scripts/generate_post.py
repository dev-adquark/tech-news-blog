#!/usr/bin/env python3
"""
1. Fetches a live top tech headline from NewsAPI.org.
2. Sends that headline + description to OpenRouter (Qwen model) to write an
   original blog post about it.
3. Writes the generated content as a new Jekyll post in _posts/.
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

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_KEY = os.environ.get("OPENROUTER_KEY", "")
MODEL = os.environ.get("QWEN_MODEL", "qwen/qwen-2.5-7b-instruct")

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
            source = (article.get("source") or {}).get("name", "")
            url_ = article.get("url", "")
            return title, description, source, url_
    except Exception as e:
        write_debug(f"NEWSAPI ERROR: {e}")

    return FALLBACK_TOPIC, "", "", ""


def generate_post(headline, description, source, source_url):
    system_prompt = (
        "You are a tech blog writer. Given a news headline and short description, "
        "write an original, engaging blog post about it (do not just repeat the "
        "headline text verbatim). Write in Markdown. Start with a single '# Title' "
        "line, then the body. Keep it to roughly 400-600 words, professional but "
        "readable tone, aimed at tech-savvy readers. Do not include a FAQ section."
    )
    user_prompt = (
        f"Headline: {headline}\n"
        f"Description: {description or 'N/A'}\n"
        f"Source: {source or 'N/A'}\n\n"
        "Write the blog post now."
    )

    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENROUTER_KEY}",
    }

    write_debug(f"OPENROUTER URL: {OPENROUTER_URL}")
    write_debug(f"OPENROUTER MODEL: {MODEL}")
    write_debug(f"OPENROUTER USER PROMPT: {user_prompt}")

    try:
        status, resp_body = http_post(OPENROUTER_URL, body, headers)
        write_debug(f"OPENROUTER STATUS: {status}")
        write_debug(f"OPENROUTER BODY: {resp_body[:3000]}")
        return json.loads(resp_body)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        write_debug(f"OPENROUTER HTTPError {e.code}: {err_body}")
        print(f"API HTTPError {e.code}: {err_body}", file=sys.stderr)
        raise
    except urllib.error.URLError as e:
        write_debug(f"OPENROUTER URLError: {e.reason}")
        print(f"API URLError: {e.reason}", file=sys.stderr)
        raise


def extract_title_and_body(markdown_text, fallback_title):
    lines = markdown_text.strip().splitlines()
    title = fallback_title
    body_lines = lines

    if lines and lines[0].strip().startswith("#"):
        title = lines[0].lstrip("#").strip()
        body_lines = lines[1:]

    body = "\n".join(body_lines).strip()
    return title, body


def main():
    headline, description, source, source_url = fetch_headline()
    data = generate_post(headline, description, source, source_url)

    choices = data.get("choices") or []
    if not choices:
        write_debug(f"UNEXPECTED RESPONSE SHAPE: {json.dumps(data)[:1000]}")
        raise RuntimeError("No choices returned from OpenRouter")

    raw_content = choices[0]["message"]["content"]
    title, body = extract_title_and_body(raw_content, headline)

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
        f.write(body + "\n")
        if source and source_url:
            f.write(f"\n*Source: [{source}]({source_url})*\n")

    print(f"Wrote {filepath}")


if __name__ == "__main__":
    main()
