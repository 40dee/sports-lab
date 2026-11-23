#!/usr/bin/env python3
"""Temporary Turso board-state cleanup. Preview by default."""
import argparse
import os
import sys
from urllib.parse import urlparse
import requests

NAMESPACES = ("board", "board_download", "board_run", "board_run_history",
              "health", "preparation", "coverage", "automation")
CONFIRMATION = "DELETE_STALE_BOARD_STATE"

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--delete", action="store_true")
    p.add_argument("--confirm", default="")
    args = p.parse_args()
    if args.delete and args.confirm != CONFIRMATION:
        sys.exit("Invalid confirmation; no records deleted.")
    url = os.getenv("TURSO_DATABASE_URL", "").strip()
    token = os.getenv("TURSO_AUTH_TOKEN", "").strip()
    parsed = urlparse(url.replace("libsql://", "https://", 1))
    if (parsed.scheme != "https" or not parsed.hostname or
        not parsed.hostname.endswith(".turso.io") or parsed.username or
        parsed.password or parsed.query or not token):
        sys.exit("Missing or invalid Turso credentials; no changes made.")
    endpoint = f"https://{parsed.netloc}/v2/pipeline"
    args_sql = [{"type":"text","value":n} for n in NAMESPACES]
    placeholders = ",".join("?" for _ in NAMESPACES)

    def execute(sql, fetch=False):
        r = requests.post(endpoint, headers={"Authorization":"Bearer "+token},
            json={"requests":[{"type":"execute","stmt":{"sql":sql,"args":args_sql,"want_rows":fetch}},{"type":"close"}]},
            timeout=40)
        r.raise_for_status()
        item = r.json()["results"][0]
        if item.get("type") != "ok" or item.get("response",{}).get("type") != "execute":
            raise RuntimeError("Turso rejected statement.")
        return item["response"]["result"]

    try:
        result = execute("SELECT namespace, COUNT(*) FROM sports_records WHERE namespace IN ("+placeholders+") GROUP BY namespace", True)
        counts = {row[0]["value"]:int(row[1]["value"]) for row in result.get("rows",[])}
        for ns in NAMESPACES:
            print(f"{ns}: {counts.get(ns,0)} records")
        print("Total targeted:",sum(counts.values()))
        if not args.delete:
            print("DRY RUN ONLY. No records deleted.")
            return
        execute("DELETE FROM sports_records WHERE namespace IN ("+placeholders+")")
        result = execute("SELECT COUNT(*) FROM sports_records WHERE namespace IN ("+placeholders+")", True)
        remaining = int(result["rows"][0][0]["value"])
        if remaining:
            sys.exit(f"ERROR: {remaining} targeted records remain.")
        print("DELETE VERIFIED: zero records remain in eight targeted namespaces.")
    except (requests.RequestException, KeyError, ValueError, RuntimeError) as exc:
        sys.exit(f"Cleanup failed: {type(exc).__name__}: {exc}")

if __name__ == "__main__":
    main()
