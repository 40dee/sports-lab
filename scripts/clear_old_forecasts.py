#!/usr/bin/env python3
"""One-time cleanup of legacy Sports Lab forecasts, grades, and combos.

Dry run by default. Requires --delete and an explicit confirmation phrase.
Never prints DATABASE_URL or database credentials.
"""
import argparse
import os
import sys

NAMESPACES = ("forecast", "grade", "combo")
CONFIRMATION = "DELETE_LEGACY_FORECASTS"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delete", action="store_true", help="Permanently delete target namespaces")
    parser.add_argument("--confirm", default="", help="Required confirmation phrase")
    args = parser.parse_args()

    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        sys.exit("DATABASE_URL is missing; refusing to use a local database.")

    import psycopg

    with psycopg.connect(url, connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.sports_records')")
            if cursor.fetchone()[0] is None:
                sys.exit("sports_records table not found; no changes made.")
            cursor.execute(
                "SELECT namespace, COUNT(*) FROM sports_records "
                "WHERE namespace = ANY(%s) GROUP BY namespace",
                (list(NAMESPACES),),
            )
            counts = dict(cursor.fetchall())
            for namespace in NAMESPACES:
                print(f"{namespace}: {counts.get(namespace, 0)} records")

            if not args.delete:
                print("DRY RUN ONLY. No records deleted.")
                return
            if args.confirm != CONFIRMATION:
                sys.exit("Confirmation phrase incorrect; no records deleted.")

            cursor.execute(
                "DELETE FROM sports_records WHERE namespace = ANY(%s)",
                (list(NAMESPACES),),
            )
            print(f"DELETED {cursor.rowcount} total records across {', '.join(NAMESPACES)}.")
    print("Transaction committed. Disable or remove this workflow after use.")


if __name__ == "__main__":
    main()
