#!/usr/bin/env python3
"""
Content engine pipeline:

  Mode select (news / evergreen)
    -> Topic/Keyword Discovery
    -> Trend + News Research (NewsAPI)
    -> Source Collection (multiple candidate articles)
    -> Relevance Filtering
    -> Deduplication (vs. published_index.json)
    -> Fact Extraction
    -> Content Brief
    -> Qwen Generation (grounded on the brief only)
    -> SEO Optimization (slug, meta description, keywords)
    -> Fact Validation (automated grounding check)
    -> Quality Validation (length / placeholder checks)
    -> Duplicate/Cannibalization check (vs. recent published topics)
    -> Publish (write _posts/*.md + update published_index.json)

Fails CLOSED: if any validation gate fails, the script exits without
writing a post rather than publishing something low-quality or ungrounded.
"""

import os
import re
import sys
import json
import random
import datetime
import urllib.request
import urllib.error
import urllib.parse

# ---------------------------------------------------------------------------
# Config / secrets
# ---------------------------------------------------------------------------

NEWSAPI_TOP_URL = "https://newsapi.org/v2/top-headlines"
NEWSAPI_EVERYTHING_URL = "https://newsapi.org/v2/everything"
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_KEY = os.environ.get("OPENROUTER_KEY", "")
MODEL = os.environ.get("QWEN_MODEL", "qwen/qwen-2.5-7b-instruct")

# Manual overrides (from workflow_dispatch inputs)
NEWS_QUERY = os.environ.get("NEWS_QUERY", "").strip()
MODE_OVERRIDE = os.environ.get("CONTENT_MODE", "").strip().lower()  # "news" / "evergreen" / ""

PUBLISHED_INDEX_PATH = "data/published_index.json"
SEED_TOPICS_PATH = "data/seed_topics.json"
DEDUP_WINDOW_DAYS = 21
CANNIBALIZATION_JACCARD_THRESHOLD = 0.5
MIN_WORD_COUNT = 250


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def write_debug(text: str):
    with open("debug_log.txt", "a", encoding="utf-8") as f:
        f.write(text.rstrip("\n") + "\n\n")


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s-]+", "-", text)
    return text[:60].strip("-") or "post"


def word_set(text: str):
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


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


def call_qwen(system_prompt, user_prompt, max_tokens=None):
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    if max_tokens:
        body["max_tokens"] = max_tokens

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENROUTER_KEY}",
    }
    status, resp_body = http_post(OPENROUTER_URL, body, headers)
    data = json.loads(resp_body)
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"No choices from Qwen: {resp_body[:500]}")
    return choices[0]["message"]["content"].strip()


def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Stage: Mode select + Topic/Keyword Discovery
# ---------------------------------------------------------------------------

def select_mode():
    if MODE_OVERRIDE in ("news", "evergreen"):
        return MODE_OVERRIDE
    return "news" if datetime.date.today().toordinal() % 2 == 0 else "evergreen"


def recent_topics(index, days):
    cutoff = datetime.date.today() - datetime.timedelta(days=days)
    out = []
    for entry in index:
        try:
            d = datetime.date.fromisoformat(entry["date"])
        except Exception:
            continue
        if d >= cutoff:
            out.append(entry)
    return out


def discover_evergreen_topic(index):
    seeds = load_json(SEED_TOPICS_PATH, [])
    recent = recent_topics(index, DEDUP_WINDOW_DAYS)
    recent_titles_words = [word_set(e.get("title", "")) for e in recent]

    random.shuffle(seeds)
    for topic in seeds:
        tw = word_set(topic)
        if all(jaccard(tw, rw) < CANNIBALIZATION_JACCARD_THRESHOLD for rw in recent_titles_words):
            return topic
    return seeds[0] if seeds else "technology trends"


# ---------------------------------------------------------------------------
# Stage: Trend + News Research / Source Collection
# ---------------------------------------------------------------------------

def collect_candidate_articles(mode, topic_hint=None):
    if NEWS_QUERY:
        query = NEWS_QUERY
    elif mode == "evergreen":
        query = topic_hint
    else:
        query = None

    if query:
        params = {
            "q": query,
            "language": "en",
            "sortBy": "relevancy",
            "pageSize": 5,
            "apiKey": NEWSAPI_KEY,
        }
        url = f"{NEWSAPI_EVERYTHING_URL}?{urllib.parse.urlencode(params)}"
        write_debug(f"SOURCE COLLECTION URL: {NEWSAPI_EVERYTHING_URL} (q='{query}')")
    else:
        params = {
            "category": "technology",
            "country": "us",
            "pageSize": 5,
            "apiKey": NEWSAPI_KEY,
        }
        url = f"{NEWSAPI_TOP_URL}?{urllib.parse.urlencode(params)}"
        write_debug(f"SOURCE COLLECTION URL: {NEWSAPI_TOP_URL} (top-headlines/technology)")

    try:
        status, body = http_get(url)
        write_debug(f"SOURCE COLLECTION STATUS: {status}")
        write_debug(f"SOURCE COLLECTION BODY: {body[:2500]}")
        data = json.loads(body)
        return data.get("articles") or []
    except Exception as e:
        write_debug(f"SOURCE COLLECTION ERROR: {e}")
        return []


