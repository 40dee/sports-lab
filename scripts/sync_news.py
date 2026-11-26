"""Sync public RSS posts into one isolated Turso news record; never import app."""
import calendar
import json
import os
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlparse
import feedparser
import requests

# Public Bluesky RSS endpoints use stable DID identifiers where known.
FEEDS = (
    "https://bsky.app/profile/did:plc:ihxhbkzgvr2wzja4rlit6pnn/rss",  # Underdog NBA
    "https://bsky.app/profile/did:plc:sa73fbx3vxzh2fw5pu5wmief/rss",  # Shams mirror
    "https://bsky.app/profile/did:plc:ykcnnf6kqnn3lasmobrczoem/rss",  # NBA Daily News mirror
)

class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
    def handle_data(self, data):
        self.parts.append(data)

def plain(value):
    parser = PlainText()
    parser.feed(str(value or ""))
    return " ".join(" ".join(parser.parts).split())[:4000]

def query(sql, values, fetch=False):
    url = urlparse(os.environ["TURSO_DATABASE_URL"].replace("libsql://", "https://", 1))
    if url.scheme != "https" or not url.hostname or not url.hostname.endswith(".turso.io") or url.username or url.password:
        raise ValueError("Invalid Turso URL")
    response = requests.post("https://" + url.netloc + "/v2/pipeline",
        headers={"Authorization": "Bearer " + os.environ["TURSO_AUTH_TOKEN"]},
        json={"requests": [{"type": "execute", "stmt": {"sql": sql,
            "args": [{"type": "text", "value": v} for v in values], "want_rows": fetch}},
            {"type": "close"}]}, timeout=20)
    response.raise_for_status()
    item = response.json()["results"][0]
    if item.get("type") != "ok" or item.get("response", {}).get("type") != "execute":
        raise RuntimeError("Turso statement rejected")
    return item["response"]["result"].get("rows", [])

def main():
    posts = {}
    successful = 0
    for url in FEEDS:
        try:
            response = requests.get(url, timeout=12, headers={"User-Agent": "SportsLabNews/1.0"})
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            if feed.bozo and not feed.entries:
                raise ValueError("Invalid RSS")
            if not feed.version:
                raise ValueError("Not an RSS feed")
            successful += 1
            for entry in feed.entries:
                link = str(entry.get("link", ""))
                parsed = urlparse(link)
                date = entry.get("published_parsed") or entry.get("updated_parsed")
                text = plain(entry.get("summary") or entry.get("title"))
                if parsed.scheme != "https" or parsed.hostname != "bsky.app" or not date or not text:
                    continue
                timestamp = datetime.fromtimestamp(calendar.timegm(date), timezone.utc).isoformat()
                posts[link] = {"text": text, "link": link, "timestamp": timestamp}
            print("Feed read:", url, len(feed.entries))
        except Exception as exc:
            print("Feed unavailable:", url, type(exc).__name__)
    if not successful or not posts:
        raise SystemExit("No usable fresh feed posts; previous snapshot preserved.")
    rows = query("SELECT payload FROM sports_records WHERE namespace=? AND key=?", ["injury_news", "latest"], True)
    if rows:
        previous = json.loads(rows[0][0]["value"])
        for post in previous.get("posts", []):
            posts.setdefault(post["link"], post)
    latest = sorted(posts.values(), key=lambda p: p["timestamp"], reverse=True)[:10]
    stamp = datetime.now(timezone.utc).isoformat()
    payload = json.dumps({"posts": latest, "synced_at": stamp, "feeds_ok": successful, "feeds_total": len(FEEDS)})
    query("INSERT INTO sports_records (namespace,key,payload,updated) VALUES (?,?,?,?) ON CONFLICT(namespace,key) DO UPDATE SET payload=excluded.payload,updated=excluded.updated",
          ["injury_news", "latest", payload, stamp])
    print("Saved", len(latest), "posts from", successful, "of", len(FEEDS), "feeds")

if __name__ == "__main__":
    main()
