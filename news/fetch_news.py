"""
news/fetch_news.py
--------------------
Fetches recent product-analytics / SaaS-growth / retention news via
NewsAPI.org, has Claude write a short 3-4 bullet digest summarizing them,
and stores the result in the same SQLite database the app already uses
(data/product_insights.db, table `news_digest`).

Meant to run on a schedule (see .github/workflows/daily_news.yml), which
is what makes this "daily" -- Render's free tier has no built-in cron, so
a GitHub Actions workflow runs this script once a day and commits the
updated database back to the repo. The backend just reads whatever's in
the table; it never calls NewsAPI itself.

On NewsAPI.org's free "Developer" tier: it's explicitly for development/
non-commercial use, with a 24-hour article delay and a 100-requests/day
cap. Fetching once a day, server-side (this script, not a browser), is
exactly the intended use case for a personal/portfolio project.

Requires:
  NEWSAPI_KEY        from newsapi.org (required)
  ANTHROPIC_API_KEY   optional -- without it, the digest falls back to a
                       plain headline list instead of a Claude-written
                       summary, same fallback philosophy as ai/narrator.py

Run:
  NEWSAPI_KEY=... ANTHROPIC_API_KEY=... python news/fetch_news.py
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from analytics.metrics import DB_PATH

NEWSAPI_URL = "https://newsapi.org/v2/everything"
QUERY = '"product analytics" OR "user retention" OR "SaaS growth" OR "churn reduction" OR "growth marketing"'
MAX_ARTICLES = 6
KEEP_LAST_N_DIGESTS = 30  # prune old rows so the table doesn't grow unbounded


def fetch_articles(api_key: str) -> list[dict]:
    resp = requests.get(NEWSAPI_URL, params={
        "q": QUERY,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": MAX_ARTICLES,
        "apiKey": api_key,
    }, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "ok":
        raise RuntimeError(f"NewsAPI error: {data.get('message', data)}")

    articles = []
    for a in data.get("articles", [])[:MAX_ARTICLES]:
        articles.append({
            "title": a.get("title") or "(untitled)",
            "url": a.get("url"),
            "source": (a.get("source") or {}).get("name", "unknown"),
            "published_at": a.get("publishedAt"),
            "description": a.get("description") or "",
        })
    return articles


def summarize_with_claude(articles: list[dict], anthropic_key: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=anthropic_key)
    headlines_block = "\n".join(
        f"- {a['title']} ({a['source']}): {a['description']}" for a in articles
    )
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=250,
        system=(
            "You write a short daily briefing for a product analyst. You will "
            "be given a list of real headlines and descriptions. Summarize "
            "them into 3-4 bullet points covering the key themes. Never "
            "invent facts, numbers, or headlines beyond what's given. Plain "
            "text bullets starting with '-', no markdown headers."
        ),
        messages=[{"role": "user", "content": f"Headlines:\n{headlines_block}"}],
    )
    return "".join(b.text for b in message.content if getattr(b, "type", None) == "text").strip()


def fallback_digest(articles: list[dict]) -> str:
    return "\n".join(f"- {a['title']} ({a['source']})" for a in articles)


def save_to_db(digest_text: str, articles: list[dict]):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS news_digest (
                fetched_date TEXT PRIMARY KEY,
                digest_text TEXT,
                articles_json TEXT,
                created_at TEXT
            )
        """)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        conn.execute(
            "INSERT OR REPLACE INTO news_digest (fetched_date, digest_text, articles_json, created_at) "
            "VALUES (?, ?, ?, ?)",
            (today, digest_text, json.dumps(articles), datetime.now(timezone.utc).isoformat()),
        )
        # Prune old rows so the table doesn't grow forever
        conn.execute("""
            DELETE FROM news_digest WHERE fetched_date NOT IN (
                SELECT fetched_date FROM news_digest ORDER BY fetched_date DESC LIMIT ?
            )
        """, (KEEP_LAST_N_DIGESTS,))
        conn.commit()
    finally:
        conn.close()


def main():
    newsapi_key = os.environ.get("NEWSAPI_KEY")
    if not newsapi_key:
        print("ERROR: NEWSAPI_KEY environment variable is not set.", file=sys.stderr)
        print("Get a free key at https://newsapi.org/register", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(DB_PATH):
        print("ERROR: data/product_insights.db not found. Run data/generate_data.py first.", file=sys.stderr)
        sys.exit(1)

    print("Fetching articles from NewsAPI...")
    articles = fetch_articles(newsapi_key)
    print(f"Got {len(articles)} articles.")

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key and articles:
        try:
            digest_text = summarize_with_claude(articles, anthropic_key)
            print("Digest written by Claude.")
        except Exception as e:  # noqa: BLE001
            print(f"Claude summarization failed ({e}); using fallback digest.")
            digest_text = fallback_digest(articles)
    else:
        digest_text = fallback_digest(articles) if articles else "No articles found today."
        print("No ANTHROPIC_API_KEY set; using plain headline list as digest.")

    save_to_db(digest_text, articles)
    print(f"Saved digest to {DB_PATH}")


if __name__ == "__main__":
    main()