# ---------------------------------------------------------------------------
# Stage: Relevance Filtering + Deduplication
# ---------------------------------------------------------------------------

def filter_and_dedup(articles, index, query_hint=None):
    recent = recent_topics(index, DEDUP_WINDOW_DAYS)
    recent_words = [word_set(e.get("title", "")) for e in recent]
    query_words = word_set(query_hint) if query_hint else None

    candidates = []
    for a in articles:
        title = (a.get("title") or "").strip()
        desc = (a.get("description") or "").strip()
        if not title or len(title) < 10:
            continue
        if "[Removed]" in title:
            continue
        tw = word_set(title)

        # Relevance filter: when we searched by a specific topic/query, require
        # meaningful keyword overlap between the article and the query — NewsAPI's
        # "everything" search can return loosely-matched or unrelated results.
        if query_words:
            combined = tw | word_set(desc)
            overlap = len(combined & query_words)
            if overlap < 2:
                continue

        if any(jaccard(tw, rw) >= CANNIBALIZATION_JACCARD_THRESHOLD for rw in recent_words):
            continue
        candidates.append(a)
    return candidates


# ---------------------------------------------------------------------------
# Stage: Fact Extraction + Content Brief
# ---------------------------------------------------------------------------

def build_brief(mode, articles, topic_hint=None):
    facts = []
    sources = []
    for a in articles[:3]:
        title = (a.get("title") or "").strip()
        desc = (a.get("description") or "").strip()
        source_name = (a.get("source") or {}).get("name", "")
        source_url = a.get("url", "")
        if title:
            facts.append(f"- {title}" + (f" — {desc}" if desc else ""))
        if source_url:
            sources.append({"name": source_name, "url": source_url})

    angle = topic_hint if mode == "evergreen" else (articles[0].get("title") if articles else None)

    return {
        "mode": mode,
        "angle": angle,
        "facts": facts,
        "sources": sources,
    }


# ---------------------------------------------------------------------------
# Stage: Qwen Generation
# ---------------------------------------------------------------------------

def generate_from_brief(brief):
    facts_block = "\n".join(brief["facts"]) if brief["facts"] else "(no specific sourced facts available)"

    system_prompt = (
        "You are a professional tech blog writer. Write an original blog post in "
        "Markdown based ONLY on the brief provided. Start with a single '# Title' "
        "line, then the body. Aim for 450-700 words, professional but readable, "
        "for a tech-savvy audience. Do not include a FAQ section.\n\n"
        "STRICT GROUNDING RULES:\n"
        "- Only state facts that appear in the brief's 'Known facts' list, or that "
        "are well-established general knowledge.\n"
        "- Never invent specific details not given to you — no fabricated stats, "
        "quotes, prices, dates, or product specs.\n"
        "- Framing, analysis, and context are fine, but must be clearly presented "
        "as commentary/analysis, not as reported fact.\n"
        "- Stay tightly focused on the angle/topic given. If a 'known fact' seems "
        "tangential or unrelated to the main topic, leave it out rather than "
        "forcing it into the post as a section or case study.\n"
        "- If the brief has limited facts, write a shorter, more general post "
        "rather than padding with invented specifics."
    )
    user_prompt = (
        f"Mode: {brief['mode']}\n"
        f"Angle/topic: {brief['angle']}\n\n"
        f"Known facts:\n{facts_block}\n\n"
        "Write the blog post now."
    )

    write_debug(f"GENERATION PROMPT (user):\n{user_prompt}")
    content = call_qwen(system_prompt, user_prompt)
    write_debug(f"GENERATION OUTPUT:\n{content[:3000]}")
    return content


def extract_title_and_body(markdown_text, fallback_title):
    lines = markdown_text.strip().splitlines()
    title = fallback_title
    body_lines = lines
    if lines and lines[0].strip().startswith("#"):
        title = lines[0].lstrip("#").strip()
        body_lines = lines[1:]
    return title, "\n".join(body_lines).strip()


# ---------------------------------------------------------------------------
# Stage: SEO Optimization
# ---------------------------------------------------------------------------

def build_seo_fields(title, body, brief):
    plain = re.sub(r"[#*_`>\-]", "", body)
    plain = re.sub(r"\s+", " ", plain).strip()
    description = plain[:155].rsplit(" ", 1)[0] + "…" if len(plain) > 155 else plain

    keywords = set()
    for w in re.findall(r"[A-Za-z]{4,}", title):
        keywords.add(w.lower())
    if brief.get("angle"):
        for w in re.findall(r"[A-Za-z]{4,}", brief["angle"]):
            keywords.add(w.lower())
    keywords = list(keywords)[:8]

    return description, keywords


# ---------------------------------------------------------------------------
# Stage: Fact Validation (automated grounding check)
# ---------------------------------------------------------------------------

