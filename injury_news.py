"""Conservative, pregame-only news interpretation; no network calls."""
import re
import unicodedata
from datetime import datetime, timedelta, timezone

def normalize(value):
    return unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()

def timestamp(value):
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return t.astimezone(timezone.utc) if t.tzinfo else None
    except (ValueError, TypeError):
        return None

def status_for(posts, name, start, now=None):
    now = now or datetime.now(timezone.utc)
    start = timestamp(start)
    if not start or start <= now:
        return None
    name = normalize(name).strip()
    if len(name.split()) < 2:
        return None
    pattern = r"(?<![\w])" + re.escape(name) + r"(?![\w])(?:\s*\([^()]{1,60}\))?\s*(?::\s*|\s+)(?:(?:is|has been|will be)\s+)?(ruled out|out|will not play|won't play|available|active|in|questionable|doubtful|probable|game.time decision|will play)\b"
    matches = []
    for post in posts:
        t = timestamp(post.get("timestamp"))
        if not t or not now-timedelta(hours=24) <= t <= now:
            continue
        text = normalize(post.get("text"))
        # Do not interpret historical, future-game or conditional reports.
        if re.search(r"\b(yesterday|tomorrow|next game|last game|last night|if|could|might|expected|out indefinitely|not ruled out|was out|were out|missed\s+\d+\s+games?)\b", text):
            continue
        # Only accept explicit current-game qualifiers after OUT for.
        if re.search(r"\bout for\b(?!\s+(?:tonight\b|the game\b|today\b))", text):
            continue
        m = re.search(pattern, text)
        if m:
            matches.append((t, m.group(1) in ("ruled out","out","will not play","won't play")))
    if not matches:
        return None
    latest = max(t for t, _ in matches)
    return "OUT" if all(out for t, out in matches if t == latest) else "OTHER"

def read_posts(store):
    try:
        return (store.get("injury_news", "latest") or {}).get("posts") or []
    except Exception:
        return []

def usage_context(posts, player, roster, start):
    position = (player.get("position") or {}).get("abbreviation")
    names = []
    if position in ("PG","SG","SF","PF","C"):
        for mate in roster:
            if not mate.get("id") or str(mate["id"]) == str(player.get("id")):
                continue
            if (mate.get("position") or {}).get("abbreviation") != position:
                continue
            name = mate.get("displayName")
            if status_for(posts, name, start) == "OUT":
                names.append(name)
    return {"out": status_for(posts, player.get("displayName"), start) == "OUT",
            "position": position, "teammates": sorted(set(names)), "factor": 1.10 if names else 1.0}

def apply_usage(result, context, weights=None):
    factor = context.get("factor", 1.0)
    if factor == 1.0:
        return result
    for component in result["components"]:
        if component["Stat"] in ("PTS","REB","AST"):
            component["Projection"] *= factor
            component["News usage factor"] = factor
    result["projection"] = sum(c["Projection"] * (weights or {}).get(c["Stat"], 1.0) for c in result["components"])
    result["warnings"].append("Experimental same-position news usage adjustment: " + ", ".join(context["teammates"]))
    return result

def pass_result(result):
    result["direction"] = "PASS"
    result["selection_allowed"] = False
    result["warnings"] = list(result.get("warnings") or []) + ["Ruled out by news feed."]
    return result
