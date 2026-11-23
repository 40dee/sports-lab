#!/usr/bin/env python3
"""One-time Turso cleanup of legacy Sports Lab forecasts and combo records.

Preview by default. Requires --delete and an explicit confirmation phrase.
Only six named namespaces are affected; never prints credentials.
"""
import argparse
import os
import sys
from urllib.parse import urlparse

import requests

NAMESPACES = (
    "forecast", "grade", "combo", "combo_grade",
    "combo_suggestions", "combo_cohort",
)
CONFIRMATION = "DELETE_LEGACY_FORECASTS"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delete", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()

    url = os.getenv("TURSO_DATABASE_URL", "").strip()
    token = os.getenv("TURSO_AUTH_TOKEN", "").strip()
    parsed = urlparse(url.replace("libsql://", "https://", 1))
    if (parsed.scheme != "https" or not parsed.hostname
            or not parsed.hostname.endswith(".turso.io")
            or parsed.username or parsed.password or parsed.query):
        sys.exit("Valid TURSO_DATABASE_URL is required; no changes made.")
    if not token:
        sys.exit("TURSO_AUTH_TOKEN is missing; no changes made.")
    if args.delete and args.confirm != CONFIRMATION:
        sys.exit("Confirmation phrase incorrect; no changes made.")

    endpoint = "https://" + parsed.netloc + "/v2/pipeline"
    placeholders = ",".join("?" for _ in NAMESPACES)
    arguments = [{"type": "text", "value": ns} for ns in NAMESPACES]

    def execute(sql, fetch=False):
        payload = {
            "requests": [
                {"type": "execute", "stmt": {
                    "sql": sql, "args": arguments, "want_rows": fetch
                }},
                {"type": "close"},
            ]
        }
        response = requests.post(
            endpoint, headers={"Authorization": "Bearer " + token},
            json=payload, timeout=30,
        )
        response.raise_for_status()
        item = response.json()["results"][0]
        if item.get("type") != "ok" or item.get("response", {}).get("type") != "execute":
            raise RuntimeError("Turso rejected the SQL statement.")
        return item["response"]["result"]

    try:
        sql = ("SELECT namespace, COUNT(*) FROM sports_records "
               f"WHERE namespace IN ({placeholders}) GROUP BY namespace")
        before = execute(sql, fetch=True)
        counts = {
            row[0]["value"]: int(row[1]["value"])
            for row in before.get("rows", [])
        }
        for namespace in NAMESPACES:
            print(f"{namespace}: {counts.get(namespace, 0)} records")
        if not args.delete:
            print("DRY RUN ONLY. No records deleted.")
            return

        execute(f"DELETE FROM sports_records WHERE namespace IN ({placeholders})")
        remaining = execute(
            f"SELECT COUNT(*) FROM sports_records WHERE namespace IN ({placeholders})",
            fetch=True,
        )
        left = int(remaining["rows"][0][0]["value"])
        if left:
            raise RuntimeError(f"Cleanup incomplete: {left} targeted records remain.")
        print("DELETE VERIFIED: zero records remain in all six targeted namespaces.")
        print("Disable or remove this workflow and script after use.")
    except (requests.RequestException, KeyError, ValueError, RuntimeError) as error:
        sys.exit(f"Turso cleanup failed: {type(error).__name__}: {error}")


if __name__ == "__main__":
    main()