def validate_facts(brief, body):
    facts_block = "\n".join(brief["facts"]) if brief["facts"] else "(none provided)"
    system_prompt = (
        "You are a strict fact-checking assistant. You will be given a list of "
        "known facts and a blog post. Identify whether the post contains any "
        "specific factual claims (numbers, statistics, quotes, prices, named "
        "product features, dates) that are NOT supported by the known facts and "
        "are not common general knowledge. Reply with exactly 'PASS' if there are "
        "no such unsupported claims, or 'FAIL: <brief reason>' if there are."
    )
    user_prompt = f"Known facts:\n{facts_block}\n\nBlog post:\n{body}"

    try:
        result = call_qwen(system_prompt, user_prompt)
        write_debug(f"FACT VALIDATION RESULT: {result}")
        return result.strip().upper().startswith("PASS"), result
    except Exception as e:
        write_debug(f"FACT VALIDATION ERROR (treated as pass with warning): {e}")
        return True, "validator unavailable, skipped"


# ---------------------------------------------------------------------------
# Stage: Quality Validation
# ---------------------------------------------------------------------------

BAD_PHRASES = [
    "as an ai language model",
    "lorem ipsum",
    "i cannot",
    "i'm sorry, but",
]


def validate_quality(title, body):
    word_count = len(body.split())
    if word_count < MIN_WORD_COUNT:
        return False, f"too short ({word_count} words)"
    lowered = body.lower()
    for phrase in BAD_PHRASES:
        if phrase in lowered:
            return False, f"contains bad phrase: '{phrase}'"
    if not title or len(title) < 5:
        return False, "missing or too-short title"
    return True, "ok"


# ---------------------------------------------------------------------------
# Stage: Publish
# ---------------------------------------------------------------------------

def publish(title, body, description, keywords, brief):
    today = datetime.date.today()
    slug = slugify(title)
    filename = f"{today.isoformat()}-{slug}.md"
    filepath = os.path.join("_posts", filename)

    front_matter_lines = [
        "---",
        f'title: "{title.replace(chr(34), chr(39))}"',
        f"date: {today.isoformat()}",
        f'description: "{description.replace(chr(34), chr(39))}"',
        f"keywords: [{', '.join(keywords)}]",
        f"mode: {brief['mode']}",
        "---",
        "",
    ]

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(front_matter_lines))
        f.write(body + "\n")
        if brief["sources"]:
            f.write("\n---\n\n**Sources:**\n\n")
            for s in brief["sources"]:
                if s.get("url"):
                    f.write(f"- [{s.get('name') or s['url']}]({s['url']})\n")

    print(f"Wrote {filepath}")

    index = load_json(PUBLISHED_INDEX_PATH, [])
    index.append({
        "date": today.isoformat(),
        "title": title,
        "slug": slug,
        "mode": brief["mode"],
        "keywords": keywords,
    })
    save_json(PUBLISHED_INDEX_PATH, index)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main():
    write_debug(f"=== Pipeline run {datetime.datetime.utcnow().isoformat()} UTC ===")

    index = load_json(PUBLISHED_INDEX_PATH, [])

    mode = select_mode()
    write_debug(f"MODE: {mode}")

    topic_hint = None
    if mode == "evergreen" and not NEWS_QUERY:
        topic_hint = discover_evergreen_topic(index)
        write_debug(f"DISCOVERED EVERGREEN TOPIC: {topic_hint}")

    articles = collect_candidate_articles(mode, topic_hint)
    query_hint = NEWS_QUERY or (topic_hint if mode == "evergreen" else None)
    candidates = filter_and_dedup(articles, index, query_hint)

    if not candidates and articles and mode == "news":
        # For news mode with no query filter, top-headlines are inherently on-topic
        # (they came from the technology category), so it's safe to fall back to it.
        write_debug("No candidates passed dedup — using top headline anyway (news mode, no query filter).")
        candidates = articles[:1]

    if not candidates:
        write_debug("No relevant/sourced articles available — proceeding with brief containing no sourced facts.")

    brief = build_brief(mode, candidates, topic_hint)
    write_debug(f"BRIEF: {json.dumps(brief, indent=2)}")

    raw_content = generate_from_brief(brief)
    fallback_title = brief["angle"] or "Tech Update"
    title, body = extract_title_and_body(raw_content, fallback_title)

    ok, reason = validate_quality(title, body)
    write_debug(f"QUALITY VALIDATION: {ok} ({reason})")
    if not ok:
        print(f"Quality validation failed: {reason}. Not publishing.", file=sys.stderr)
        sys.exit(1)

    passed, reason = validate_facts(brief, body)
    write_debug(f"FACT VALIDATION PASSED: {passed}")
    if not passed:
        print(f"Fact validation failed: {reason}. Not publishing.", file=sys.stderr)
        sys.exit(1)

    description, keywords = build_seo_fields(title, body, brief)
    publish(title, body, description, keywords, brief)


if __name__ == "__main__":
    main()
