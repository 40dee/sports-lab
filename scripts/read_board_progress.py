import json, os
from datetime import datetime, timezone
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

def main():
    day = datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
    parsed = urlsplit(os.getenv("TURSO_DATABASE_URL", "").replace("libsql://", "https://", 1))
    token = os.getenv("TURSO_AUTH_TOKEN", "")
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".turso.io") or not token:
        print(json.dumps({"state": "Database connection not configured for this read-only check"}))
        return 1
    sql = """SELECT namespace,key,payload,updated FROM sports_records
        WHERE (namespace='preparation' AND key='last')
        OR (namespace IN ('board_run','board_download','coverage','automation') AND key=?)
        OR (namespace='board' AND key='board')
        LIMIT 6"""
    body = json.dumps({"requests": [
        {"type":"execute","stmt":{"sql":sql,"args":[{"type":"text","value":day}],"want_rows":True}},
        {"type":"close"}]}).encode()
    data = None
    for attempt in range(2):
        try:
            request = Request("https://" + parsed.netloc + "/v2/pipeline", data=body,
                headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"})
            with urlopen(request, timeout=20) as response:
                reply = json.load(response)
            result = reply["results"][0]
            if result.get("type") != "ok":
                print(json.dumps({"state":"Database read rejected; no records changed"}))
                return 1
            data = result["response"]["result"].get("rows", [])
            break
        except Exception:
            if attempt:
                print(json.dumps({"state":"Database read unavailable after two attempts; no records changed"}))
                return 1
    records = {}
    times = {}
    for row in data:
        namespace, key, payload, updated = [cell.get("value") for cell in row]
        records[namespace] = json.loads(payload)
        times[namespace] = updated
    prep = records.get("preparation", {})
    run = records.get("board_run", {})
    download = records.get("board_download", {})
    ledger = records.get("automation", {})
    coverage = records.get("coverage", {})
    report = {
        "read_at": datetime.now(timezone.utc).isoformat(), "slate_day": day,
        "worker_stage": run.get("stage"), "worker_started": run.get("started_at"),
        "import_complete": run.get("import_complete"),
        "download": {key:download.get(key) for key in ("games_done","games_total","lines","state")},
        "preparation": {key:prep.get(key) for key in ("run_at","ready","total","pending","unavailable","attempted","state","current","finished_at","elapsed_seconds","newly_ready")},
        "progress_last_saved": times.get("preparation"),
        "coverage": {key:coverage.get(key) for key in ("ready","imported","upcoming_pending","upcoming_unavailable","missed_start","checked_at")},
        "last_completed_pass": {key:(ledger.get("preparation") or {}).get(key) for key in ("ready","total","pending","unavailable","finished_at")},
        "provider_capture_complete": (ledger.get("fallback") or {}).get("complete")
    }
    games = {}
    for card in (records.get("board", {}).get("v4_rows") or []):
        game = str(card.get("Game", "Unknown"))
        item = games.setdefault(game, {"lines": 0, "fantasy": 0, "latest_quote": ""})
        item["lines"] += 1
        item["fantasy"] += int("fantasy" in str(card.get("Stat", "")).lower() or "fantasy" in str(card.get("Stat Key", "")).lower())
        item["latest_quote"] = max(item["latest_quote"], str(card.get("Odds checked (UTC)") or ""))
    report["games"] = games
    print("BOARD_PROGRESS_JSON " + json.dumps(report, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
