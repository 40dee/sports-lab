"""NBA workflow: durable records, forecasts, directional audits and paper ROI.
No function in this section requests OddsPAPI or places an order.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
import sqlite3
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

import numpy as np
import pandas as pd


def now():
    return datetime.now(timezone.utc).isoformat()


def json_safe(value):
    """Represent missing optional data as JSON null, never as NaN or zero."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return json_safe(value.tolist())
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Decimal) and not value.is_finite():
        return None
    return value


def validate_forecast_numbers(card, result):
    """Never freeze an invalid calculation as a real prediction."""
    fields = {"prop line": card.get("Line")}
    fields.update({name: result.get(name) for name in (
        "projection", "line", "gap", "minutes"
    )})
    for name, value in fields.items():
        try:
            valid = value is not None and math.isfinite(float(value))
        except (TypeError, ValueError, OverflowError):
            valid = False
        if not valid:
            raise ValueError(f"Missing or invalid {name}; forecast not saved.")


def encode(value):
    return json.dumps(
        json_safe(value),
        default=str,
        sort_keys=True,
        allow_nan=False,
    )


def identity(card):
    fields = (
        "Fixture ID",
        "Platform",
        "Player",
        "Stat Key",
        "Period",
        "Line",
        "Line Type",
    )
    values = [str(card.get(field)) for field in fields]
    return hashlib.sha256(encode(values).encode()).hexdigest()


class Store:
    """Use PostgreSQL when configured; otherwise use local SQLite."""

    def __init__(
        self,
        path="sports_lab_workflow.sqlite3",
        url=None,
    ):
        self.path = str(path)
        self.url = url or os.getenv("DATABASE_URL", "")
        self.remote = bool(self.url)
        # Read-through cache is scoped to one Streamlit browser session.
        # Never persists data outside Neon and never replaces the database.
        self._read_cache = None
        if self.remote:
            try:
                self._read_cache = st.session_state.setdefault("_neon_read_cache_v1", {})
            except Exception:
                pass

        with self.connection() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS sports_records ("
                "namespace TEXT NOT NULL, "
                "key TEXT NOT NULL, "
                "payload TEXT NOT NULL, "
                "updated TEXT NOT NULL, "
                "PRIMARY KEY(namespace,key))"
            )

    @contextmanager
    def connection(self):
        if self.remote:
            import psycopg

            con = psycopg.connect(
                self.url,
                connect_timeout=3,
            )
        else:
            con = sqlite3.connect(
                self.path,
                timeout=30,
            )
            con.execute("PRAGMA busy_timeout=30000")

        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def query(self, sql, values=(), fetch=False):
        with self.connection() as con:
            statement = (
                sql.replace("?", "%s")
                if self.remote
                else sql
            )
            cursor = con.execute(statement, values)
            return cursor.fetchall() if fetch else None

    def put(
        self,
        namespace,
        key,
        value,
        immutable=False,
    ):
        # Immutable source snapshots are often shared by hundreds of props.
        # A successful earlier read/write proves this insert is unnecessary.
        if immutable and self._read_cache is not None:
            cached = self._read_cache.get(("get", namespace, key))
            listed = self._read_cache.get(("items", namespace, None))
            if (cached is not None and cached[1] is not None) or (listed is not None and key in dict(listed[1])):
                return
        suffix = (
            "DO NOTHING"
            if immutable
            else (
                "DO UPDATE SET "
                "payload=excluded.payload, "
                "updated=excluded.updated"
            )
        )

        written = self.query(
            "INSERT INTO sports_records VALUES (?,?,?,?) "
            "ON CONFLICT(namespace,key) " + suffix + " RETURNING key",
            (namespace, key, encode(value), now()), fetch=True,
        )
        # Update this record in cached lists. A single new forecast must not
        # force a download of every historic forecast on the next render.
        if self._read_cache is not None:
            import copy
            stamp = time.monotonic()
            if written:
                self._read_cache[("get", namespace, key)] = (stamp, json.loads(encode(value)))
                list_key = ("items", namespace, None)
                cached = self._read_cache.get(list_key)
                if cached is not None:
                    values = dict(cached[1])
                    values[key] = json.loads(encode(value))
                    # Preserve the original refresh deadline for other workers.
                    self._read_cache[list_key] = (cached[0], list(values.items()))
            else:
                self._read_cache.pop(("get", namespace, key), None)
                cached = self._read_cache.get(("items", namespace, None))
                if cached and key not in dict(cached[1]):
                    self._read_cache.pop(("items", namespace, None), None)
            while len(self._read_cache) > 128:
                self._read_cache.pop(next((k for k in self._read_cache if k[0] == "get"), next(iter(self._read_cache))))

    def _cached_read(self, kind, namespace, key, loader):
        if self._read_cache is None:
            return loader()
        import copy
        cache_key = (kind, namespace, key)
        cached = self._read_cache.get(cache_key)
        stamp = time.monotonic()
        if cached is not None and stamp - cached[0] < 120:
            return copy.deepcopy(cached[1])
        result = loader()
        self._read_cache[cache_key] = (stamp, copy.deepcopy(result))
        while len(self._read_cache) > 128:
            self._read_cache.pop(next((k for k in self._read_cache if k[0] == "get"), next(iter(self._read_cache))))
        return result

    def get(self, namespace, key, default=None):
        if self._read_cache is not None:
            snapshot = self._read_cache.get(("items", namespace, None))
            if snapshot is not None and time.monotonic() - snapshot[0] < 120:
                import copy
                return copy.deepcopy(dict(snapshot[1]).get(key, default))
        def load():
            rows = self.query(
                "SELECT payload FROM sports_records "
                "WHERE namespace=? AND key=?",
                (namespace, key), True,
            )
            return json.loads(rows[0][0]) if rows else None
        value = self._cached_read("get", namespace, key, load)
        return value if value is not None else default

    def items(self, namespace):
        def load():
            rows = self.query(
                "SELECT key,payload FROM sports_records "
                "WHERE namespace=? ORDER BY updated,key",
                (namespace,), True,
            )
            return [(key, json.loads(payload)) for key, payload in rows]
        return self._cached_read("items", namespace, None, load)

    def export(self):
        rows = self.query(
            "SELECT namespace,key,payload,updated "
            "FROM sports_records ORDER BY namespace,key",
            fetch=True,
        )

        return encode({
            "format": "sports-lab-workspace-v2",
            "records": [list(row) for row in rows],
        })

    def restore(self, value):
        if (
            value.get("format") != "sports-lab-workspace-v2"
            or not isinstance(value.get("records"), list)
        ):
            raise ValueError("Unsupported workspace backup.")

        allowed = {
            "watchlist",
            "board",
            "forecast",
            "grade",
            "model",
            "model_review",
            "ticket",
            "maintenance",
            "legacy",
            "source",
            "paper_forecast",
            "paper_settlement",
            "preparation",
            "calculation_status",
            "grade_run",
            "automation",
            "audit_error",
            "health",
            "coverage",
            "input_health",
            "request_budget",
        }

        checked = []

        for row in value["records"]:
            if (
                not isinstance(row, list)
                or len(row) != 4
                or row[0] not in allowed
            ):
                raise ValueError("Invalid backup row.")

            namespace, key, payload, stamp = row

            if (
                not all(isinstance(item, str) for item in row)
                or len(key) > 300
            ):
                raise ValueError("Invalid backup fields.")

            parsed = json.loads(payload)

            if not isinstance(parsed, dict) and not (
                namespace == 'request_budget' and type(parsed) is int and parsed >= 0
            ):
                raise ValueError("Invalid record payload.")

            pd.to_datetime(
                stamp,
                utc=True,
                errors="raise",
            )

            checked.append((
                namespace,
                key,
                encode(parsed),
                stamp,
            ))

        with self.connection() as con:
            sql = (
                "INSERT INTO sports_records VALUES (?,?,?,?) "
                "ON CONFLICT(namespace,key) DO NOTHING"
            )

            if self.remote:
                sql = sql.replace("?", "%s")

            for row in checked:
                con.execute(sql, row)

        return len(checked)


def fee_estimate(
    price,
    count,
    multiplier,
    precision="0.0001",
):
    """Estimate one all-taker fill with explicit balance rounding."""

    price = Decimal(str(price))
    count = Decimal(str(count))
    multiplier = Decimal(str(multiplier))
    grid = Decimal(precision)

    if not all(
        value.is_finite()
        for value in (price, count, multiplier, grid)
    ):
        raise ValueError("Fee inputs must be finite.")

    if (
        not 0 < price < 1
        or count <= 0
        or count != count.to_integral_value()
        or multiplier < 0
        or grid not in (Decimal(".0001"), Decimal(".01"))
    ):
        raise ValueError("Invalid fee inputs.")

    trade_fee = (
        multiplier
        * Decimal(".07")
        * count
        * price
        * (1 - price)
    ).quantize(
        Decimal(".000001"),
        rounding=ROUND_CEILING,
    )

    cost = count * price

    total = (
        ((cost + trade_fee) / grid)
        .to_integral_value(rounding=ROUND_CEILING)
        * grid
    )

    return {
        "fee": float(total - cost),
        "total": float(total),
        "break_even": float(total / count),
    }


def kalshi_fee_metadata(fetch, ticker):
    market = fetch("markets/" + ticker).get("market", {})

    if (
        market.get("ticker") != ticker
        or not market.get("event_ticker")
    ):
        raise ValueError(
            "Exact contract event could not be verified."
        )

    event_ticker = market["event_ticker"]
    series_ticker = ticker.split("-", 1)[0]

    metadata = fetch(
        "series/" + series_ticker
    ).get("series", {})

    if metadata.get("ticker") != series_ticker:
        raise ValueError(
            "Series fees could not be verified."
        )

    kind = metadata.get("fee_type")
    multiplier = metadata.get("fee_multiplier")
    changes = []
    cursor = ""

    for _ in range(20):
        params = {
            "event_ticker": event_ticker,
            "limit": 1000,
        }

        if cursor:
            params["cursor"] = cursor

        page = fetch("events/fee_changes", params)

        if not isinstance(
            page.get("event_fee_changes"),
            list,
        ):
            raise ValueError(
                "Event fee overrides could not be verified."
            )

        changes.extend(
            change
            for change in page["event_fee_changes"]
            if change.get("event_ticker") == event_ticker
        )

        cursor = page.get("cursor", "")

        if not cursor:
            break

    if cursor:
        raise ValueError(
            "Event fee pagination incomplete."
        )

    effective = [
        change
        for change in changes
        if pd.to_datetime(
            change["scheduled_ts"],
            utc=True,
        ) <= pd.Timestamp.now(tz="UTC")
    ]

    if effective:
        latest = max(
            effective,
            key=lambda change: pd.to_datetime(
                change["scheduled_ts"],
                utc=True,
            ),
        )

        kind = latest.get("fee_type_override") or kind

        if latest.get("fee_multiplier_override") is not None:
            multiplier = latest["fee_multiplier_override"]

    if (
        kind not in (
            "quadratic",
            "quadratic_with_maker_fees",
        )
        or multiplier is None
    ):
        raise ValueError("This fee model is not mapped.")

    if (
        not math.isfinite(float(multiplier))
        or float(multiplier) < 0
    ):
        raise ValueError("Invalid fee multiplier.")

    return {
        "type": kind,
        "multiplier": float(multiplier),
        "checked_at": now(),
    }


def completed_actual(summary, player_id, columns):
    header = summary.get("header", {})
    competitions = header.get("competitions", [])

    if (
        len(competitions) != 1
        or not competitions[0]
        .get("status", {})
        .get("type", {})
        .get("completed")
    ):
        return None

    appearances = []

    for group in summary.get("boxscore", {}).get("players", []):
        for section in group.get("statistics", []):
            labels = section.get("labels", [])

            for athlete in section.get("athletes", []):
                returned_id = (
                    athlete.get("athlete", {}).get("id")
                )

                if str(returned_id) != str(player_id):
                    continue

                if athlete.get("didNotPlay"):
                    appearances.append({
                        "status": "DNP",
                        "actual": None,
                    })
                    continue

                values = athlete.get("stats", [])

                if len(labels) != len(values):
                    continue

                stats = dict(zip(labels, values))
                minute_parts = str(
                    stats.get("MIN", "")
                ).split(":")

                try:
                    minutes = float(minute_parts[0])

                    if len(minute_parts) == 2:
                        minutes += float(minute_parts[1]) / 60

                    if minutes <= 0:
                        appearances.append({
                            "status": "DNP",
                            "actual": None,
                        })
                        continue

                    numeric = {}

                    for column in columns:
                        source_column = {
                            "TOV": "TO",
                            "FG3M": "3PT",
                        }.get(column, column)

                        raw = stats.get(source_column)

                        if column == "FG3M":
                            raw = str(raw).split("-")[0]

                        number = float(raw)

                        if (
                            not math.isfinite(number)
                            or number < 0
                        ):
                            raise ValueError("Missing stat.")

                        numeric[column] = number

                    appearances.append({
                        "status": "FINAL",
                        "actual": sum(numeric.values()),
                        "minutes": minutes,
                        "stats": numeric,
                    })

                except (ValueError, TypeError):
                    continue

    unique = {
        encode(appearance): appearance
        for appearance in appearances
    }

    return (
        next(iter(unique.values()))
        if len(unique) == 1
        else None
    )


def espn_injury_player_id(athlete):
    """ESPN injuries may expose identity only in official player links."""
    import re
    from urllib.parse import urlparse
    if athlete.get('id') is not None:
        return str(athlete['id'])
    ids = set()
    for link in athlete.get('links') or []:
        parsed = urlparse(str(link.get('href') or ''))
        if parsed.hostname not in ('espn.com', 'www.espn.com'):
            continue
        match = re.search(r'/nba/player/(?:[^/]+/)*id/(\d+)(?:/|$)', parsed.path)
        if match:
            ids.add(match.group(1))
    return next(iter(ids)) if len(ids) == 1 else None


def roster_name_key(name):
    """Allow missing generational suffixes only inside a verified matchup."""
    import re
    import unicodedata
    name = str(name or '').strip()
    if ',' in name:
        last, first = name.split(',', 1)
        name = first.strip() + ' ' + last.strip()
    name = re.sub(r'\s+(?:jr\.?|sr\.?|ii|iii|iv)\s*$', '', name, flags=re.I)
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]', '', name)


class Workflow:
    def __init__(self, engine, store):
        self.e = engine
        self.store = store

    def event_for(self, card):
        start = pd.to_datetime(
            card["Start (UTC)"],
            utc=True,
        )

        date = start.tz_convert(
            "America/New_York"
        ).strftime("%Y%m%d")

        board = self.e["saved_fetch"](
            self.e["ESPN_BASE"] + "scoreboard",
            {"dates": date},
            ttl_seconds=300,
        )

        team_key = self.e["engine_team_key"]

        wanted = {
            team_key(name)
            for name in str(card["Game"]).split(" vs ")
        }

        events = []

        for event in board.get("events", []):
            stamp = pd.to_datetime(
                event.get("date"),
                utc=True,
                errors="coerce",
            )

            if (
                pd.isna(stamp)
                or abs((stamp - start).total_seconds()) > 900
            ):
                continue

            for competition in event.get("competitions", []):
                competitors = competition.get(
                    "competitors",
                    [],
                )

                returned_teams = {
                    team_key(
                        item.get("team", {}).get(
                            "displayName",
                            "",
                        )
                    )
                    for item in competitors
                }

                if (
                    len(competitors) == 2
                    and returned_teams == wanted
                ):
                    events.append((event, competition))

        if len(events) != 1:
            raise ValueError(
                "No unique ESPN fixture matches "
                "the teams and start time."
            )

        return events[0]

    def player(self, card):
        team_key = self.e["engine_team_key"]
        normalize = self.e["normalize_player"]

        names = {
            team_key(name)
            for name in card["Game"].split(" vs ")
        }

        teams = [
            team
            for team in self.e["espn_teams"]()
            if team_key(team["displayName"]) in names
        ]

        if len(teams) != 2:
            raise ValueError(
                "Matchup team identities are unavailable."
            )

        matches = []
        suffix_matches = []

        for team in teams:
            url = (
                self.e["ESPN_BASE"]
                + f"teams/{team['id']}/roster"
            )

            roster = self.e["saved_fetch"](
                url,
                {},
                ttl_seconds=300,
            )

            for athlete in roster.get("athletes", []):
                if roster_name_key(athlete.get('displayName', '')) == roster_name_key(card['Player']):
                    suffix_matches.append((team, athlete, self.e['cached_response_time'](url, {})))
                if normalize(
                    athlete.get("displayName", "")
                ) == normalize(card["Player"]):
                    matches.append((
                        team,
                        athlete,
                        self.e["cached_response_time"](
                            url,
                            {},
                        ),
                    ))

        if not matches:
            matches = suffix_matches
        if len(matches) != 1:
            raise ValueError(
                "Player does not uniquely match "
                "a current matchup roster."
            )

        return matches[0]

    def availability(self, player_id, team_id):
        try:
            url = self.e["ESPN_BASE"] + "injuries"

            data = self.e["saved_fetch"](
                url,
                {},
                ttl_seconds=60,
            )

            if not isinstance(data.get("injuries"), list):
                raise ValueError("Invalid injury feed.")

            entries = []

            for group in data["injuries"]:
                for injury in group.get("injuries", []):
                    if str(group.get('id')) != str(team_id):
                        continue
                    returned_id = espn_injury_player_id(injury.get('athlete') or {})

                    if returned_id == str(player_id):
                        entries.append(injury)

            return {
                "entries": entries,
                "identity_parser": 2,
                "checked_at": now(),
                "fetched_at": self.e[
                    "cached_response_time"
                ](url, {}),
                "available": True,
            }

        except Exception:
            return {
                "entries": [],
                "available": False,
                "checked_at": now(),
            }

    def enrich(
        self,
        result,
        history,
        card,
        player,
        event_id,
        context,
        availability,
    ):
        result = dict(result)

        if any(
            str(entry.get("status", "")).lower()
            in ("out", "suspended")
            for entry in availability.get("entries", [])
        ):
            raise ValueError(
                "Player is listed out or suspended; "
                "no playable projection."
            )

        components = result["components"]

        weights = self.e.get(
            "fantasy_weights",
            lambda record: None,
        )(card) or {}

        baseline = sum(
            float(component["Expected minutes"])
            * float(component["Per minute"])
            * weights.get(component["Stat"], 1.0)
            for component in components
        )

        adjusted = float(result["projection"])

        stat_key = (
            "PrizePicks Fantasy"
            if weights
            else "+".join(sorted(
                self.e["market_columns"](card)
            ))
        )

        model = self.store.get(
            "model",
            stat_key,
            {
                "alpha": 1.0,
                "version": "initial-v1",
            },
        )

        alpha = float(model["alpha"])

        forecast = max(
            0.0,
            baseline + alpha * (adjusted - baseline),
        )

        line = float(card["Line"])

        result.update(
            baseline=baseline,
            matchup_projection=adjusted,
            projection=forecast,
            direction="MORE" if forecast > line else "LESS",
            gap=forecast - line,
            tie=abs(forecast - line) < 1e-9,
            learning_model=model,
            stat_group=stat_key,
            event_id=str(event_id),
            player_id=str(player["id"]),
            game_context=context,
            availability=availability,
        )

        columns = self.e["market_columns"](card)

        sample = history.copy()
        sample["GAME_DATE"] = pd.to_datetime(
            sample["GAME_DATE"],
            utc=True,
        )

        current = sample[
            (
                sample["SEASON"]
                == self.e["current_season"]()
            )
            & (
                sample["GAME_DATE"]
                < pd.Timestamp.now(tz="UTC")
            )
        ].sort_values(
            "GAME_DATE",
            ascending=False,
        ).head(15)

        if "score_frame" in self.e:
            values = self.e["score_frame"](
                current,
                card,
            ).dropna()
        else:
            values = current[columns].apply(
                pd.to_numeric,
                errors="coerce",
            ).sum(
                axis=1,
                min_count=len(columns),
            ).dropna()

        spread = (
            float(values.std(ddof=1))
            if len(values) > 1
            else 0.0
        )

        result["standardized_gap"] = (
            abs(result["gap"]) / spread
            if spread > 1e-9
            else None
        )

        result["historical_more_rate"] = (
            float((values > line).mean())
            if len(values)
            else None
        )

        result["ranking_games"] = len(values)

        # Experimental estimate, saved before the game.
        # The calibration audit measures its actual hit rate.
        if len(values) >= 3:
            shifted = (
                values
                - float(values.mean())
                + forecast
            )

            above = int((shifted > line).sum())
            below = int((shifted < line).sum())

            wins = (
                above
                if result["direction"] == "MORE"
                else below
            )

            result["estimated_hit_probability"] = (
                (wins + 1)
                / (above + below + 2)
            )

            result["probability_sample_games"] = len(values)

            result["probability_method"] = (
                "experimental-centered-history-v1"
            )

            result["probability_note"] = (
                "Uncalibrated shifted recent-game distribution; "
                "conditional on playing and no tie. "
                "Saved before the game. The percentage tracker "
                "measures how these estimates actually perform."
            )

        for source_field in (
            "advanced",
            "position_data",
            "games",
        ):
            source_value = result.get(source_field)

            if source_value is not None:
                source_id = hashlib.sha256(
                    encode(source_value).encode()
                ).hexdigest()

                self.store.put(
                    "source",
                    source_id,
                    {"data": source_value},
                    immutable=True,
                )

                result[source_field] = {
                    "source_ref": source_id,
                }

        result["warnings"] = list(
            result.get("warnings", [])
        )

        if not availability["available"]:
            result["warnings"].append(
                "Injury report unavailable; "
                "playing status is unverified."
            )

        elif not availability["entries"]:
            result["warnings"].append('No matching injury entry returned; this does not confirm playing or starting.')
        elif availability["entries"]:
            statuses = ", ".join(
                str(entry.get("status", "Unknown"))
                for entry in availability["entries"]
            )

            result["warnings"].append(
                "Injury designation: " + statuses
            )

        result["warnings"].append(
            "Blowout spread is context. No arbitrary minutes "
            "deduction is applied; injured teammate usage and "
            "minutes restrictions require verified inputs."
        )

        offered = card.get("Available Sides")
        if card.get("Platform") == "prizepicks" and offered is not None:
            result["selection_allowed"] = result["direction"] in offered
        else:
            result["selection_allowed"] = not (
                card.get("Platform") == "prizepicks"
                and card.get("Line Type") in ("Goblin", "Demon")
                and result["direction"] == "LESS"
            )

        if not result["selection_allowed"]:
            result["warnings"].append(
                "LESS is not selectable on this PrizePicks "
                "tier. This is a directional forecast, "
                "not an eligible entry."
            )

        return result

    def freeze(
        self,
        card,
        result,
        season,
        snapshot=None,
    ):
        validate_forecast_numbers(card, result)
        event, competition = self.event_for(card)

        start = pd.to_datetime(
            card["Start (UTC)"],
            utc=True,
        )

        state = competition.get(
            "status",
            event.get("status", {}),
        ).get("type", {}).get("state")

        if (
            start <= pd.Timestamp.now(tz="UTC")
            or state != "pre"
        ):
            raise ValueError(
                "Fixture is no longer pregame; "
                "no new record saved."
            )

        key = identity(card)
        signature = market_signature(card, str(event["id"]))
        existing = next((frozen for _, frozen in
            unique_forecasts(self._preparation_existing if getattr(self, "_preparation_existing", None) is not None else self.store.items("forecast"))
            if market_signature(frozen["card"], frozen.get("event_id")) == signature), None)
        if existing is not None:
            return existing

        value = {
            "id": key,
            "created_at": now(),
            "card": dict(card),
            "season": season,
            "result": result,
            "event_id": str(event["id"]),
            "snapshot": snapshot or {},
            "odds_scan_at": self.e[
                "st"
            ].session_state.get("v4_scan_at"),
        }

        self.store.put(
            "forecast",
            key,
            value,
            immutable=True,
        )

        frozen = self.store.get("forecast", key)
        self.record_paper_forecast(frozen)

        return frozen

    def calculate(self, card, season):
        event, competition = self.event_for(card)

        state = competition.get(
            "status",
            event.get("status", {}),
        ).get("type", {}).get("state")

        if state != "pre":
            raise ValueError(
                "Fixture is no longer pregame."
            )

        team, player, roster_time = self.player(card)

        availability = self.availability(
            player["id"],
            team["id"],
        )

        statuses = {
            str(entry.get("status", "")).strip().lower()
            for entry in availability["entries"]
        }

        if statuses & {"out", "suspended"}:
            raise ValueError(
                "Player is listed out or suspended; "
                "no playable projection."
            )

        history = self.e["fetch_player_history"](
            str(player["id"]),
            player["displayName"],
            season,
        )

        position, evidence = self.e["engine_position"](
            team,
            player,
            season,
        )

        optional = []

        for name, arguments in (
            ("load_team_advanced", (season,)),
            ("load_position_defense", ()),
        ):
            try:
                optional.append(
                    self.e[name](*arguments)
                )
            except Exception:
                optional.append(None)

        result = self.e["calculate_player_projection"](
            history,
            card,
            season,
            team["displayName"],
            position,
            *optional,
        )
        result.setdefault('warnings', []).extend(history.attrs.get('history_warnings', []))

        context = self.e["engine_game_context"](card)

        result = self.enrich(
            result,
            history,
            card,
            player,
            event["id"],
            context,
            availability,
        )

        result["position_evidence"] = evidence
        result["market"] = self.e["engine_no_vig"](card)

        if card.get("Platform") == "kalshi":
            try:
                result["kalshi"] = self.e[
                    "native_kalshi_quote"
                ](card)
            except Exception as error:
                result["kalshi"] = {
                    "available": False,
                    "reason": str(error),
                }

        return self.freeze(
            card,
            result,
            season,
            {
                "roster": player,
                "roster_fetched_at": roster_time,
            },
        )

    def board(self, rows, season, progress=None):
        unique = {}
        for card in rows:
            if card.get("Decoded"):
                unique.setdefault(market_signature(card), card)
        saved_index = forecast_index(self.store.items("forecast"))

        outcomes = []

        for index, (key, card) in enumerate(unique.items()):
            try:
                saved = saved_prediction(saved_index, card)

                value = saved or self.calculate(card, season)
                result = value["result"]

                future = (
                    pd.to_datetime(
                        card["Start (UTC)"],
                        utc=True,
                    )
                    > pd.Timestamp.now(tz="UTC")
                )

                outcomes.append({
                    "Player": card["Player"],
                    "Platform": card["Platform"],
                    "Stat": card["Stat"],
                    "Line": float(card["Line"]),
                    "Pick": result["direction"],
                    "Projection": result["projection"],
                    "Ranking score": result.get(
                        "standardized_gap"
                    ),
                    "Games": result.get("ranking_games"),
                    "Start (UTC)": card["Start (UTC)"],
                    "Forecast ID": value["id"],
                    "Eligible side": result.get(
                        "selection_allowed",
                        True,
                    ),
                    "Status": (
                        "Frozen"
                        if future
                        else "Started / historical forecast"
                    ),
                })

            except Exception as error:
                outcomes.append({
                    "Player": card["Player"],
                    "Platform": card["Platform"],
                    "Stat": card["Stat"],
                    "Line": card["Line"],
                    "Status": str(error),
                    "Forecast ID": key,
                })

            if progress:
                progress(
                    (index + 1) / max(1, len(unique))
                )

        return outcomes

    def grade(self, limit=100, time_budget=120, force_retry=False):
        """Grade pregame forecasts fairly; keep failures inspectable and retryable."""
        counts = {"graded": 0, "pending": 0, "errors": 0, "DNP": 0,
                  "ties": 0, "attempted": 0, "deferred": 0, "remaining": 0,
                  "future_start": 0, "invalid_start": 0, "late_excluded": 0,
                  "earliest_future_start": None, "latest_future_start": None,
                  "due": 0}
        settled = {key for key, _ in self.store.items("grade")}
        records = [(key, frozen) for key, frozen in unique_forecasts(self.store.items("forecast"))
                   if key not in settled]
        current = pd.Timestamp.now(tz="UTC")
        due = []
        for key, frozen in records:
            try:
                start = pd.to_datetime(frozen["card"]["Start (UTC)"], utc=True)
                if start > current:
                    counts["pending"] += 1
                    counts["future_start"] += 1
                    iso = start.isoformat()
                    if counts["earliest_future_start"] is None or iso < counts["earliest_future_start"]:
                        counts["earliest_future_start"] = iso
                    if counts["latest_future_start"] is None or iso > counts["latest_future_start"]:
                        counts["latest_future_start"] = iso
                    continue
            except (KeyError, ValueError, TypeError):
                counts["invalid_start"] += 1
                # A malformed forecast is a genuine error, not an upcoming game.
                pass
            previous = self.store.get("audit_error", key)
            if previous and not force_retry:
                next_retry = pd.to_datetime(previous.get("retry_after"), utc=True, errors="coerce")
                if pd.notna(next_retry) and next_retry > current:
                    counts["deferred"] += 1
                    continue
            due.append((key, frozen))

        counts["due"] = len(due)
        # Older failures are not allowed to monopolize the first 20 slots.
        due.sort(key=lambda item: (
            bool(self.store.get("audit_error", item[0])),
            str(item[1].get("card", {}).get("Start (UTC)", "")), item[0]))
        began = time.monotonic()
        for index, (key, frozen) in enumerate(due):
            if counts["attempted"] >= limit or time.monotonic() - began > time_budget:
                counts["remaining"] = len(due) - index
                break
            counts["attempted"] += 1
            try:
                card = frozen["card"]
                result = frozen["result"]
                start = pd.to_datetime(card["Start (UTC)"], utc=True)
                created = pd.to_datetime(frozen["created_at"], utc=True)
                if created >= start:
                    self.store.put("grade", key, {
                        "status": "EXCLUDED", "outcome": "LATE_EXCLUDED",
                        "actual": None, "graded_at": now(),
                        "event_id": str(frozen.get("event_id", "")),
                        "note": "Saved at or after the recorded start; excluded from pregame accuracy and ROI."
                    }, immutable=True)
                    counts["late_excluded"] += 1
                    continue
                if start > pd.Timestamp.now(tz="UTC"):
                    counts["pending"] += 1
                    continue
                event_id = str(frozen["event_id"])
                summary = self.e["saved_fetch"](
                    self.e["ESPN_BASE"] + "summary", {"event": event_id},
                    ttl_seconds=300)
                if str(summary.get("header", {}).get("id")) != event_id:
                    raise ValueError("ESPN event ID does not match frozen event")
                actual = completed_actual(
                    summary, result["player_id"], self.e["market_columns"](card))
                if actual is None:
                    # A final boxscore may still omit a player or unsupported market.
                    # Do not silently mark missing data as DNP or a loss.
                    completed = any(c.get("status", {}).get("type", {}).get("completed")
                                    for c in summary.get("header", {}).get("competitions", []))
                    if completed:
                        raise ValueError("Final boxscore missing supported player statistics")
                    counts["pending"] += 1
                    continue
                weights = self.e.get("fantasy_weights", lambda record: None)(card)
                if weights and actual.get("status") == "FINAL":
                    actual["actual"] = sum(actual["stats"][column] * weight
                                           for column, weight in weights.items())
                outcome = "DNP"
                if actual["status"] == "FINAL":
                    difference = actual["actual"] - float(card["Line"])
                    if abs(difference) < 1e-9:
                        outcome = "TIE"
                    else:
                        if result["direction"] not in ("MORE", "LESS"):
                            raise ValueError("Forecast has no valid MORE/LESS direction")
                        correct = (difference > 0) == (result["direction"] == "MORE")
                        outcome = "WIN" if correct else "LOSS"
                grade = dict(actual, outcome=outcome, graded_at=now(),
                             event_id=event_id,
                             note="Directional research grade; platform settlement and priced paper ROI are separate.")
                self.store.put("grade", key, grade, immutable=True)
                self.store.put("audit_error", key, {"resolved": True, "resolved_at": now()})
                counts["DNP" if outcome == "DNP" else "ties" if outcome == "TIE" else "graded"] += 1
            except Exception as error:
                counts["errors"] += 1
                prior = self.store.get("audit_error", key, {}) or {}
                attempts = int(prior.get("attempts", 0)) + 1
                # No raw exception strings: they can contain API URLs or secrets.
                reason = str(error) if isinstance(error, (ValueError, KeyError, TypeError)) else type(error).__name__
                if not isinstance(error, (ValueError, KeyError, TypeError)):
                    reason = "Unexpected " + type(error).__name__
                self.store.put("audit_error", key, {
                    "resolved": False, "type": type(error).__name__,
                    "reason": reason[:180], "attempts": attempts,
                    "last_attempt": now(),
                    "retry_after": (pd.Timestamp.now(tz="UTC") +
                                    pd.Timedelta(minutes=min(360, 15 * 2 ** min(attempts - 1, 5)))).isoformat(),
                    "event_id": str(frozen.get("event_id", ""))[:32],
                    "player": str(frozen.get("card", {}).get("Player", ""))[:90],
                    "market": str(frozen.get("card", {}).get("Stat", ""))[:90],
                })
        return counts

    def record_paper_forecast(self, frozen):
        key = frozen["id"]

        if self.store.get(
            "paper_forecast",
            key,
        ) is not None:
            return

        result = frozen["result"]
        card = frozen["card"]

        record = {
            "forecast_id": key,
            "platform": card.get("Platform"),
            "created_at": now(),
            "direction": result["direction"],
            "priced": False,
            "policy": "model-paper-v1",
            "reason": (
                "No verified matching price captured "
                "at forecast time."
            ),
        }

        if (
            not result.get("selection_allowed", True)
            or result.get("tie")
        ):
            record["reason"] = (
                "Model side is not selectable on this tier, "
                "or projection equals the line."
            )

        elif card.get("Platform") == "prizepicks":
            record["reason"] = (
                "Single-pick unit benchmark only. "
                "PrizePicks entry ROI requires a frozen "
                "multi-pick entry and its payout rules."
            )

        elif (
            pd.Timestamp.now(tz="UTC")
            - pd.to_datetime(
                frozen["created_at"],
                utc=True,
            )
        ).total_seconds() > 60:
            record["reason"] = (
                "Original forecast is older; "
                "no new price is retrofitted to it."
            )

        else:
            try:
                if card.get("Platform") == "draftkings":
                    scan = pd.to_datetime(
                        frozen.get("odds_scan_at"),
                        utc=True,
                        errors="coerce",
                    )

                    created = pd.to_datetime(
                        frozen["created_at"],
                        utc=True,
                    )

                    if (
                        pd.isna(scan)
                        or not 0 <= (
                            created - scan
                        ).total_seconds() <= 1800
                    ):
                        raise ValueError(
                            "Odds snapshot was missing or "
                            "over 30 minutes old at freeze time."
                        )

                    market = result.get("market") or {}

                    price_field = (
                        "over_price"
                        if result["direction"] == "MORE"
                        else "under_price"
                    )

                    price = float(market[price_field])

                    if (
                        not math.isfinite(price)
                        or abs(price) < 100
                    ):
                        raise ValueError(
                            "Invalid American odds."
                        )

                    profit = (
                        price / 100
                        if price > 0
                        else 100 / abs(price)
                    )

                    record.update(
                        priced=True,
                        stake=1.0,
                        fees=0.0,
                        win_payout=1 + profit,
                        american_odds=price,
                        price_captured_at=frozen[
                            "odds_scan_at"
                        ],
                        reason=(
                            "Flat $1 hypothetical straight prop; "
                            "recorded sportsbook price, with "
                            "no-vig used only as separate context."
                        ),
                    )

                elif card.get("Platform") == "kalshi":
                    quote = result.get("kalshi") or {}

                    side = (
                        "yes"
                        if result["direction"] == "MORE"
                        else "no"
                    )

                    prices = [
                        row
                        for row in quote.get("quotes", [])
                        if str(
                            row.get("Contract", "")
                        ).lower() == side
                    ]

                    if len(prices) != 1:
                        raise ValueError(
                            "No unique matching contract ask."
                        )

                    stamp = pd.to_datetime(
                        quote["read_at"],
                        utc=True,
                    )

                    created = pd.to_datetime(
                        frozen["created_at"],
                        utc=True,
                    )

                    if not 0 <= (
                        created - stamp
                    ).total_seconds() <= 120:
                        raise ValueError(
                            "Contract quote is too old "
                            "for the paper entry."
                        )

                    price = float(
                        prices[0]["Ask ($ / contract)"]
                    )

                    fetch = self.e["kalshi_public"]

                    metadata = kalshi_fee_metadata(
                        fetch,
                        quote["ticker"],
                    )

                    estimate = fee_estimate(
                        price,
                        1,
                        metadata["multiplier"],
                        ".0001",
                    )

                    book = fetch(
                        "markets/"
                        + quote["ticker"]
                        + "/orderbook"
                    ).get("orderbook_fp", {})

                    opposite = (
                        "no"
                        if side == "yes"
                        else "yes"
                    )

                    depth = sum(
                        float(level[1])
                        for level in book.get(
                            opposite + "_dollars",
                            [],
                        )
                        if abs(
                            1 - float(level[0]) - price
                        ) < 1e-6
                    )

                    if depth < 1:
                        raise ValueError(
                            "One contract of displayed "
                            "liquidity could not be verified "
                            "at the ask."
                        )

                    start = pd.to_datetime(
                        card["Start (UTC)"],
                        utc=True,
                    )

                    if pd.Timestamp.now(tz="UTC") >= start:
                        raise ValueError(
                            "Game started before paper "
                            "entry pricing completed."
                        )

                    record.update(
                        priced=True,
                        stake=price,
                        fees=estimate["fee"],
                        contracts=1,
                        side=side,
                        ticker=quote["ticker"],
                        price_captured_at=quote["read_at"],
                        fee_metadata=metadata,
                        reason=(
                            "One hypothetical contract at "
                            "the captured ask, all-taker fee "
                            "estimate, direct-member rounding. "
                            "This assumes a fill; "
                            "no trade was placed."
                        ),
                    )

            except Exception as error:
                record["reason"] = str(error)

        self.store.put(
            "paper_forecast",
            key,
            record,
            immutable=True,
        )

    def model_roi_rows(self):
        grades = dict(self.store.items("grade"))
        prices = dict(self.store.items("paper_forecast"))
        rows = []

        for key, frozen in unique_forecasts(self.store.items("forecast")):
            result = frozen["result"]
            card = frozen["card"]
            grade = grades.get(key, {})
            outcome = grade.get("outcome", "PENDING")

            eligible = (
                result.get("selection_allowed", True)
                and not result.get("tie", False)
            )

            benchmark_stake = (
                1.0
                if eligible and outcome in ("WIN", "LOSS")
                else 0.0
            )

            benchmark_profit = (
                (1.0 if outcome == "WIN" else -1.0)
                if benchmark_stake
                else 0.0
            )

            price = prices.get(key, {})
            settled = False
            payout = None

            cost = (
                float(price.get("stake", 0))
                + float(price.get("fees", 0))
                if price.get("priced")
                else None
            )

            note = price.get(
                "reason",
                "No entry price captured; "
                "unit benchmark is still available.",
            )

            if eligible and price.get("priced"):
                if (
                    card.get("Platform") == "draftkings"
                    and outcome != "PENDING"
                ):
                    if outcome == "WIN":
                        payout = price["win_payout"]
                    elif outcome == "LOSS":
                        payout = 0.0
                    else:
                        payout = cost

                    settled = True

                elif card.get("Platform") == "kalshi":
                    venue = self.store.get(
                        "paper_settlement",
                        key,
                    )

                    if venue:
                        payout = float(venue["payout"])
                        settled = True

                    elif (
                        pd.to_datetime(
                            card["Start (UTC)"],
                            utc=True,
                        )
                        <= pd.Timestamp.now(tz="UTC")
                    ):
                        try:
                            market = self.e[
                                "kalshi_public"
                            ](
                                "markets/" + price["ticker"]
                            ).get("market", {})

                            if (
                                market.get("ticker")
                                == price["ticker"]
                                and market.get("status")
                                == "finalized"
                            ):
                                if (
                                    "settlement_value_dollars"
                                    in market
                                ):
                                    value = float(
                                        market[
                                            "settlement_value_dollars"
                                        ]
                                    )
                                elif market.get("result") == "yes":
                                    value = 1.0
                                elif market.get("result") == "no":
                                    value = 0.0
                                else:
                                    value = float("nan")

                                notional = float(
                                    market.get(
                                        "notional_value_dollars",
                                        "1",
                                    )
                                )

                                if (
                                    math.isfinite(value)
                                    and 0 <= value <= 1
                                    and notional == 1
                                ):
                                    unit_payout = (
                                        value
                                        if price["side"] == "yes"
                                        else 1 - value
                                    )

                                    payout = (
                                        price["contracts"]
                                        * unit_payout
                                    )
                                    settled = True

                                    self.store.put(
                                        "paper_settlement",
                                        key,
                                        {
                                            "payout": payout,
                                            "settlement_value_dollars": value,
                                            "recorded_at": now(),
                                            "venue_settlement_ts": market.get(
                                                "settlement_ts"
                                            ),
                                        },
                                        immutable=True,
                                    )

                        except Exception:
                            pass

            rows.append({
                "Forecast ID": key,
                "Player": card["Player"],
                "Platform": card["Platform"],
                "Stat": card["Stat"],
                "Line": card["Line"],
                "Model side": result["direction"],
                "Result": outcome,
                "Benchmark stake (units)": benchmark_stake,
                "Benchmark profit (units)": benchmark_profit,
                "Priced paper cost ($)": cost,
                "Priced paper payout ($)": payout,
                "Priced paper profit ($)": (
                    payout - cost
                    if settled
                    else None
                ),
                "Paper settled": settled,
                "Policy / missing price": note,
            })

        return rows
    def probability_calibration(self):
        grades = dict(self.store.items("grade"))
        buckets = {}

        for key, frozen in unique_forecasts(self.store.items("forecast")):
            card = frozen["card"]
            result = frozen["result"]

            if (
                card.get("Platform") != "prizepicks"
                or not result.get("selection_allowed", True)
                or result.get("tie")
            ):
                continue

            probability = result.get(
                "estimated_hit_probability"
            )

            if (
                probability is None
                or not math.isfinite(float(probability))
                or not 0 <= float(probability) <= 1
            ):
                continue

            created = pd.to_datetime(
                frozen["created_at"],
                utc=True,
            )
            start = pd.to_datetime(
                card["Start (UTC)"],
                utc=True,
            )

            if created >= start:
                continue

            tier = card.get("Line Type", "Unverified")

            if tier == "Standard":
                tier = "Regular"
            elif tier not in ("Goblin", "Demon"):
                tier = "Unverified"

            decile = min(
                9,
                int(float(probability) * 10),
            )

            bucket = buckets.setdefault(
                (tier, decile),
                {
                    "probabilities": [],
                    "settled_probabilities": [],
                    "wins": 0,
                    "losses": 0,
                    "ties": 0,
                    "dnp": 0,
                    "pending": 0,
                },
            )

            bucket["probabilities"].append(
                float(probability)
            )

            outcome = grades.get(
                key,
                {},
            ).get("outcome", "PENDING")

            field = {
                "WIN": "wins",
                "LOSS": "losses",
                "TIE": "ties",
                "DNP": "dnp",
            }.get(outcome, "pending")

            bucket[field] += 1

            if outcome in ("WIN", "LOSS"):
                bucket["settled_probabilities"].append(
                    float(probability)
                )

        output = []

        for (tier, decile), data in sorted(buckets.items()):
            count = data["wins"] + data["losses"]

            expected = (
                sum(data["settled_probabilities"]) / count
                if count
                else None
            )

            actual = (
                data["wins"] / count
                if count
                else None
            )

            if count:
                z = 1.96
                denominator = 1 + z * z / count

                center = (
                    actual + z * z / (2 * count)
                ) / denominator

                half = (
                    z
                    * math.sqrt(
                        actual * (1 - actual) / count
                        + z * z / (4 * count * count)
                    )
                    / denominator
                )

                interval = (
                    f"{max(0, center - half):.1%}"
                    f"–{min(1, center + half):.1%}"
                )
            else:
                interval = "Awaiting finals"

            label = (
                f"{decile * 10}–{decile * 10 + 9}%"
                if decile < 9
                else "90–100%"
            )

            output.append({
                "Tier": tier,
                "Predicted bucket": label,
                "Forecasts": len(data["probabilities"]),
                "Decided": count,
                "Hits": data["wins"],
                "Misses": data["losses"],
                "Average predicted (decided)": (
                    f"{expected:.1%}"
                    if expected is not None
                    else "—"
                ),
                "Actual hit rate": (
                    f"{actual:.1%}"
                    if actual is not None
                    else "Awaiting finals"
                ),
                "Actual 95% interval": interval,
                "Ties": data["ties"],
                "DNP": data["dnp"],
                "Pending": data["pending"],
            })

        return output

    def research_rows(self, across_platforms=False):
        rows = []
        grades = dict(self.store.items("grade"))

        for key, frozen in unique_forecasts(self.store.items("forecast"), across_platforms=across_platforms):
            grade = grades.get(key)
            card = frozen["card"]
            result = frozen["result"]

            rows.append({
                "ID": key,
                "Player": card["Player"],
                "Platform": card["Platform"],
                "Stat": card["Stat"],
                "Line": card["Line"],
                "Pick": result["direction"],
                "Projection": result["projection"],
                "Actual": (
                    grade.get("actual")
                    if grade
                    else None
                ),
                "Outcome": (
                    grade.get("outcome")
                    if grade
                    else "PENDING"
                ),
                "Created (UTC)": frozen["created_at"],
                "Event": frozen["event_id"],
                "Game date": pd.to_datetime(card.get("Start (UTC)"), utc=True, errors="coerce").tz_convert("America/Los_Angeles").strftime("%Y-%m-%d")
                    if pd.notna(pd.to_datetime(card.get("Start (UTC)"), utc=True, errors="coerce")) else "Unknown",
            })

        return rows

    def learn(self):
        groups = {}
        seen = set()
        grades = dict(self.store.items("grade"))
        reviews = self.store.items("model_review")

        for key, frozen in unique_forecasts(self.store.items("forecast")):
            result = frozen["result"]
            card = frozen["card"]
            grade = grades.get(key)

            if (
                not grade
                or grade.get("status") != "FINAL"
                or grade.get("outcome") == "TIE"
            ):
                continue

            group = result.get("stat_group")

            if (
                not group
                or result.get("baseline") is None
            ):
                continue

            dedup = (
                frozen["event_id"],
                result["player_id"],
                group,
                float(card["Line"]),
            )

            if dedup in seen:
                continue

            seen.add(dedup)

            groups.setdefault(group, []).append({
                "start": card["Start (UTC)"],
                "created": frozen["created_at"],
                "event": frozen["event_id"],
                "baseline": result["baseline"],
                "adjusted": result["matchup_projection"],
                "line": float(card["Line"]),
                "actual": grade["actual"],
            })

        reports = []

        for group, rows in groups.items():
            frame = pd.DataFrame(rows).sort_values(
                "start"
            )

            frame["day"] = pd.to_datetime(
                frame["start"],
                utc=True,
            ).dt.strftime("%Y-%m-%d")

            days = sorted(frame["day"].unique())

            if (
                len(frame) < 120
                or frame["event"].nunique() < 25
                or len(days) < 12
            ):
                reports.append({
                    "stat": group,
                    "status": (
                        "Collecting settled pregame forecasts"
                    ),
                    "samples": len(frame),
                })
                continue

            split = days[
                max(1, int(len(days) * 0.75))
            ]

            previous = [
                review["holdout_end"]
                for _, review in reviews
                if review.get("stat") == group
            ]

            if previous:
                fresh_days = [
                    day
                    for day in days
                    if day > max(previous)
                ]

                if len(fresh_days) < 4:
                    continue

                split = fresh_days[0]

            train = frame[frame["day"] < split]
            test = frame[frame["day"] >= split]

            if (
                len(train) < 80
                or len(test) < 30
                or test["event"].nunique() < 8
            ):
                continue

            def accuracy(data, alpha):
                prediction = (
                    data["baseline"]
                    + alpha * (
                        data["adjusted"]
                        - data["baseline"]
                    )
                )

                correct = (
                    (prediction > data["line"])
                    == (data["actual"] > data["line"])
                )

                return float(correct.mean())

            def mae(data, alpha):
                prediction = (
                    data["baseline"]
                    + alpha * (
                        data["adjusted"]
                        - data["baseline"]
                    )
                )

                return float(
                    abs(
                        prediction - data["actual"]
                    ).mean()
                )

            candidate = max(
                [0, 0.25, 0.5, 0.75, 1, 1.25, 1.5],
                key=lambda alpha: (
                    accuracy(train, alpha),
                    -mae(train, alpha),
                ),
            )

            incumbent = self.store.get(
                "model",
                group,
                {"alpha": 1.0},
            )

            review_key = group + ":" + str(days[-1])

            if self.store.get(
                "model_review",
                review_key,
            ):
                continue

            score = accuracy(test, candidate)

            incumbent_score = accuracy(
                test,
                float(incumbent["alpha"]),
            )

            baseline_score = accuracy(test, 0)

            reference = max(
                incumbent_score,
                baseline_score,
            )

            promote = (
                score >= incumbent_score + 0.01
                and score >= baseline_score
            )

            report = {
                "stat": group,
                "alpha": candidate,
                "train_samples": len(train),
                "holdout_samples": len(test),
                "holdout_start": split,
                "holdout_end": days[-1],
                "accuracy": score,
                "reference_accuracy": reference,
                "promoted": promote,
                "reviewed_at": now(),
                "method": (
                    "Chronological date-grouped holdout; "
                    "directional accuracy first, "
                    "numeric error tie-break."
                ),
            }

            if promote:
                digest = hashlib.sha256(
                    encode(report).encode()
                ).hexdigest()[:12]

                model = dict(
                    report,
                    version="learned-" + digest,
                )

                self.store.put(
                    "model",
                    group,
                    model,
                )

            self.store.put(
                "model_review",
                review_key,
                report,
                immutable=True,
            )

            reports.append(report)

        return reports

    def settle_kalshi_tickets(self):
        count = 0

        for key, ticket in self.store.items("ticket"):
            if (
                ticket.get("status") != "OPEN"
                or ticket.get("settlement_method")
                != "kalshi_public_final"
            ):
                continue

            try:
                market = self.e["kalshi_public"](
                    "markets/" + ticket["ticker"]
                ).get("market", {})

                if (
                    market.get("ticker") != ticket["ticker"]
                    or market.get("status") != "finalized"
                ):
                    continue

                if "settlement_value_dollars" in market:
                    yes_value = float(
                        market["settlement_value_dollars"]
                    )
                elif market.get("result") in ("yes", "no"):
                    yes_value = (
                        1.0
                        if market["result"] == "yes"
                        else 0.0
                    )
                else:
                    continue

                if (
                    not math.isfinite(yes_value)
                    or not 0 <= yes_value <= 1
                ):
                    continue

                notional = float(
                    market.get(
                        "notional_value_dollars",
                        "1",
                    )
                )

                if notional != 1:
                    continue

                unit = (
                    yes_value
                    if ticket["side"] == "yes"
                    else 1 - yes_value
                )

                payout = (
                    float(ticket["contracts"])
                    * unit
                )

                value = save_ticket(
                    self.store,
                    {
                        "id": key,
                        "payout": payout,
                    },
                    settle=True,
                )

                value["settlement_source"] = (
                    "Kalshi finalized public contract"
                )

                value["settlement_value_dollars"] = (
                    yes_value
                )

                value["venue_settlement_ts"] = market.get(
                    "settlement_ts"
                )

                self.store.put(
                    "ticket",
                    key,
                    value,
                )

                count += 1

            except Exception:
                continue

        return count

    def maintenance(self, interactive=False):
        count = self.grade(
            limit=max(1, len(unique_forecasts(self.store.items("forecast")))),
            time_budget=15 if interactive else 480,
        )

        reports = self.learn()
        self.model_roi_rows()
        settled_tickets = self.settle_kalshi_tickets()

        status = {
            "run_at": now(),
            "grading": count,
            "learning": reports,
            "kalshi_tickets_settled": settled_tickets,
        }

        self.store.put(
            "maintenance",
            "last",
            status,
        )

        return status


def ticket_profit(ticket):
    if ticket.get("status") != "SETTLED":
        return None

    return (
        float(ticket["payout"])
        - float(ticket["stake"])
        - float(ticket.get("fees", 0))
    )


def save_ticket(store, ticket, settle=False):
    key = str(ticket.get("id", "")).strip()

    if not key or len(key) > 150:
        raise ValueError(
            "Enter a unique ticket or fill ID."
        )

    for field in ("stake", "fees", "payout"):
        value = float(ticket.get(field, 0))

        if (
            not math.isfinite(value)
            or value < 0
        ):
            raise ValueError(
                "Cash amounts must be finite "
                "and nonnegative."
            )

    existing = store.get("ticket", key)

    if settle:
        if existing is None:
            raise ValueError("Ticket not found.")

        ticket = dict(
            existing,
            payout=float(ticket["payout"]),
            status="SETTLED",
            settled_at=now(),
        )

    else:
        if existing:
            raise ValueError(
                "That ticket ID already exists."
            )

        if float(ticket["stake"]) <= 0:
            raise ValueError(
                "Stake must be positive."
            )

        ticket = dict(
            ticket,
            status="OPEN",
            payout=0,
            created_at=now(),
        )

    store.put("ticket", key, ticket)
    return ticket


def utc_timestamp(value):
    """Fast scalar timestamp parsing for saved rows; invalid values stay missing."""
    try:
        stamp = pd.Timestamp(value)
        if pd.isna(stamp):
            return pd.NaT
        return stamp.tz_localize('UTC') if stamp.tzinfo is None else stamp.tz_convert('UTC')
    except (TypeError, ValueError, OverflowError):
        return pd.NaT


def market_signature(card, event_id=None, across_platforms=False):
    """Stable market identity; scan IDs and tier-label corrections are metadata."""
    import re
    import unicodedata
    def clean(value):
        value = unicodedata.normalize('NFKD', str(value or ''))
        return re.sub(r'[^a-z0-9]', '', value.encode('ascii', 'ignore').decode().lower())
    player = str(card.get('Player') or '')
    if ',' in player:
        last, first = player.split(',', 1)
        player = first + ' ' + last
    start = utc_timestamp(card.get('Start (UTC)'))
    game = clean(card.get('Game'))
    # Refuse to merge unknown events or unknown players on weak metadata.
    if not player or (not event_id and (not game or pd.isna(start))):
        return ('unverified', identity(card))
    try:
        line = Decimal(str(card['Line'])).normalize()
        if not line.is_finite():
            raise ValueError('Invalid line')
        columns = tuple(sorted(market_columns(card) or ()))
    except (ValueError, TypeError, KeyError, ArithmeticError):
        return ('unverified', identity(card))
    if not columns:
        return ('unverified', identity(card))
    scoring = tuple(sorted((fantasy_weights(card) or {}).items()))
    event = ('espn', str(event_id)) if event_id else ('fixture', game, start.isoformat())
    return (event, clean(player), columns, scoring, clean(card.get('Period') or 'fullgame'),
            str(line), '' if across_platforms else clean(card.get('Platform')))


def unique_forecasts(records, across_platforms=False):
    """Earliest original forecast wins; source records remain untouched."""
    def created(item):
        stamp = utc_timestamp(item[1].get('created_at'))
        return (pd.isna(stamp), stamp.value if pd.notna(stamp) else 0, str(item[0]))
    kept = {}
    for key, frozen in sorted(records, key=created):
        signature = market_signature(frozen.get('card') or {}, frozen.get('event_id'), across_platforms)
        kept.setdefault(signature, (key, frozen))
    return list(kept.values())


def forecast_index(records):
    # Keep old IDs working for watchlists while aliasing new scans to old picks.
    lookup = dict(records)
    earliest = {}
    for key, frozen in unique_forecasts(records):
        earliest.setdefault(market_signature(frozen['card']), frozen)
    lookup.update(earliest)
    return lookup


def saved_prediction(index, card):
    return index.get(market_signature(card)) or index.get(identity(card))


class TursoStore(Store):
    """Remote libSQL store, retaining the same immutable records and backup format."""
    def __init__(self, url, token):
        from urllib.parse import urlparse
        parsed = urlparse(str(url).strip().replace('libsql://', 'https://', 1))
        if parsed.scheme != 'https' or not parsed.hostname or not parsed.hostname.endswith('.turso.io') or parsed.username or parsed.password or parsed.query:
            raise ValueError('Invalid Turso database URL')
        if not token:
            raise ValueError('Turso authentication token missing')
        self.endpoint = 'https://' + parsed.netloc + '/v2/pipeline'
        self.token = str(token).strip()
        self.http = requests.Session()
        self.remote = True
        self._read_cache = st.session_state.setdefault('_turso_read_cache_v1', {})
        self.query('CREATE TABLE IF NOT EXISTS sports_records (namespace TEXT NOT NULL, key TEXT NOT NULL, payload TEXT NOT NULL, updated TEXT NOT NULL, PRIMARY KEY(namespace,key))')

    def restore(self, value):
        # Validate the complete backup before any remote writes. Original records win.
        validator = OfflineSessionStore.__new__(OfflineSessionStore)
        validator.records = {}
        checked = validator.restore(value)
        for (namespace, key), payload in validator.records.items():
            self.put(namespace, key, payload, immutable=True)
        return checked

    def query(self, sql, values=(), fetch=False):
        def argument(value):
            if value is None: return {'type': 'null'}
            if isinstance(value, int): return {'type': 'integer', 'value': str(value)}
            if isinstance(value, float): return {'type': 'float', 'value': value}
            return {'type': 'text', 'value': str(value)}
        try:
            response = getattr(self, 'http', requests).post(self.endpoint, headers={'Authorization': 'Bearer ' + self.token},
                json={'requests': [{'type': 'execute', 'stmt': {'sql': sql, 'args': [argument(v) for v in values], 'want_rows': fetch}}, {'type': 'close'}]}, timeout=30)
            if response.status_code != 200:
                raise RuntimeError(f'Turso returned HTTP {response.status_code}. Check the database token and its permissions.' )
            item = response.json()['results'][0]
            if item.get('type') != 'ok' or item.get('response', {}).get('type') != 'execute':
                raise RuntimeError('Turso rejected the database statement. Confirm the token has read and write permission.')
            rows = item['response']['result'].get('rows', [])
            return [[cell.get('value') for cell in row] for row in rows] if fetch else None
        except RuntimeError as error:
            if str(error).startswith(('Turso returned HTTP ', 'Turso rejected the database statement.')):
                raise
            raise RuntimeError('Turso connection failed. Saved remote records are unchanged.') from None
        except requests.Timeout:
            raise RuntimeError('Turso connection timed out. Saved remote records are unchanged.') from None
        except Exception:
            raise RuntimeError('Turso connection or response failed. Check the database URL. Saved remote records are unchanged.') from None


def preparation_rows(rows):
    unique = {}
    for card in rows:
        if card.get('Decoded') and today_pregame(card.get('Start (UTC)')) and market_columns(card):
            unique.setdefault(market_signature(card), card)
    return list(unique.values())


def calculation_failure_details(error):
    """Keep known app errors and code location; never save arbitrary URLs/keys."""
    import traceback
    known = {
        'No unique ESPN fixture matches the teams and start time.',
        'Matchup team identities are unavailable.',
        'Player does not uniquely match a current matchup roster.',
        'Player is listed out or suspended; no playable projection.',
        'Fixture is no longer pregame.',
        'Fixture is no longer pregame; no new record saved.',
        'New projections require a future fixture start time.',
        'Choose the current season for a pregame projection.',
        "This market's scoring is not mapped yet.",
        'A verified positive prop line is required.',
        'At least 3 current-season appearances are needed.',
        'No regular-season appearances returned for these two seasons.',
        'ESPN game-log fields are missing. No averages were substituted.',
        'ESPN returned a game without matchup metadata.',
        'Incomplete player game-log row.',
        'Unknown home/away marker.',
        'Depth-chart team did not match.',
        'Depth-chart season did not match.',
    }
    for field in ('prop line', 'projection', 'line', 'gap', 'minutes'):
        known.add('Missing or invalid ' + field + '; forecast not saved.')
    message = str(error)
    reason = message if isinstance(error, ValueError) and message in known else (
        type(error).__name__ + ': calculation failed; code location recorded for diagnosis.')
    frames = traceback.extract_tb(error.__traceback__) if error.__traceback__ else []
    local = [frame for frame in frames if frame.filename.endswith('app.py')]
    frame = local[-1] if local else None
    return {'reason': reason, 'error_type': type(error).__name__,
            'location': (frame.name + ':' + str(frame.lineno)) if frame else 'Unavailable'}


def prepare_saved_board(wf, board, budget_seconds=20, max_picks=6, clock=time.monotonic):
    """Bounded resumable saved-data work. Never calls an odds/feed refresh."""
    import copy
    board = board if isinstance(board, dict) else {}
    rows = preparation_rows(board.get('v4_rows') or [])
    snapshot = wf.store.items('forecast')
    index = forecast_index(snapshot)
    previous_snapshot = getattr(wf, '_preparation_existing', None)
    wf._preparation_existing = list(snapshot)
    states = dict(wf.store.items('calculation_status'))
    # Warm small namespaces once, including absent keys, instead of a remote
    # lookup for every new forecast or every prop with the initial model.
    wf.store.items('paper_forecast')
    wf.store.items('model')
    began = clock()
    report = {'run_at': now(), 'state': 'running', 'total': len(rows),
              'ready': sum(bool(saved_prediction(index, c)) for c in rows),
              'attempted': 0, 'source_scan_at': board.get('v4_scan_at')}
    wf.store.put('preparation', 'last', report)
    # Work on an isolated environment mapping; do not patch process-wide globals.
    engine = wf.e
    wf.e = dict(engine)
    # CLI workers have no browser session. Price context must come from the
    # exact saved board being prepared, rather than an empty session_state.
    if 'engine_no_vig' in engine:
        wf.e['engine_no_vig'] = lambda card: engine['engine_no_vig'](card, board.get('v4_rows') or [])
    original_player, original_event = wf.player, wf.event_for
    original_availability = getattr(wf, 'availability', None)
    player_cache, event_cache, history_cache, availability_cache, shared_cache = {}, {}, {}, {}, {}
    def reuse(cache, key, call):
        if key not in cache:
            try:
                cache[key] = (True, call())
            except Exception as error:
                cache[key] = (False, error)
        ok, value = cache[key]
        if not ok:
            raise value
        return copy.deepcopy(value)
    wf.player = lambda c: reuse(player_cache, (c.get('Game'), c.get('Player')), lambda: original_player(c))
    wf.event_for = lambda c: reuse(event_cache, (c.get('Game'), c.get('Start (UTC)')), lambda: original_event(c))
    if original_availability is not None:
        wf.availability = lambda *args: reuse(availability_cache, (int(clock() // 60), args), lambda: original_availability(*args))
    for name in ('load_team_advanced', 'load_position_defense', 'engine_position', 'engine_game_context'):
        if name in engine:
            function = engine[name]
            wf.e[name] = lambda *args, _fn=function, _name=name: reuse(
                shared_cache, (_name, encode(args)), lambda: _fn(*args))
    history = engine['fetch_player_history']
    wf.e['fetch_player_history'] = lambda *args: reuse(history_cache, args, lambda: history(*args))
    try:
        for card in sorted(rows, key=lambda c: str(c.get('Start (UTC)'))):
            key = identity(card)
            if saved_prediction(index, card):
                continue
            retry = pd.to_datetime((states.get(key) or {}).get('retry_at'), utc=True, errors='coerce')
            if pd.notna(retry) and retry > pd.Timestamp.now(tz='UTC'):
                continue
            if clock() - began >= budget_seconds or report['attempted'] >= max_picks:
                break
            if not today_pregame(card.get('Start (UTC)')):
                continue
            state = {'Status': 'Calculating', 'updated_at': now(), 'Player': card.get('Player')}
            report['current'] = str(card.get('Player')) + ' · ' + str(card.get('Stat'))
            try:
                frozen = wf.calculate(card, engine['current_season']())
                index[key] = index[market_signature(card)] = frozen
                wf._preparation_existing.append((frozen['id'], frozen))
                state = {'Status': 'Ready', 'updated_at': now()}
            except Exception as error:
                details = calculation_failure_details(error)
                print('SPORTS_LAB_PREP_ERROR', str(card.get('Player')), details['reason'], details['location'], flush=True)
                state = {'Status': 'Unavailable', **details,
                         'Player': card.get('Player'), 'Stat': card.get('Stat'),
                         'Platform': card.get('Platform'),
                         'updated_at': now(), 'retry_at': (pd.Timestamp.now(tz='UTC') + pd.Timedelta(minutes=15)).isoformat()}
            states[key] = state
            wf.store.put('calculation_status', key, state)
            report['attempted'] += 1
            if report['attempted'] % 25 == 0:
                report['ready'] = sum(bool(saved_prediction(index, c)) for c in rows)
                wf.store.put('preparation', 'last', report)
    finally:
        wf.player, wf.event_for, wf.e = original_player, original_event, engine
        if original_availability is not None:
            wf.availability = original_availability
        wf._preparation_existing = previous_snapshot
    report['ready'] = sum(bool(saved_prediction(index, c)) for c in rows)
    report['unavailable'] = sum(not saved_prediction(index, c) and
        (states.get(identity(c)) or {}).get('Status') == 'Unavailable' for c in rows)
    report['pending'] = len(rows) - report['ready'] - report['unavailable']
    report['state'] = 'complete' if report['ready'] == len(rows) else 'retry pending' if not report['pending'] else 'queued'
    report['finished_at'] = now()
    report.pop('current', None)
    wf.store.put('preparation', 'last', report)
    return report


def preparation_panel(wf, st, rows):
    st.markdown("#### Prepare today's board")
    with st.expander('Input checks and coverage'):
        inputs = wf.store.get('input_health', 'latest') or {}
        if inputs:
            st.caption('Input checks (UTC): ' + str(inputs.get('checked_at')))
            checks = [{'Input': name, 'Available': value.get('available', False),
                'Rows': value.get('rows'), 'Reason': value.get('reason', ''),
                'Fetched or checked (UTC)': value.get('fetched_at') or value.get('checked_at')}
                for name, value in inputs.get('sources', {}).items()]
            st.dataframe(pd.DataFrame(checks), hide_index=True, use_container_width=True)
            for limitation in inputs.get('limitations', []):
                st.warning(limitation)
            news = (inputs.get('sources', {}).get('news') or {}).get('articles') or []
            if news:
                st.caption('Recent ESPN NBA headlines. League news is context, not confirmed player availability.')
                st.dataframe(pd.DataFrame([{'Headline': a['headline'], 'Published (UTC)': a['published']} for a in news]), hide_index=True, use_container_width=True)
        else:
            st.info('The worker has not recorded input checks with this version yet.')
        coverage = wf.store.get('coverage', slate_day()) or {}
        if coverage:
            st.write({key: value for key, value in coverage.items() if key != 'checked_at'})
            st.caption('Missed starts remain counted. Provider completeness is reported separately below.')
    st.caption('The scheduled worker imports today’s lines and calculates them automatically when configured. This button resumes calculations using saved lines without a paid refresh.')
    auto = wf.store.get('automation', slate_day()) or {}
    if auto:
        cap = auto.get('feed_cap', 10)
        st.caption(f"Automatic worker last run (UTC): {auto.get('last_run', 'Import started')} · Odds request reservations: {auto.get('odds_requests', 0)}/34 · Tier feed: {auto.get('feed_requests', 0)}/{cap}")
        if auto.get('feed_partial'):
            if auto.get('feed_requests', 0) >= cap:
                st.warning(f'PrizePicks download is incomplete and the app’s daily {cap}-request cap is reached. Downloads cannot continue today under this cap. Missing lines are excluded from the ready count. PRIZEPICKS_DAILY_REQUEST_CAP controls this limit; increasing it can use more provider credits.')
            else:
                st.warning('PrizePicks download is incomplete. The background worker can continue saved pagination within the remaining daily request budget. Ready counts cover saved lines only.')
        if auto.get('fantasy_partial'):
            st.caption('Fantasy Score feed is incomplete or unavailable; a complete prop count does not confirm Fantasy coverage.')
        for issue in auto.get('errors', []):
            st.warning(issue)
    if not wf.store.remote:
        st.warning('Temporary session only. Keep this page open and download a backup before leaving. Background preparation is unavailable until permanent storage is restored.')
    current = preparation_rows(rows)
    index = forecast_index(wf.store.items('forecast'))
    ready = sum(bool(saved_prediction(index, c)) for c in current)
    st.progress(ready / len(current) if current else 0, text=f'{ready}/{len(current)} ready')
    last = wf.store.get('preparation', 'last') or {}
    if last.get('unavailable'):
        st.caption(f"Last batch: {last['unavailable']} unavailable; retried after 15 minutes while preparation is active.")
    with st.expander('Unavailable props — failure reasons'):
        failures = []
        statuses = dict(wf.store.items('calculation_status'))
        for card in current:
            state = statuses.get(identity(card)) or {}
            if state.get('Status') == 'Unavailable' and not saved_prediction(index, card):
                failures.append({'Player': card.get('Player'), 'Prop': card.get('Stat'),
                    'Platform': card.get('Platform'), 'Line': card.get('Line'),
                    'Reason': state.get('reason'), 'Code location': state.get('location', 'Awaiting diagnostic retry'),
                    'Checked (UTC)': state.get('updated_at')})
        if failures:
            st.dataframe(pd.DataFrame(failures), hide_index=True, use_container_width=True)
            st.download_button('Download calculation failures CSV',
                data=pd.DataFrame(failures).to_csv(index=False), file_name='calculation_failures.csv', mime='text/csv')
            st.caption('Older generic errors gain a specific reason on the next scheduled retry. This panel makes no provider requests.')
        else:
            st.caption('No failed calculations recorded for the current upcoming board.')
    if not current:
        st.info("No upcoming lines saved for today's Pacific date. The automatic worker must import today's slate first. Restoring an older board cannot supply today's lines.")
    if st.button("Prepare today's board", type='primary', disabled=not current, key='prepare_one_action'):
        board = {field: st.session_state.get(field) for field in BOARD_FIELDS}
        wf.store.put('board', 'board', board)
        wf.store.put('preparation', 'request', {'requested_at': now(), 'day': slate_day(), 'enabled': True})
        st.session_state['_prepare_active'] = True
        st.rerun()
    if st.session_state.get('_prepare_active'):
        st.caption('Preparation resumes automatically while this page is open. Closing it requires a connected database and the scheduled worker to continue.')
        if st.button('Pause preparation', key='pause_preparation'):
            st.session_state['_prepare_active'] = False
            wf.store.put('preparation', 'request', {'enabled': False, 'day': slate_day(), 'requested_at': now()})
            st.rerun()


def storage_failure_hint(error):
    # Inspect locally, but never display a raw exception or connection string.
    message = str(error).lower()
    if any(word in message for word in ('quota', 'limit exceeded', 'data transfer', 'storage limit', 'compute time', 'disk full')):
        return 'The database reports a usage or capacity limit. Check the Neon Usage page; code cannot restore exhausted capacity.'
    if any(word in message for word in ('password authentication', 'authentication failed', 'invalid password')):
        return 'Database credentials were rejected. Update DATABASE_URL in Streamlit secrets.'
    if any(word in message for word in ('timeout', 'timed out', 'resolve', 'connection refused')):
        return 'The database could not be reached. Check its endpoint status and connection settings.'
    return 'Permanent storage could not connect. Check Neon status, Usage and DATABASE_URL in Streamlit secrets.'


def run_grading_pass(wf, time_budget=15):
    """Visit every eligible saved forecast, resuming automatically if time expires."""
    forecasts = unique_forecasts(wf.store.items('forecast'))
    wf.store.items('audit_error')  # Reuse one cached list rather than N remote reads.
    original_engine = wf.e
    engine = dict(original_engine)
    fetch = original_engine['saved_fetch']
    summaries = {}
    def fetch_once(url, params=None, **kwargs):
        key = encode([url, params, kwargs])
        if key not in summaries:
            try:
                summaries[key] = (True, fetch(url, params, **kwargs))
            except Exception as error:
                summaries[key] = (False, error)
        ok, value = summaries[key]
        if not ok:
            raise value
        return value
    engine['saved_fetch'] = fetch_once
    wf.e = engine
    try:
        counts = wf.grade(limit=max(1, len(forecasts)), time_budget=time_budget)
    finally:
        wf.e = original_engine
    grades = dict(wf.store.items('grade'))
    resolved = sum(key in grades for key, _ in forecasts)
    report = {'run_at': now(), 'total': len(forecasts), 'resolved': resolved,
              'unresolved': len(forecasts) - resolved, 'grading': counts,
              'state': 'running' if counts.get('remaining', 0) else 'complete'}
    if report['state'] == 'complete':
        reviews = wf.learn()
        wf.model_roi_rows()
        settled = wf.settle_kalshi_tickets()
        wf.store.put('maintenance', 'last', {'run_at': now(), 'grading': counts,
            'learning': reviews, 'kalshi_tickets_settled': settled})
    wf.store.put('grade_run', 'last', report)
    return report


def compact_result_stat(value):
    import re
    text = re.sub(r'(?i)^over under player\s+', '', str(value))
    text = re.sub(r'(?i)\s*\(incl\.?\s*overtime\)', '', text)
    for word, short in [('fantasy score', 'Fantasy'), ('3 point field goals', '3PM'),
                        ('3 point fg', '3PM'), ('points', 'PTS'), ('rebounds', 'REB'),
                        ('assists', 'AST'), ('steals', 'STL'), ('blocks', 'BLK'),
                        ('turnovers', 'TOV')]:
        text = re.sub(re.escape(word), short, text, flags=re.I)
    return text.strip()


def result_table_html(records):
    """Human-readable results; internal identifiers stay in the CSV export."""
    from html import escape
    def number(value):
        try:
            value = float(value)
            return f'{value:.2f}'.rstrip('0').rstrip('.') if math.isfinite(value) else '—'
        except (TypeError, ValueError):
            return '—'
    headers = ['Player', 'Prop', 'Line', 'Pick', 'Projection', 'Actual', 'Result', 'Platform']
    html = ['<div class="sl-results-scroll"><table class="sl-results"><thead><tr>']
    html += ['<th>' + label + '</th>' for label in headers]
    html.append('</tr></thead><tbody>')
    for row in records:
        name = str(row.get('Player', ''))
        if ',' in name:
            surname, first = name.rsplit(',', 1)
            name = first.strip() + ' ' + surname.strip()
        direction = str(row.get('Pick', ''))
        outcome = str(row.get('Outcome', 'PENDING'))
        css = 'hit' if outcome == 'WIN' else 'miss' if outcome == 'LOSS' else 'waiting'
        label = {'WIN': '✓ WIN', 'LOSS': '✕ LOSS', 'PENDING': 'Pending',
                 'TIE': 'Tie', 'DNP': 'DNP', 'LATE_EXCLUDED': 'Excluded'}.get(outcome, outcome)
        cells = [name, compact_result_stat(row.get('Stat', '')), number(row.get('Line')),
                 ('↑ ' if direction == 'MORE' else '↓ ') + direction,
                 number(row.get('Projection')), number(row.get('Actual')), label,
                 {'prizepicks': 'PrizePicks', 'draftkings': 'DraftKings', 'kalshi': 'Kalshi'}.get(str(row.get('Platform')), str(row.get('Platform', '')))]
        html.append('<tr>')
        for index, value in enumerate(cells):
            attrs = (' class="sl-outcome ' + css + '"') if index == 6 else ''
            if index == 3:
                attrs = ' class="' + ('hit' if direction == 'MORE' else 'miss') + '"'
            html.append('<td' + attrs + '>' + escape(str(value)) + '</td>')
        html.append('</tr>')
    html.append('</tbody></table></div>')
    return ''.join(html)


def compact_audit_table(records, columns):
    """Render an escaped, readable table with explicitly selected public columns."""
    from html import escape
    parts = ['<div class="sl-results-scroll"><table class="sl-results"><thead><tr>']
    parts.extend('<th>' + escape(label) + '</th>' for key, label in columns)
    parts.append('</tr></thead><tbody>')
    for record in records:
        parts.append('<tr>')
        for key, label in columns:
            value = record.get(key)
            parts.append('<td>' + escape('—' if value is None else str(value)) + '</td>')
        parts.append('</tr>')
    parts.append('</tbody></table></div>')
    return ''.join(parts)


def render_results_dashboard(wf, st):
    st.markdown('### Results')
    active = bool(st.session_state.get('_audit_active'))
    if st.button('Grade all completed games', type='primary', disabled=active, key='grade_all_once'):
        st.session_state['_audit_active'] = True
        st.rerun()
    st.caption('One click checks all saved picks. If interrupted by a time limit, grading continues automatically while this page stays open. No paid odds scan.')
    report = wf.store.get('grade_run', 'last') or {}
    if active:
        st.info(f"Grading all completed games… {report.get('resolved', 0)}/{report.get('total', 0)} resolved.")
    elif report:
        st.caption(f"Last check: {report['resolved']}/{report['total']} resolved · "
                   f"{report['unresolved']} awaiting finals, missing data or retry. A completed check does not mean every game is final.")
    research = wf.research_rows()
    if not research:
        st.info('No saved forecasts loaded. Restore a full workspace backup in Settings.')
        return
    frame = pd.DataFrame(research)
    dates = ['All dates'] + sorted(frame['Game date'].dropna().unique().tolist(), reverse=True)
    left, center, right = st.columns(3)
    date = left.selectbox('Game date (Pacific)', dates, key='results_date')
    platform = center.selectbox('Platform', ['All platforms'] + sorted(frame['Platform'].unique().tolist()), key='results_platform')
    outcome = right.selectbox('Result', ['All results', 'WIN', 'LOSS', 'PENDING', 'TIE', 'DNP', 'LATE_EXCLUDED'], key='results_outcome')
    visible = frame.copy()
    if date != 'All dates': visible = visible[visible['Game date'] == date]
    if platform != 'All platforms': visible = visible[visible['Platform'] == platform]
    if outcome != 'All results': visible = visible[visible['Outcome'] == outcome]
    wins = int((visible['Outcome'] == 'WIN').sum())
    losses = int((visible['Outcome'] == 'LOSS').sum())
    pending = int((visible['Outcome'] == 'PENDING').sum())
    metrics = st.columns(5)
    for column, label, value in zip(metrics,
        ['Picks', 'Wins', 'Losses', 'Hit rate', 'Pending'],
        [len(visible), wins, losses, f'{wins / (wins + losses):.1%}' if wins + losses else '—', pending]):
        column.metric(label, value)
    st.caption('Summary follows your filters. Hit rate excludes ties, DNP and excluded records. It is not ROI.')
    more_less = []
    for side in ['MORE', 'LESS']:
        selected = visible[visible['Pick'] == side]
        hits = int((selected['Outcome'] == 'WIN').sum())
        misses = int((selected['Outcome'] == 'LOSS').sum())
        more_less.append(f'{side}: {hits}W–{misses}L')
    st.caption(' · '.join(more_less))
    st.markdown('''<style>
    .sl-results-scroll {overflow-x:auto;border:1px solid #395268;border-radius:12px;}
    .sl-results {width:100%;border-collapse:collapse;background:#102234;color:#f3f8ff;font-size:.9rem;}
    .sl-results th {text-align:left;background:#183246;color:#c6d6e5;padding:12px 10px;white-space:nowrap;}
    .sl-results td {padding:10px;border-top:1px solid #2b4356;white-space:nowrap;}
    .sl-results tbody tr:nth-child(even) {background:#12293b;}
    .sl-results .hit {color:#62e6a5;font-weight:700;}
    .sl-results .miss {color:#ff929b;font-weight:700;}
    .sl-results .waiting {color:#f4cf7a;}
    </style>''', unsafe_allow_html=True)
    if len(visible):
        pages = max(1, math.ceil(len(visible) / 50))
        filters = (date, platform, outcome)
        if st.session_state.get('_results_filters') != filters or int(st.session_state.get('results_page', 1)) > pages:
            st.session_state.pop('results_page', None)
            st.session_state['_results_filters'] = filters
        page = int(st.number_input('Results page', min_value=1, max_value=pages, value=1, step=1, key='results_page'))
        st.markdown(result_table_html(visible.iloc[(page - 1) * 50:page * 50].to_dict('records')), unsafe_allow_html=True)
        st.caption(f'{len(visible)} matching picks · up to 50 per page')
    else:
        st.info('No results match these filters.')
    with st.expander('Audit details and learning'):
        if st.checkbox('Load audit details and learning', key='load_results_learning'):
            combined = wf.research_rows(across_platforms=True)
            decided = [r for r in combined if r['Outcome'] in ('WIN', 'LOSS')]
            combined_wins = sum(r['Outcome'] == 'WIN' for r in decided)
            raw_count = len(wf.store.items('forecast'))
            st.caption(f'{raw_count - len(research)} duplicate records excluded from platform totals; originals retained.')
            st.caption(f'Combined model record across all dates: {combined_wins}W–{len(decided) - combined_wins}L. Identical cross-platform markets counted once.')
            st.caption(f"{len(wf.store.items('model'))} learned stat models active. Updates require a chronological holdout; a successful grading run alone does not promote a model.")
    st.download_button('Download full audit CSV', frame.to_csv(index=False), 'nba_audit.csv', 'text/csv')


def render_workflow(wf, st, rows, mode="settings"):
    if mode == 'results':
        render_results_dashboard(wf, st)
        if not st.checkbox('Show detailed accuracy, ROI and unresolved records', key='load_detailed_results'):
            return
    st.subheader("NBA workflow")

    st.caption(
        "Saved scans → complete supported board → "
        "frozen forecasts → completed-game grades → "
        "learning review. No odds calls are made here."
    )

    if wf.store.remote:
        st.caption(
            "Permanent database storage connected."
        )
    else:
        st.caption(
            "Temporary session storage active. Download a workspace backup "
            "after each slate; data may not survive restarts or redeploys."
        )

    with st.expander("Audit health and unresolved forecasts", expanded=False):
        status = wf.store.get("maintenance", "last") or {}
        st.caption("Last maintenance: " + str(status.get("run_at", "Never")))
        if status.get("grading"):
            g = status["grading"]
            st.caption("This check only — these counts are not your lifetime results.")
            checks = st.columns(3)
            checks[0].metric("Newly graded", g.get("graded", 0))
            checks[1].metric("Still pending", g.get("pending", 0))
            checks[2].metric("Errors this check", g.get("errors", 0))
            if not g.get("attempted", 0) and not g.get("pending", 0):
                st.info("No additional pending picks found in this check. Previous grades remain in Results.")
            elif g.get("remaining", 0):
                st.caption(f"{g['remaining']} picks still queued for the next automatic grading pass.")
            if g.get("DNP", 0) or g.get("ties", 0):
                st.caption(f"DNP: {g.get('DNP', 0)} · Ties: {g.get('ties', 0)}")
            if g.get("future_start", 0) and not g.get("due", 0):
                st.info(
                    f"All {g['future_start']} pending forecasts have recorded start times in the future "
                    f"(earliest: {g.get('earliest_future_start')}). "
                    "No ESPN boxscore requests are made until the recorded start passes. "
                    "If the games already happened, the saved event/start mapping needs correction."
                )
            if g.get("late_excluded", 0):
                st.warning(f"{g['late_excluded']} post-start forecasts excluded from pregame scoring.")
        errors = [{"Forecast ID": key, **value}
                  for key, value in wf.store.items("audit_error")
                  if not value.get("resolved")]
        if errors:
            st.warning(f"{len(errors)} forecast(s) need another grading attempt")
            st.caption("Original picks are retained. Recheck completed games to retry eligible records.")
            st.download_button("Download grading error details", pd.DataFrame(errors).to_csv(index=False),
                               "nba_grading_errors.csv", "text/csv")
        else:
            st.caption("No unresolved grading errors recorded.")
        if st.button("Recheck all completed games", key="audit_retry_now"):
            st.session_state['_audit_active'] = True
            st.rerun()

    if mode == "settings":
        with st.expander("Full workspace backup / restore"):
            if st.button("Prepare full workspace backup", key="prepare_full_backup"):
                st.session_state["_prepared_full_backup"] = wf.store.export()
                st.session_state["_prepared_full_backup_at"] = now()
            backup = st.session_state.get("_prepared_full_backup")
            if backup:
                st.caption("Backup snapshot prepared (UTC): " + st.session_state["_prepared_full_backup_at"])
                st.download_button(
                    "Download forecasts, grades, model and board", backup,
                    "sports_lab_workspace.json", "application/json",
                )
            st.caption("Preparing a full backup downloads the workspace once. Browsing this panel does not download the database.")

            uploaded = st.file_uploader(
                "Restore full workspace",
                type=["json"],
                key="full_restore",
            )

            if st.button(
                "Restore missing workspace records",
                disabled=uploaded is None,
            ):
                try:
                    if uploaded.size > 50 * 1024 * 1024:
                        raise ValueError(
                            "Maximum backup size is 50 MB."
                        )

                    count = wf.store.restore(
                        json.loads(uploaded.getvalue())
                    )

                    restored_board = wf.store.get("board", "board")
                    if restored_board:
                        st.session_state.update(validate_board(restored_board))
                    restored_count = len(wf.store.items("forecast"))
                    st.success(f"Checked {count} records. {restored_count} saved forecasts available. "
                               "Open Results to view earlier picks. No browser refresh needed.")

                except Exception as error:
                    st.error(str(error))

    if mode == "settings" or rows:
        if mode == "settings":
            preparation_panel(wf, st, rows)
        saved_index = forecast_index(wf.store.items("forecast")) if rows else {}
        calculated = wf.board(
            [card for card in preparation_rows(rows)
             if saved_prediction(saved_index, card)],
            wf.e["current_season"]()) if rows else []
        st.session_state["calculated_board"] = calculated

        calculated = st.session_state.get(
            "calculated_board",
            [],
        )

        if calculated:
            frame = pd.DataFrame(calculated)

            if "Ranking score" in frame:
                frame = frame.sort_values(
                    "Ranking score",
                    ascending=False,
                    na_position="last",
                )

            st.dataframe(
                frame,
                hide_index=True,
                use_container_width=True,
            )

            st.download_button(
                "Download calculated board",
                frame.to_csv(index=False),
                "nba_calculated_board.csv",
                "text/csv",
            )

            st.caption(
                "Rank uses projected distance from the line "
                "divided by recent stat variability. "
                "It is not a calibrated win probability "
                "or a profit ranking. Unavailable players "
                "stay visible with the reason; supported "
                "calculations receive MORE or LESS."
            )

    if mode == "settings":
        return

    with st.expander("PrizePicks estimate accuracy", expanded=False):
        st.caption("Compare saved pregame estimates with actual results. Line types stay separate; Unverified means the original tier was not confirmed. These estimates are experimental.")
        calibration = wf.probability_calibration()
        if calibration:
            calibration_frame = pd.DataFrame(calibration)
            st.markdown(compact_audit_table(calibration, [
                ('Tier', 'Line type'), ('Predicted bucket', 'Estimated range'),
                ('Decided', 'Decided'), ('Hits', 'Wins'), ('Misses', 'Losses'),
                ('Actual hit rate', 'Actual hit rate')]), unsafe_allow_html=True)
            st.caption("Hit rate = wins ÷ (wins + losses). Small groups are uncertain; ties, DNP and pending picks are excluded.")
            with st.expander("Sample size and uncertainty"):
                st.markdown(compact_audit_table(calibration, [
                    ('Tier', 'Line type'), ('Predicted bucket', 'Estimated range'),
                    ('Average predicted (decided)', 'Average estimate'),
                    ('Actual 95% interval', '95% interval'), ('Forecasts', 'All picks'),
                    ('Ties', 'Ties'), ('DNP', 'DNP'), ('Pending', 'Pending')]), unsafe_allow_html=True)
            st.download_button("Download estimate accuracy CSV", calibration_frame.to_csv(index=False),
                               "prizepicks_probability_audit.csv", "text/csv")
        else:
            st.info("No saved PrizePicks estimates to compare yet. Missing historical percentages are not invented.")

    with st.expander("Paper performance by platform", expanded=False):
        st.caption("Hypothetical performance of frozen picks. The direction benchmark and returns using saved prices are shown separately.")
        roi_rows = wf.model_roi_rows()
        if not roi_rows:
            st.info("No paper results yet. Saved forecasts populate this section as games finish.")
        else:
            frame = pd.DataFrame(roi_rows)
            for platform, group in frame.groupby("Platform"):
                title = {'prizepicks': 'PrizePicks', 'draftkings': 'DraftKings', 'kalshi': 'Kalshi'}.get(str(platform), str(platform))
                st.markdown('#### ' + title)
                stake = float(group["Benchmark stake (units)"].sum())
                profit = float(group["Benchmark profit (units)"].sum())
                metrics = st.columns(3)
                metrics[0].metric("Benchmark picks", int(stake))
                metrics[1].metric("Net benchmark units", f"{profit:+.1f}")
                metrics[2].metric("Even-money benchmark", f"{profit / stake:.1%}" if stake else "—")
                st.caption("Direction benchmark: +1 unit per win, −1 per loss. This is not a platform payout.")
                priced = group[group["Paper settled"]]
                cash_cost = float(priced["Priced paper cost ($)"].sum())
                cash_profit = float(priced["Priced paper profit ($)"].sum())
                if cash_cost:
                    metrics = st.columns(3)
                    metrics[0].metric("Priced settled picks", len(priced))
                    metrics[1].metric("Paper profit", f"${cash_profit:+.2f}")
                    metrics[2].metric("Priced paper ROI", f"{cash_profit / cash_cost:.1%}")
                else:
                    st.info("Priced paper ROI unavailable — no settled entries with a usable saved cost. This does not mean a 0% return.")
            with st.expander("Individual paper results"):
                platform = st.selectbox("Paper platform", ['All platforms'] + sorted(frame['Platform'].unique().tolist()), key='paper_platform')
                detail = frame if platform == 'All platforms' else frame[frame['Platform'] == platform]
                detail = detail.copy()
                detail['Stat'] = detail['Stat'].map(compact_result_stat)
                detail['Benchmark profit (units)'] = detail['Benchmark profit (units)'].map(lambda value: f'{float(value):+.1f}')
                pages = max(1, math.ceil(len(detail) / 50))
                if st.session_state.get('_paper_filter') != platform or int(st.session_state.get('paper_page', 1)) > pages:
                    st.session_state.pop('paper_page', None)
                    st.session_state['_paper_filter'] = platform
                page = int(st.number_input('Paper results page', min_value=1, max_value=pages, value=1, step=1, key='paper_page'))
                st.markdown(compact_audit_table(detail.iloc[(page-1)*50:page*50].to_dict('records'), [
                    ('Player', 'Player'), ('Stat', 'Prop'), ('Line', 'Line'),
                    ('Model side', 'Pick'), ('Result', 'Result'),
                    ('Benchmark profit (units)', 'Net units'), ('Platform', 'Platform')]), unsafe_allow_html=True)
                st.caption(f'{len(detail)} matching picks · up to 50 per page. Internal IDs and pricing details remain in the CSV.')
            st.download_button("Download full paper performance CSV", frame.to_csv(index=False),
                               "model_paper_roi.csv", "text/csv")

        st.caption(
            "The 1:1 benchmark compares model directions; "
            "it is not a PrizePicks payout. DraftKings "
            "priced paper ROI uses a captured same-line "
            "price and flat $1 stake. Kalshi uses one "
            "hypothetical contract, verified displayed "
            "depth and an estimated taker fee; its "
            "official final value handles fair-price "
            "settlements. Missing or stale prices are "
            "never invented. PrizePicks entry ROI remains "
            "separate until a multi-pick entry and "
            "payout rules are frozen."
        )


# ================= STREAMLIT APPLICATION =================

from datetime import datetime, timedelta, timezone
from pathlib import Path
import html
import json
import re
import sqlite3
import unicodedata

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components


st.set_page_config(
    page_title="Elite Individual Analytics Lab",
    page_icon="🎯",
    layout="wide",
)

# Keep native controls readable even when the browser selected a light theme.
st.markdown("""<style id="sports-lab-readable-controls">
[data-testid="stHeader"] {background:#07121f !important;color:#f3f8ff !important;}
[data-testid="stToolbar"] button, [data-testid="stMainMenu"] button {
 color:#f3f8ff !important;background:#142536 !important;}
.stApp [data-testid="stAlertContainer"], .stApp [data-testid="stAlert"] {
 background:#15283a !important;color:#f3f8ff !important;border-radius:12px;}
.stApp [data-testid="stAlertContainer"] p,
.stApp [data-testid="stAlertContainer"] li,
.stApp [data-testid="stAlertContainer"] span,
.stApp [data-testid="stAlert"] p,
.stApp [data-testid="stAlert"] a {color:#f3f8ff !important;}
.stApp [data-testid="stDownloadButton"] button,
.stApp [data-testid="stButton"] button,
.stApp [data-testid="stFormSubmitButton"] button {
 background:#142536 !important;color:#f3f8ff !important;border:1px solid #63829c !important;}
.stApp [data-testid="stDownloadButton"] button p,
.stApp [data-testid="stButton"] button p {color:inherit !important;}
.stApp button:disabled {opacity:.55;}
.stApp [data-testid="stTextInput"] input,
.stApp [data-testid="stNumberInput"] input,
.stApp [data-testid="stTextArea"] textarea,
.stApp [data-baseweb="select"] > div {
 background:#142536 !important;color:#f3f8ff !important;}
.stApp input::placeholder, .stApp textarea::placeholder {color:#b6c7d7 !important;}
.stApp [data-testid="stExpander"] details,
.stApp [data-testid="stFileUploaderDropzone"] {
 background:#102234 !important;color:#f3f8ff !important;}
.stApp [data-testid="stFileUploader"] button,
.stApp [data-testid="stFileUploaderDropzone"] button,
.stApp [data-testid="stFileUploaderDropzoneButton"],
.stApp [data-testid="stNumberInput"] button {
 background:#142536 !important;color:#f3f8ff !important;
 border:1px solid #63829c !important;border-radius:10px;}
.stApp [data-testid="stFileUploader"] button:hover {
 background:#203e55 !important;border-color:#37d6f7 !important;}
.stApp [data-testid="stFileUploader"] button p,
.stApp [data-testid="stFileUploader"] button span,
.stApp [data-testid="stFileUploader"] button svg,
.stApp [data-testid="stFileUploaderDropzone"] p,
.stApp [data-testid="stFileUploaderDropzone"] span,
.stApp [data-testid="stFileUploaderDropzone"] small,
.stApp [data-testid="stFileUploaderFileName"],
.stApp [data-testid="stFileUploaderFileData"] small {
 color:#f3f8ff !important;}
.stApp [data-testid="stTextInput"] [data-baseweb="base-input"],
.stApp [data-testid="stNumberInput"] [data-baseweb="base-input"],
.stApp [data-testid="stFileUploaderFile"] {
 background:#142536 !important;color:#f3f8ff !important;}
</style>""", unsafe_allow_html=True)

BASE = "https://api.oddspapi.io/v4"
BOOKS = ("prizepicks", "kalshi", "draftkings")

ESPN_BASE = (
    "https://site.api.espn.com/apis/site/v2/"
    "sports/basketball/nba/"
)

HISTORY_DB = Path("sports_lab_history.sqlite3")


def setting(name, default=""):
    import os

    if os.getenv(name):
        return os.environ[name]

    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


# ---------------- ODDS CONNECTION ----------------

def api_get(path, params):
    key = setting("ODDS_API_KEY", "")

    if not key:
        raise ValueError(
            "Set ODDS_API_KEY in Streamlit Secrets."
        )

    try:
        response = requests.get(
            BASE + path,
            params={
                **params,
                "apiKey": key,
            },
            timeout=60,
        )

    except requests.RequestException:
        raise ValueError(
            "Unable to connect to OddsPAPI."
        ) from None

    if response.status_code != 200:
        raise ValueError(
            f"OddsPAPI {path}: "
            f"HTTP {response.status_code}."
        )

    try:
        return response.json()
    except ValueError:
        raise ValueError(
            "OddsPAPI returned invalid JSON."
        ) from None


SLATE_TIMEZONE = "America/Los_Angeles"


def slate_day(reference=None):
    stamp = pd.Timestamp.now(tz="UTC") if reference is None else pd.to_datetime(reference, utc=True)
    return stamp.tz_convert(SLATE_TIMEZONE).date().isoformat()


def today_pregame(start_value, reference=None):
    """A future tipoff on the user's Pacific calendar date, never a UTC-date guess."""
    current = pd.Timestamp.now(tz="UTC") if reference is None else pd.to_datetime(reference, utc=True)
    start = utc_timestamp(start_value)
    return bool(pd.notna(start) and start > current
                and start.tz_convert(SLATE_TIMEZONE).date() == current.tz_convert(SLATE_TIMEZONE).date())


@st.cache_data(ttl=1800, show_spinner=False)
def _today_nba_fixtures(day):
    # The Pacific day is part of the cache key, so yesterday's slate cannot leak
    # across local midnight. The final filter also removes games that have begun.
    begin = pd.Timestamp(day, tz=SLATE_TIMEZONE)
    end = begin + pd.DateOffset(days=1)
    data = api_get("/fixtures", {
        "sportId": 11,
        "from": begin.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": end.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
        "statusId": 0,
    })
    if not isinstance(data, list):
        raise ValueError("Unexpected fixtures response structure.")
    games = []
    for game in data:
        if not isinstance(game, dict) or not game.get("fixtureId"):
            continue
        if not ("nba" in str(game.get("tournamentName", "")).lower()
                or str(game.get("tournamentSlug", "")).lower() == "nba"):
            continue
        start = pd.to_datetime(game.get("startTime"), utc=True, errors="coerce")
        if pd.notna(start) and begin <= start < end:
            games.append(game)
    return sorted(games, key=lambda game: str(game.get("startTime", "")))


def scan_schedule_preview(day, fetch, reference=None):
    """Estimate coverage from public ESPN schedules, without an odds request."""
    begin = pd.Timestamp(day, tz=SLATE_TIMEZONE)
    end = begin + pd.DateOffset(days=1)
    # Include the next UTC date, then enforce the Pacific calendar boundary.
    dates = begin.strftime('%Y%m%d') + '-' + end.strftime('%Y%m%d')
    payload = fetch(ESPN_BASE + 'scoreboard', {'dates': dates, 'limit': 1000}, ttl_seconds=300)
    if not isinstance(payload, dict) or not isinstance(payload.get('events'), list):
        raise ValueError('Schedule preview unavailable')
    games = {}
    for event in payload['events']:
        start = pd.to_datetime(event.get('date'), utc=True, errors='coerce')
        status = (event.get('status') or {}).get('type') or {}
        if (event.get('id') and today_pregame(start, reference)
                and status.get('state') == 'pre' and not status.get('completed')):
            games[str(event['id'])] = {'Game': event.get('name', 'NBA game'),
                'Tipoff (Pacific)': start.tz_convert(SLATE_TIMEZONE).strftime('%b %d · %I:%M %p')}
    return list(games.values())


def merge_scan_rows(previous, decoded, successful_fixtures):
    """Replace only successful scans; retain native imports and failed-game data."""
    successful = {str(value) for value in successful_fixtures}
    retained = [row for row in previous
        if today_pregame(row.get('Start (UTC)')) and (
            str(row.get('Fixture ID')) not in successful
            or row.get('Tier Source') == 'prizepicksapi.com'
            or row.get('Kalshi Source') == 'public API')]
    return retained + decoded


def upcoming_nba():
    return [game for game in _today_nba_fixtures(slate_day())
            if today_pregame(game.get("startTime"))]




@st.cache_data(ttl=1800, show_spinner=False)
def market_catalog():
    data = api_get(
        "/markets",
        {"language": "en"},
    )

    if isinstance(data, dict):
        data = data.get(
            "markets",
            data.get("data", []),
        )

    if not isinstance(data, list):
        raise ValueError(
            "Unexpected market catalog structure."
        )

    return {
        str(market["marketId"]): market
        for market in data
        if isinstance(market, dict)
        and market.get("marketId") is not None
        and str(market.get("sportId")) == "11"
    }


@st.cache_data(ttl=1800, show_spinner=False)
def game_odds(fixture_id):
    return api_get(
        "/odds",
        {
            "fixtureId": fixture_id,
            "bookmakers": ",".join(BOOKS),
            "language": "en",
            "verbosity": 3,
        },
    )


def numeric_line(value):
    if isinstance(value, bool) or value is None:
        return None

    try:
        number = float(value)
        return (
            number
            if 0 <= number <= 1000
            else None
        )
    except (ValueError, TypeError):
        return None


def outcome_direction(definition, outcome_id):
    options = definition.get("outcomes", [])

    if not isinstance(options, list):
        return None

    for option in options:
        if (
            isinstance(option, dict)
            and str(option.get("outcomeId"))
            == str(outcome_id)
        ):
            name = str(
                option.get("outcomeName", "")
            ).strip().lower()

            return {
                "over": "MORE",
                "under": "LESS",
            }.get(name)

    return None


def explicit_tier(*records):
    fields = (
        "lineType",
        "tier",
        "label",
        "badge",
        "projectionType",
    )

    text = " ".join(
        str(record[field]).lower()
        for record in records
        if isinstance(record, dict)
        for field in fields
        if record.get(field) is not None
    )

    for token, label in (
        ("goblin", "Goblin"),
        ("demon", "Demon"),
        ("standard", "Standard"),
        ("regular", "Standard"),
    ):
        if re.search(
            r"\b" + token + r"\b",
            text,
        ):
            return label

    return "Unverified"
def decode_game(game, odds, catalog):
    fixture_id = str(game["fixtureId"])
    matchup = (
        f"{game.get('participant1Name', '?')} vs "
        f"{game.get('participant2Name', '?')}"
    )
    books = odds.get("bookmakerOdds", {}) if isinstance(odds, dict) else {}
    if not isinstance(books, dict):
        books = {}

    rows, summaries, seen = [], [], set()

    for book in BOOKS:
        book_data = books.get(book, {})
        if not isinstance(book_data, dict):
            book_data = {}
        markets = book_data.get("markets", {})
        if not isinstance(markets, dict):
            markets = {}

        count = 0
        for market_id, market in markets.items():
            if not isinstance(market, dict):
                continue
            definition = catalog.get(str(market_id), {})
            if definition.get("playerProp") is not True:
                continue
            if market.get("marketActive") is not True:
                continue

            raw_line = numeric_line(definition.get("handicap"))
            line = raw_line if raw_line is not None and raw_line > 0 else None
            outcomes = market.get("outcomes", {})
            if not isinstance(outcomes, dict):
                continue

            for outcome_id, outcome in outcomes.items():
                if not isinstance(outcome, dict):
                    continue
                direction = outcome_direction(definition, outcome_id)
                players = outcome.get("players", {})
                if not isinstance(players, dict):
                    continue

                for player_id, player in players.items():
                    if not isinstance(player, dict):
                        continue
                    if player.get("active") is not True:
                        continue
                    name = player.get("playerName")
                    if not isinstance(name, str) or not name.strip():
                        continue

                    key = (
                        fixture_id, book, str(market_id),
                        str(outcome_id), str(player_id),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    count += 1

                    rows.append({
                        "Game": matchup,
                        "Fixture ID": fixture_id,
                        "Start (UTC)": game.get("startTime"),
                        "Platform": book,
                        "Player": name.strip(),
                        "Stat": definition.get("marketName", "Unknown"),
                        "Stat Key": definition.get("marketType", "Unknown"),
                        "Period": definition.get("period", "Unknown"),
                        "Line": line,
                        "Raw Handicap": raw_line,
                        "Direction": direction,
                        "Line Status": (
                            "Verified positive threshold"
                            if line is not None
                            else "Review: zero or missing threshold"
                        ),
                        "Line Type": explicit_tier(
                            player, outcome, market, definition
                        ),
                        "Odds": player.get("priceAmerican"),
                        "Market ID": str(market_id),
                        "Outcome ID": str(outcome_id),
                        "Player ID": str(player_id),
                        "mainLine Flag": player.get(
                            "mainLine", player.get("mainline")
                        ),
                        "Decoded": line is not None and direction is not None,
                    })

        summaries.append({
            "Game": matchup,
            "Platform": book,
            "Returned Markets": len(markets),
            "Active Named Prop Outcomes": count,
            "Bookmaker Active Flag": book_data.get("bookmakerIsActive"),
        })

    return rows, summaries


# ---------------- ESPN RESPONSE CACHE ----------------

def history_connection():
    con = sqlite3.connect(str(HISTORY_DB), timeout=30)
    con.execute(
        "CREATE TABLE IF NOT EXISTS responses "
        "(key TEXT PRIMARY KEY, fetched TEXT, payload TEXT)"
    )
    return con


def saved_fetch(url, params, ttl_seconds=3600):
    key = url + "?" + json.dumps(params, sort_keys=True)
    con = history_connection()
    try:
        found = con.execute(
            "SELECT fetched, payload FROM responses WHERE key=?",
            (key,),
        ).fetchone()
    finally:
        con.close()

    fetched_now = datetime.now(timezone.utc)
    if found:
        age = (
            fetched_now - datetime.fromisoformat(found[0])
        ).total_seconds()
        if 0 <= age < ttl_seconds:
            return json.loads(found[1])

    response = requests.get(url, params=params, timeout=25)
    response.raise_for_status()
    payload = response.json()
    fetched_at = datetime.now(timezone.utc).isoformat()

    con = history_connection()
    try:
        with con:
            con.execute(
                "INSERT OR REPLACE INTO responses VALUES (?,?,?)",
                (key, fetched_at, json.dumps(payload)),
            )
    finally:
        con.close()

    return payload


def cached_response_time(url, params):
    key = url + "?" + json.dumps(params, sort_keys=True)
    con = history_connection()
    try:
        row = con.execute(
            "SELECT fetched FROM responses WHERE key=?",
            (key,),
        ).fetchone()
    finally:
        con.close()
    return row[0] if row else None


def stat_number(value):
    text = str(value).strip()
    if text in ("", "--", "-", "None"):
        return None
    return float(text)


def stat_minutes(value):
    text = str(value).strip()
    if ":" in text:
        parts = text.split(":")
        return float(parts[0]) + float(parts[1]) / 60
    return stat_number(text)


# ---------------- PLAYER DISCOVERY ----------------

@st.cache_data(ttl=3600, show_spinner=False)
def load_nba_player_logs(season):
    year = int(season.split("-")[0])
    season_start = datetime(year, 10, 1, tzinfo=timezone.utc)
    season_end = datetime(year + 1, 7, 1, tzinfo=timezone.utc)
    current_time = datetime.now(timezone.utc)
    if current_time < season_start:
        raise ValueError("The selected season has not started.")

    end = min(current_time, season_end - timedelta(days=1))
    start = max(season_start, end - timedelta(days=6))
    games = {}
    day = start

    while day.date() <= end.date():
        board = saved_fetch(
            ESPN_BASE + "scoreboard",
            {"dates": day.strftime("%Y%m%d")},
        )
        if "events" not in board:
            raise ValueError("ESPN returned an unexpected scoreboard.")

        for event in board["events"]:
            completed = event.get("status", {}).get(
                "type", {}
            ).get("completed", False)
            regular = event.get("season", {}).get("type") == 2
            if completed and regular:
                games[str(event["id"])] = event
        day += timedelta(days=1)

    if not games:
        raise ValueError(
            "No completed regular-season games returned for "
            f"{start:%Y-%m-%d} through {end:%Y-%m-%d}."
        )

    rows = []
    for game_id, event in games.items():
        summary = saved_fetch(
            ESPN_BASE + "summary", {"event": game_id}
        )
        competitors = event["competitions"][0]["competitors"]
        teams = {str(item["team"]["id"]): item for item in competitors}
        groups = summary.get("boxscore", {}).get("players", [])
        if not groups:
            raise ValueError(f"No player box score for game {game_id}.")

        game_rows = []
        for group in groups:
            team_id = str(group["team"]["id"])
            team = teams[team_id]
            opponent = next(
                item for key, item in teams.items() if key != team_id
            )
            home = team["homeAway"] == "home"
            team_name = team["team"]["abbreviation"]
            opponent_name = opponent["team"]["abbreviation"]

            for section in group.get("statistics", []):
                labels = section.get("labels", [])
                required = {
                    "MIN", "PTS", "REB", "AST",
                    "STL", "BLK", "TO", "3PT",
                }
                if not required.issubset(labels):
                    raise ValueError(
                        f"Unexpected stat columns: game {game_id}."
                    )

                for athlete in section.get("athletes", []):
                    if athlete.get("didNotPlay"):
                        continue
                    values = athlete.get("stats", [])
                    if not values:
                        continue
                    if len(values) != len(labels):
                        raise ValueError(
                            f"Incomplete stat row: game {game_id}."
                        )

                    stats = dict(zip(labels, values))
                    played = stat_minutes(stats["MIN"])
                    if played is None:
                        raise ValueError(
                            f"Missing minutes: game {game_id}."
                        )
                    player = athlete["athlete"]

                    game_rows.append({
                        "PLAYER_ID": str(player["id"]),
                        "PLAYER_NAME": player["displayName"],
                        "GAME_ID": game_id,
                        "GAME_DATE": event["date"],
                        "TEAM_ID": team_id,
                        "OPPONENT_ID": str(opponent["team"]["id"]),
                        "HOME_AWAY": "Home" if home else "Away",
                        "MATCHUP": (
                            f"{team_name} "
                            f"{'vs.' if home else '@'} {opponent_name}"
                        ),
                        "MIN": played,
                        "PTS": stat_number(stats["PTS"]),
                        "REB": stat_number(stats["REB"]),
                        "AST": stat_number(stats["AST"]),
                        "STL": stat_number(stats["STL"]),
                        "BLK": stat_number(stats["BLK"]),
                        "TOV": stat_number(stats["TO"]),
                        "FG3M": stat_number(
                            str(stats["3PT"]).split("-")[0]
                        ),
                        "SOURCE": "ESPN",
                    })

        if not game_rows:
            raise ValueError(
                f"No usable player records: game {game_id}."
            )
        rows.extend(game_rows)

    logs = pd.DataFrame(rows)
    logs["GAME_DATE"] = pd.to_datetime(
        logs["GAME_DATE"], utc=True, errors="raise"
    )
    logs = logs.drop_duplicates(["PLAYER_ID", "GAME_ID"])

    return {
        "logs": logs.sort_values("GAME_DATE", ascending=False),
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "coverage_start": start.strftime("%Y-%m-%d"),
        "coverage_end": end.strftime("%Y-%m-%d"),
    }


# ---------------- DEEP PLAYER HISTORY ----------------

def normalize_player(name):
    name = str(name)
    if "," in name:
        last, first = name.split(",", 1)
        name = first.strip() + " " + last.strip()
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", name.lower())


@st.cache_data(ttl=3600, show_spinner=False)
def espn_teams():
    data = saved_fetch(ESPN_BASE + "teams", {"limit": 100})
    return [
        item["team"]
        for item in data["sports"][0]["leagues"][0]["teams"]
    ]


def resolve_player(name):
    seed = st.session_state.get(
        "nba_history", {}
    ).get("logs", pd.DataFrame())
    if seed.empty:
        return None

    matches = seed[
        seed["PLAYER_NAME"].map(normalize_player)
        == normalize_player(name)
    ]
    ids = matches["PLAYER_ID"].astype(str).unique()
    if len(ids) == 1:
        return ids[0], matches.iloc[0]["PLAYER_NAME"]
    return None


def parse_player_log(data, player_id, player_name, season):
    required = {
        "MIN", "PTS", "REB", "AST", "STL", "BLK", "TO", "3PT",
    }
    if not required.issubset(data.get("labels", [])):
        raise ValueError(
            "ESPN game-log fields are missing. "
            "No averages were substituted."
        )

    rows = []
    for part in data.get("seasonTypes", []):
        if "Regular Season" not in part.get("displayName", ""):
            continue
        for category in part.get("categories", []):
            for item in category.get("events", []):
                game_id = str(item["eventId"])
                event = data.get("events", {}).get(game_id)
                if not event:
                    raise ValueError(
                        "ESPN returned a game without matchup metadata."
                    )
                values = item.get("stats", [])
                if len(values) != len(data["labels"]):
                    raise ValueError("Incomplete player game-log row.")

                stats = dict(zip(data["labels"], values))
                minute = stat_minutes(stats["MIN"])
                if minute is None or minute <= 0:
                    continue
                if event.get("atVs") not in ("vs", "@"):
                    raise ValueError("Unknown home/away marker.")

                home = event["atVs"] == "vs"
                team = event.get("team", {})
                opponent = event["opponent"]
                rows.append({
                    "PLAYER_ID": str(player_id),
                    "PLAYER_NAME": player_name,
                    "GAME_ID": game_id,
                    "GAME_DATE": event["gameDate"],
                    "SEASON": season,
                    "TEAM_ID": str(team.get("id", "")),
                    "OPPONENT_ID": str(opponent["id"]),
                    "HOME_AWAY": "Home" if home else "Away",
                    "MATCHUP": (
                        f"{team.get('abbreviation', '?')} "
                        f"{'vs.' if home else '@'} "
                        f"{opponent['abbreviation']}"
                    ),
                    "MIN": minute,
                    "PTS": stat_number(stats["PTS"]),
                    "REB": stat_number(stats["REB"]),
                    "AST": stat_number(stats["AST"]),
                    "STL": stat_number(stats["STL"]),
                    "BLK": stat_number(stats["BLK"]),
                    "TOV": stat_number(stats["TO"]),
                    "FG3M": stat_number(
                        str(stats["3PT"]).split("-")[0]
                    ),
                    "SOURCE": "ESPN",
                })
    return rows


@st.cache_data(ttl=900, max_entries=600, show_spinner=False)
def fetch_player_history(player_id, player_name, season):
    year = int(season.split("-")[0])
    rows = []
    warnings = []
    for start in (year, year - 1):
        label = f"{start}-{str(start + 1)[-2:]}"
        url = (
            "https://site.web.api.espn.com/apis/common/v3/"
            "sports/basketball/nba/athletes/"
            f"{player_id}/gamelog"
        )
        try:
            data = saved_fetch(url, {"season": start + 1})
            parsed = parse_player_log(data, player_id, player_name, label)
        except Exception:
            if start == year:
                # Current-season data is required. Never replace it with old
                # averages after an error, and never accept malformed rows.
                raise
            warnings.append('Prior-season history unavailable; verified current-season data retained.')
            continue
        rows.extend(parsed)

    if not rows:
        raise ValueError(
            "No regular-season appearances returned "
            "for these two seasons."
        )
    frame = pd.DataFrame(rows).drop_duplicates(
        ["PLAYER_ID", "GAME_ID"]
    )
    frame["GAME_DATE"] = pd.to_datetime(
        frame["GAME_DATE"], utc=True, errors="raise"
    )
    frame = frame.sort_values("GAME_DATE", ascending=False)
    frame.attrs['history_warnings'] = warnings
    return frame


def market_columns(card):
    if str(card["Period"]).lower() != "result":
        return None
    name = str(card["Stat"]).lower()
    name = re.sub(r"\s*\(incl\.?\s*overtime\)\s*", "", name)
    name = name.replace("over under player ", "").strip()
    mapping = {
        "points": ["PTS"],
        "rebounds": ["REB"],
        "assists": ["AST"],
        "steals": ["STL"],
        "blocks": ["BLK"],
        "turnovers": ["TOV"],
        "3 point fg": ["FG3M"],
        "3 point field goals": ["FG3M"],
        "points + rebounds": ["PTS", "REB"],
        "points + assists": ["PTS", "AST"],
        "assists + rebounds": ["AST", "REB"],
        "rebounds + assists": ["REB", "AST"],
        "points + assists + rebounds": ["PTS", "AST", "REB"],
        "points + rebounds + assists": ["PTS", "REB", "AST"],
        "blocks + steals": ["BLK", "STL"],
        "steals + blocks": ["STL", "BLK"],
    }
    return mapping.get(name)


def window_summary(frame, line, label):
    count = len(frame)
    if not count:
        return {
            "Window": label, "Games": 0, "Average": None,
            "MORE": "Unavailable", "LESS": "Unavailable", "Ties": 0,
        }
    more = int((frame["VALUE"] > line).sum())
    less = int((frame["VALUE"] < line).sum())
    return {
        "Window": label,
        "Games": count,
        "Average": round(frame["VALUE"].mean(), 2),
        "MORE": f"{more/count:.1%} ({more}/{count})",
        "LESS": f"{less/count:.1%} ({less}/{count})",
        "Ties": count - more - less,
    }


# ---------------- ROSTER AND INJURIES ----------------
def render_player_availability(card):
    st.markdown("#### Roster & availability")
    identity = resolve_player(card["Player"])
    if identity is None:
        st.info(
            "A unique current ESPN player match is required. "
            "The roster feed may be unavailable or the name ambiguous."
        )
        return

    player_id, player_name = identity
    state_key = f"availability_v3_{card['Fixture ID']}_{player_id}"

    if st.button(
        "Refresh roster & injury report",
        key=f"refresh_{state_key}",
    ):
        st.session_state.pop(state_key, None)
        try:
            names = {
                normalize_player(name)
                for name in str(card["Game"]).split(" vs ")
            }
            teams = [
                team for team in espn_teams()
                if normalize_player(team["displayName"]) in names
            ]
            if len(teams) != 2:
                raise ValueError("Could not identify both matchup teams.")

            matches = []
            with st.spinner("Checking ESPN roster..."):
                for team in teams:
                    roster_url = ESPN_BASE + f"teams/{team['id']}/roster"
                    roster = saved_fetch(
                        roster_url, {}, ttl_seconds=300
                    )
                    for athlete in roster.get("athletes", []):
                        if str(athlete.get("id")) == str(player_id):
                            matches.append({
                                "team": team["displayName"],
                                "team_id": str(team["id"]),
                                "position": athlete.get(
                                    "position", {}
                                ).get("abbreviation", "Unavailable"),
                                "status": athlete.get(
                                    "status", {}
                                ).get("name", "Unavailable"),
                                "roster_timestamp": roster.get(
                                    "timestamp", "Unavailable"
                                ),
                                "roster_fetched_at": cached_response_time(
                                    roster_url, {}
                                ),
                            })

            if len(matches) != 1:
                raise ValueError(
                    "Player was not uniquely matched "
                    "to either matchup roster."
                )

            result = matches[0]
            result.update({
                "injuries": [],
                "injury_error": None,
                "injury_timestamp": "Unavailable",
                "injury_fetched_at": None,
            })

            try:
                injury_url = ESPN_BASE + "injuries"
                with st.spinner("Checking ESPN injury report..."):
                    report = saved_fetch(
                        injury_url, {}, ttl_seconds=60
                    )
                groups = report.get("injuries")
                if not isinstance(groups, list):
                    raise ValueError("Unexpected injury report format.")

                for group in groups:
                    for injury in group.get("injuries", []):
                        athlete = injury.get("athlete", {})
                        athlete_id = athlete.get("id")
                        if athlete_id is not None:
                            is_match = str(athlete_id) == str(player_id)
                        else:
                            is_match = (
                                normalize_player(
                                    athlete.get("displayName", "")
                                ) == normalize_player(player_name)
                                and str(group.get("id", ""))
                                == result["team_id"]
                            )
                        if is_match:
                            result["injuries"].append(injury)

                result["injury_timestamp"] = report.get(
                    "timestamp", "Unavailable"
                )
                result["injury_fetched_at"] = cached_response_time(
                    injury_url, {}
                )
            except Exception as error:
                result["injury_error"] = str(error)

            result["checked_at"] = datetime.now(timezone.utc).isoformat()
            st.session_state[state_key] = result

        except Exception as error:
            st.error(f"Availability check failed: {error}")

    result = st.session_state.get(state_key)
    if result is None:
        st.caption(
            "Use Refresh for the detailed report. "
            "The projection workflow also checks injuries automatically."
        )
        return

    st.write(f"**Player:** {player_name}")
    st.write(f"**Listed team:** {result['team']}")
    st.write(f"**Position:** {result['position']}")
    st.write(f"**Roster status:** {result['status']}")

    if result["injury_error"]:
        st.warning("Injury report unavailable: " + result["injury_error"])
    elif result["injuries"]:
        for injury in result["injuries"]:
            st.write(
                "**ESPN injury designation:** "
                + str(injury.get("status", "Unavailable"))
            )
            st.caption(
                "Injury entry updated: "
                + str(injury.get("date", "Unavailable"))
            )
            note = injury.get("shortComment") or injury.get("longComment")
            if note:
                st.write(str(note))
    else:
        st.info(
            "No matching injury entry was returned. "
            "This does not confirm availability for this game."
        )

    st.write("**Confirmed starter:** Unavailable")
    st.caption(
        f"Roster response timestamp: {result['roster_timestamp']} · "
        f"Injury feed timestamp: {result['injury_timestamp']}"
    )
    st.caption(
        "Roster fetched by app (UTC): "
        f"{result['roster_fetched_at'] or 'Unavailable'}"
    )
    st.caption(
        "Injury feed fetched by app (UTC): "
        f"{result['injury_fetched_at'] or 'Unavailable'}"
    )
    st.caption(f"Last button check (UTC): {result['checked_at']}")

    if result["injury_fetched_at"]:
        age = (
            datetime.now(timezone.utc)
            - datetime.fromisoformat(result["injury_fetched_at"])
        ).total_seconds()
        if age >= 60:
            st.warning(
                "The injury response is over 60 seconds old. "
                "Click Refresh to check again."
            )

    st.caption(
        "Manual refresh · Injury cache: up to 60 seconds · "
        "Roster cache: up to 5 minutes. "
        "Feed timestamps are not individual news-update times. "
        "Active roster status does not confirm playing or starting."
    )


# ---------------- TEAM SCORING ----------------

def render_team_scoring(card):
    st.markdown("#### Team scoring matchup")
    matchup_key = f"team_scoring_{card['Fixture ID']}"

    if st.button(
        "Load team scoring comparison",
        key=f"load_{matchup_key}",
    ):
        st.session_state.pop(matchup_key, None)
        try:
            participant_names = {
                normalize_player(name)
                for name in str(card["Game"]).split(" vs ")
            }
            matchup_teams = [
                team for team in espn_teams()
                if normalize_player(team["displayName"]) in participant_names
            ]
            if len(matchup_teams) != 2:
                raise ValueError("Could not identify both matchup teams.")

            team_rows, response_times = [], []
            with st.spinner("Loading ESPN team records..."):
                for team in matchup_teams:
                    team_url = ESPN_BASE + f"teams/{team['id']}"
                    team_params = {"enable": "stats"}
                    response = saved_fetch(
                        team_url, team_params, ttl_seconds=3600
                    )
                    returned_team = response.get("team", {})
                    if str(returned_team.get("id")) != str(team["id"]):
                        raise ValueError("ESPN returned an unexpected team.")

                    overall_records = [
                        record
                        for record in returned_team.get(
                            "record", {}
                        ).get("items", [])
                        if record.get("type") == "total"
                    ]
                    if len(overall_records) != 1:
                        raise ValueError("Overall team record unavailable.")

                    stats = {
                        item["name"]: item.get("value")
                        for item in overall_records[0].get("stats", [])
                    }
                    required = (
                        "gamesPlayed", "avgPointsFor", "avgPointsAgainst"
                    )
                    if any(stats.get(field) is None for field in required):
                        raise ValueError(
                            "Required team scoring fields are missing."
                        )
                    if float(stats["gamesPlayed"]) <= 0:
                        raise ValueError("No team games are available.")

                    team_rows.append({
                        "Team": team["displayName"],
                        "Games": int(stats["gamesPlayed"]),
                        "Points scored / game": round(
                            float(stats["avgPointsFor"]), 2
                        ),
                        "Points allowed / game": round(
                            float(stats["avgPointsAgainst"]), 2
                        ),
                    })
                    response_times.append(
                        cached_response_time(team_url, team_params)
                    )

            st.session_state[matchup_key] = {
                "rows": team_rows,
                "fetched": response_times,
            }
        except Exception as error:
            st.error(f"Team scoring comparison unavailable: {error}")

    result = st.session_state.get(matchup_key)
    if result:
        st.dataframe(
            pd.DataFrame(result["rows"]),
            hide_index=True,
            use_container_width=True,
        )
        for row, fetched in zip(result["rows"], result["fetched"]):
            st.caption(
                f"{row['Team']} · App fetched (UTC): "
                f"{fetched or 'Unavailable'}"
            )
        st.caption(
            "Source: ESPN current overall team records. "
            "This is not filtered to the historical-season selector "
            "or reconstructed as of an earlier fixture. "
            "Points allowed per game is not defensive efficiency "
            "or defense versus position."
        )


# ---------------- STATIC HISTORY GRAPH ----------------



# ---------------- TEAM EFFICIENCY ----------------
@st.cache_data(ttl=3600, show_spinner=False)
def load_team_advanced(season):
    from html.parser import HTMLParser

    class TeamTables(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tables = []
            self.table = None
            self.row = None
            self.cell = None

        def handle_starttag(self, tag, attrs):
            if tag == "table":
                self.table = []
            elif tag == "tr" and self.table is not None:
                self.row = []
            elif tag in ("td", "th") and self.row is not None:
                self.cell = []

        def handle_data(self, data):
            if self.cell is not None:
                self.cell.append(data)

        def handle_endtag(self, tag):
            if tag in ("td", "th") and self.cell is not None:
                self.row.append(
                    " ".join("".join(self.cell).split())
                )
                self.cell = None
            elif tag == "tr" and self.row is not None:
                self.table.append(self.row)
                self.row = None
            elif tag == "table" and self.table is not None:
                self.tables.append(self.table)
                self.table = None

    year = int(season.split("-")[0])
    if season != f"{year}-{str(year + 1)[-2:]}":
        raise ValueError("Invalid season format.")

    url = (
        f"https://www.nbastuffer.com/"
        f"{year}-{year + 1}-nba-team-stats/"
    )
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=(5, 15),
    )
    response.raise_for_status()

    parser = TeamTables()
    parser.feed(response.text)

    required = {"TEAM", "GP", "PACE", "oEFF", "dEFF"}
    table = next(
        (
            table for table in parser.tables
            if table and required.issubset(table[0])
        ),
        None,
    )
    if table is None:
        raise ValueError(
            "The expected regular-season team table was not found."
        )

    headers = table[0]
    records = []
    for values in table[1:]:
        if values == headers:
            continue
        if len(values) != len(headers):
            raise ValueError("Incomplete team statistics row.")
        records.append(dict(zip(headers, values)))

    names = {
        "Atlanta": "Atlanta Hawks",
        "Boston": "Boston Celtics",
        "Brooklyn": "Brooklyn Nets",
        "Charlotte": "Charlotte Hornets",
        "Chicago": "Chicago Bulls",
        "Cleveland": "Cleveland Cavaliers",
        "Dallas": "Dallas Mavericks",
        "Denver": "Denver Nuggets",
        "Detroit": "Detroit Pistons",
        "Golden State": "Golden State Warriors",
        "Houston": "Houston Rockets",
        "Indiana": "Indiana Pacers",
        "LA Clippers": "Los Angeles Clippers",
        "LA Lakers": "Los Angeles Lakers",
        "Memphis": "Memphis Grizzlies",
        "Miami": "Miami Heat",
        "Milwaukee": "Milwaukee Bucks",
        "Minnesota": "Minnesota Timberwolves",
        "New Orleans": "New Orleans Pelicans",
        "New York": "New York Knicks",
        "Oklahoma City": "Oklahoma City Thunder",
        "Orlando": "Orlando Magic",
        "Philadelphia": "Philadelphia 76ers",
        "Phoenix": "Phoenix Suns",
        "Portland": "Portland Trail Blazers",
        "Sacramento": "Sacramento Kings",
        "San Antonio": "San Antonio Spurs",
        "Toronto": "Toronto Raptors",
        "Utah": "Utah Jazz",
        "Washington": "Washington Wizards",
    }

    if (
        len(records) != 30
        or {row["TEAM"] for row in records} != set(names)
    ):
        raise ValueError(
            "Expected 30 unique NBA teams. No substitute data used."
        )

    rows = []
    for record in records:
        games, pace, offense, defense = (
            float(record[column])
            for column in ("GP", "PACE", "oEFF", "dEFF")
        )

        if not all(
            math.isfinite(value) and value > 0
            for value in (games, pace, offense, defense)
        ):
            raise ValueError("Missing or invalid team statistic.")

        if not games.is_integer():
            raise ValueError("Invalid game count.")

        rows.append({
            "TEAM_NAME": names[record["TEAM"]],
            "GP": int(games),
            "OFF_RATING": offense,
            "DEF_RATING": defense,
            "PACE": pace,
        })

    frame = pd.DataFrame(rows)

    for value, rank, ascending in (
        ("OFF_RATING", "OFF_RATING_RANK", False),
        ("DEF_RATING", "DEF_RATING_RANK", True),
        ("PACE", "PACE_RANK", False),
    ):
        frame[rank] = frame[value].rank(
            ascending=ascending,
            method="min",
        ).astype(int)

    return {
        "data": frame,
        "source_url": url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def render_team_advanced(card):
    st.markdown("#### Team efficiency & pace")
    season = st.session_state.get(
        "nba_history_season", current_season()
    )

    try:
        with st.spinner("Loading team efficiency and rankings..."):
            result = load_team_advanced(season)

        def team_key(name):
            return normalize_player(name).replace(
                "losangeles", "la"
            )

        names = {
            team_key(name)
            for name in str(card["Game"]).split(" vs ")
        }

        frame = result["data"]
        matched = frame[
            frame["TEAM_NAME"].map(team_key).isin(names)
        ].copy()

        if len(names) != 2 or len(matched) != 2:
            raise ValueError(
                "Both matchup teams could not be matched."
            )

    except Exception as error:
        st.error(
            "Team efficiency data unavailable. "
            "Other research remains available."
        )
        st.code(
            f"{type(error).__name__}: {error}",
            language="text",
        )
        return

    labels = {
        "TEAM_NAME": "Team",
        "GP": "Games",
        "OFF_RATING": "Offensive efficiency",
        "OFF_RATING_RANK": "Offense rank",
        "DEF_RATING": "Defensive efficiency",
        "DEF_RATING_RANK": "Defense rank",
        "PACE": "Pace",
        "PACE_RANK": "Pace rank",
    }

    st.dataframe(
        matched[list(labels)].rename(columns=labels),
        hide_index=True,
        use_container_width=True,
    )

    st.caption(
        f"Source: NBAstuffer · {season} regular season · "
        f"App fetched (UTC): {result['fetched_at']}."
    )
    st.markdown(
        f"[View source table]({result['source_url']})"
    )
    st.caption(
        "Offense/defense: points scored/allowed per 100 possessions. "
        "Pace: possessions per 48 minutes. "
        "Rankings calculated across all 30 teams; ties share a rank. "
        "Rank 1 means highest offense, lowest defense, or fastest pace."
    )
    st.caption(
        "Loads automatically when you open a breakdown. "
        "Shared responses cached for one hour. No OddsPAPI calls. "
        "Fetch time is not the publisher update time; "
        "source freshness is unconfirmed. "
        "These figures may differ from NBA Stats."
    )
    st.caption(
        "Season-to-date team figures, not reconstructed as of an "
        "earlier fixture. The projection below shows which "
        "matchup factors were available."
    )


@st.cache_data(ttl=3600, show_spinner=False)
def load_position_defense():
    from html.parser import HTMLParser

    class PositionTableParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.rows = []
            self.row = None
            self.cell = None

        def handle_starttag(self, tag, attrs):
            if tag == "tr":
                self.row = []
            elif tag in ("td", "th"):
                self.cell = []

        def handle_data(self, data):
            if self.cell is not None:
                self.cell.append(data)

        def handle_endtag(self, tag):
            if tag in ("td", "th") and self.cell is not None:
                if self.row is not None:
                    self.row.append(
                        " ".join(" ".join(self.cell).split())
                    )
                self.cell = None
            elif tag == "tr" and self.row is not None:
                self.rows.append(self.row)
                self.row = None

    url = "https://hashtagbasketball.com/nba-defense-vs-position"
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=(5, 20),
    )
    response.raise_for_status()

    parser = PositionTableParser()
    parser.feed(response.text)

    team_names = {
        "ATL": "Atlanta Hawks",
        "BOS": "Boston Celtics",
        "BKN": "Brooklyn Nets",
        "CHA": "Charlotte Hornets",
        "CHI": "Chicago Bulls",
        "CLE": "Cleveland Cavaliers",
        "DAL": "Dallas Mavericks",
        "DEN": "Denver Nuggets",
        "DET": "Detroit Pistons",
        "GS": "Golden State Warriors",
        "HOU": "Houston Rockets",
        "IND": "Indiana Pacers",
        "LAC": "Los Angeles Clippers",
        "LAL": "Los Angeles Lakers",
        "MEM": "Memphis Grizzlies",
        "MIA": "Miami Heat",
        "MIL": "Milwaukee Bucks",
        "MIN": "Minnesota Timberwolves",
        "NO": "New Orleans Pelicans",
        "NY": "New York Knicks",
        "OKC": "Oklahoma City Thunder",
        "ORL": "Orlando Magic",
        "PHI": "Philadelphia 76ers",
        "PHO": "Phoenix Suns",
        "POR": "Portland Trail Blazers",
        "SAC": "Sacramento Kings",
        "SA": "San Antonio Spurs",
        "TOR": "Toronto Raptors",
        "UTA": "Utah Jazz",
        "WAS": "Washington Wizards",
    }

    stat_indexes = {
        "Points": 2,
        "Threes made": 5,
        "Rebounds": 6,
        "Assists": 7,
        "Steals": 8,
        "Blocks": 9,
        "Turnovers": 10,
    }
    records = {}

    for cells in parser.rows:
        if len(cells) != 11:
            continue
        position = cells[0]
        if position not in ("PG", "SG", "SF", "PF", "C"):
            continue

        abbreviation = cells[1].split()[0]
        if abbreviation not in team_names:
            raise ValueError(
                f"Unrecognized source team: {abbreviation}"
            )

        record = {
            "Team": team_names[abbreviation],
            "Position": position,
        }
        for stat, index in stat_indexes.items():
            value = float(cells[index].split()[0])
            if not math.isfinite(value) or value < 0:
                raise ValueError("Invalid position-defense value.")
            record[stat] = value

        row_identity = (abbreviation, position)
        previous = records.get(row_identity)
        if previous is not None and previous != record:
            raise ValueError("Conflicting position-defense rows.")
        records[row_identity] = record

    expected = {
        (team, position)
        for team in team_names
        for position in ("PG", "SG", "SF", "PF", "C")
    }
    if set(records) != expected:
        raise ValueError(
            "The source did not return all 30 teams and 5 positions."
        )

    frame = pd.DataFrame(records.values())
    for stat in stat_indexes:
        frame[stat + " rank"] = (
            frame.groupby("Position")[stat]
            .rank(
                method="min",
                ascending=(stat != "Turnovers"),
            )
            .astype(int)
        )

    return {
        "data": frame,
        "url": url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def render_position_defense(card):
    st.markdown("#### Defense versus position")

    try:
        with st.spinner("Loading defense versus position..."):
            result = load_position_defense()

        def team_key(name):
            return normalize_player(name).replace(
                "losangeles", "la"
            )

        names = {
            team_key(name)
            for name in str(card["Game"]).split(" vs ")
        }
        frame = result["data"]
        matched = frame[
            frame["Team"].map(team_key).isin(names)
        ].copy()

        if len(names) != 2 or len(matched) != 10:
            raise ValueError(
                "Both matchup teams could not be matched."
            )

    except Exception as error:
        st.warning(
            "Defense versus position unavailable. "
            "Other research remains available."
        )
        st.code(
            f"{type(error).__name__}: {error}",
            language="text",
        )
        return

    stat = st.selectbox(
        "Position-defense statistic",
        [
            "Points", "Rebounds", "Assists", "Threes made",
            "Steals", "Blocks", "Turnovers",
        ],
        key=f"dvp_stat_{card['Fixture ID']}",
    )

    display = matched[
        ["Team", "Position", stat, stat + " rank"]
    ].rename(columns={
        stat: "Allowed per 48 minutes",
        stat + " rank": "Rank within position",
    })

    position_order = {
        "PG": 0, "SG": 1, "SF": 2, "PF": 3, "C": 4,
    }
    display["_order"] = display["Position"].map(position_order)
    display = display.sort_values(
        ["_order", "Team"]
    ).drop(columns="_order")

    st.dataframe(
        display,
        hide_index=True,
        use_container_width=True,
    )
    st.caption(
        "PG: point guard · SG: shooting guard · "
        "SF: small forward · PF: power forward · C: center."
    )
    st.caption(
        "Rank compares all 30 defenses against the same position. "
        "Rank 1 means fewest allowed; for turnovers, it means "
        "most opponent turnovers. Ties share a rank."
    )
    st.caption(
        "These are position-group statistics per 48 minutes, "
        "not an individual player's expected game total or "
        "a specific defender assignment."
    )
    st.caption(
        "Source: Hashtag Basketball live table. "
        "Not filtered by your historical-season selector or "
        "reconstructed for an earlier fixture. "
        "Publisher update time is not verified by this app."
    )
    st.caption(
        f"App fetched (UTC): {result['fetched_at']} · "
        "Shared cache: up to one hour · No OddsPAPI calls."
    )
    st.markdown(f"[View source table]({result['url']})")


# ---------------- CONNECTED PROJECTION ENGINE ----------------

ENGINE_VERSION = "minutes-rates-matchup-v1"


def engine_team_key(name):
    return normalize_player(name).replace("losangeles", "la")
def engine_game_context(card):
    try:
        start = pd.to_datetime(
            card["Start (UTC)"], utc=True, errors="raise"
        )
        if start <= pd.Timestamp.now(tz="UTC"):
            raise ValueError(
                "Game has reached its listed start time."
            )

        names = {
            engine_team_key(name)
            for name in str(card["Game"]).split(" vs ")
        }
        if len(names) != 2:
            raise ValueError(
                "Two distinct matchup teams are required."
            )

        date = start.tz_convert(
            "America/New_York"
        ).strftime("%Y%m%d")

        url = ESPN_BASE + "scoreboard"
        params = {"dates": date}
        board = saved_fetch(url, params, ttl_seconds=300)
        matches = []

        for event in board.get("events", []):
            event_start = pd.to_datetime(
                event.get("date"), utc=True, errors="coerce"
            )
            if pd.isna(event_start):
                continue
            if abs((event_start - start).total_seconds()) > 900:
                continue

            for competition in event.get("competitions", []):
                competitors = competition.get("competitors", [])
                returned_names = {
                    engine_team_key(
                        item.get("team", {}).get("displayName", "")
                    )
                    for item in competitors
                }
                if returned_names == names and len(competitors) == 2:
                    matches.append((event, competition))

        if len(matches) != 1:
            raise ValueError(
                "No unique ESPN game matched both teams "
                "and the fixture start time."
            )

        event, competition = matches[0]
        status = competition.get(
            "status", event.get("status", {})
        )
        if status.get("type", {}).get("state") != "pre":
            raise ValueError(
                "ESPN does not identify this game as pregame."
            )

        teams = {
            item["homeAway"]: item["team"]
            for item in competition["competitors"]
        }
        if set(teams) != {"home", "away"}:
            raise ValueError(
                "Home and away teams are not identified."
            )

        quote_url = url
        quote_params = params
        quote_feed = "ESPN scoreboard"

        def draftkings_quotes(items):
            unique = {}

            for quote in items:
                if not isinstance(quote, dict):
                    continue

                provider = quote.get("provider", {})
                name = (
                    provider.get("name")
                    or provider.get("displayName", "")
                )

                if normalize_player(name) not in (
                    "draftkings", "draftkingssportsbook"
                ):
                    continue

                unique[json.dumps(quote, sort_keys=True)] = quote

            return list(unique.values())

        books = draftkings_quotes(
            competition.get("odds") or []
        )

        if len(books) != 1:
            quote_url = ESPN_BASE + "summary"
            quote_params = {"event": str(event["id"])}
            summary = saved_fetch(
                quote_url, quote_params, ttl_seconds=300
            )

            if str(summary.get("header", {}).get("id")) != (
                str(event["id"])
            ):
                raise ValueError(
                    "ESPN summary returned a different game."
                )

            books = draftkings_quotes(
                summary.get("pickcenter") or []
            )
            quote_feed = "ESPN game summary"

        if len(books) != 1:
            raise ValueError(
                "Neither ESPN feed returned one unique "
                "DraftKings quote. No spread or blowout-risk "
                "estimate was substituted."
            )

        odds = books[0]
        spreads = odds.get("pointSpread", {})

        home_spread = float(
            spreads.get("home", {}).get("close", {}).get("line")
        )
        away_spread = float(
            spreads.get("away", {}).get("close", {}).get("line")
        )

        if not all(
            math.isfinite(value) and abs(value) <= 100
            for value in (home_spread, away_spread)
        ):
            raise ValueError("Invalid spread values.")

        if abs(home_spread + away_spread) > 0.001:
            raise ValueError(
                "Home and away spreads do not match."
            )

        total = None
        if odds.get("overUnder") is not None:
            total = float(odds["overUnder"])
            if not math.isfinite(total) or not 0 < total < 500:
                raise ValueError("Invalid game total.")

        if home_spread < 0:
            favorite = teams["home"]["displayName"]
        elif home_spread > 0:
            favorite = teams["away"]["displayName"]
        else:
            favorite = "Pick'em"

        spread_size = abs(home_spread)
        if spread_size >= 10:
            risk = "Higher"
        elif spread_size >= 5:
            risk = "Moderate"
        else:
            risk = "Lower"

        moneyline = odds.get("moneyline", {})
        implied = {}

        for side in ("home", "away"):
            try:
                price = float(
                    moneyline.get(side, {})
                    .get("close", {})
                    .get("odds")
                )
                if math.isfinite(price) and abs(price) >= 100:
                    implied[side] = (
                        100 / (price + 100)
                        if price > 0
                        else -price / (-price + 100)
                    )
            except (TypeError, ValueError):
                pass

        no_vig = None
        if len(implied) == 2:
            denominator = sum(implied.values())
            no_vig = {
                side: probability / denominator
                for side, probability in implied.items()
            }

        return {
            "available": True,
            "event_id": str(event["id"]),
            "start": start.isoformat(),
            "home": teams["home"]["displayName"],
            "away": teams["away"]["displayName"],
            "home_spread": home_spread,
            "away_spread": away_spread,
            "favorite": favorite,
            "total": total,
            "risk": risk,
            "moneyline_no_vig": no_vig,
            "source": "DraftKings via " + quote_feed,
            "fetched_at": cached_response_time(
                quote_url, quote_params
            ),
            "feed_timestamp": board.get("timestamp"),
            "quote": odds,
        }

    except Exception as error:
        return {
            "available": False,
            "reason": f"{type(error).__name__}: {error}",
        }


def render_game_context(context):
    st.markdown("#### Game market & blowout risk")

    if not context["available"]:
        st.write("**Blowout risk: Unavailable**")
        st.caption(context["reason"])
        return

    color = {
        "Lower": "🟢",
        "Moderate": "🟡",
        "Higher": "🔴",
    }[context["risk"]]

    st.write(
        f"**Blowout risk: {color} {context['risk']}**"
    )
    st.write(f"**Favorite:** {context['favorite']}")
    st.write(
        f"**Spread:** "
        f"{context['home']} {context['home_spread']:+g} · "
        f"{context['away']} {context['away_spread']:+g}"
    )

    if context["total"] is not None:
        st.write(f"**Game total:** {context['total']:g}")
    else:
        st.caption("Game total unavailable.")

    no_vig = context["moneyline_no_vig"]
    if no_vig:
        st.write(
            "**No-vig game-win market:** "
            f"{context['home']} {no_vig['home']:.1%} · "
            f"{context['away']} {no_vig['away']:.1%}"
        )
        st.caption(
            "Game-win market estimates only. "
            "These are not player-prop probabilities."
        )

    st.caption(
        "Spread-based indicator: Lower below 5 points; "
        "Moderate from 5 to below 10; Higher at 10 or more. "
        "These are initial display thresholds, not calibrated "
        "blowout probabilities. Lower does not mean no risk."
    )
    st.caption(
        "Displayed and saved as game context. "
        "No blowout minutes deduction or automatic UNDER "
        "rule is applied."
    )
    st.caption(
        f"Source: {context['source']} · "
        f"App fetched (UTC): "
        f"{context['fetched_at'] or 'Unavailable'} · "
        "Cache: up to 5 minutes. Fetch time is not the "
        "sportsbook quote-update time. No OddsPAPI calls."
    )


def engine_position(team, athlete, season):
    roster_position = athlete.get("position", {}).get(
        "abbreviation", "Unavailable"
    )
    evidence = {
        "source": "ESPN roster",
        "position": roster_position,
        "candidates": [],
        "note": "Roster position; not a confirmed game assignment.",
    }

    url = ESPN_BASE + f"teams/{team['id']}/depthcharts"

    try:
        data = saved_fetch(url, {}, ttl_seconds=300)

        if str(data.get("team", {}).get("id")) != str(team["id"]):
            raise ValueError("Depth-chart team did not match.")

        expected_year = int(season.split("-")[0]) + 1

        if str(data.get("season", {}).get("year")) != str(expected_year):
            raise ValueError("Depth-chart season did not match.")

        ranks = {}

        for chart in data.get("depthchart", []):
            for entry in chart.get("positions", {}).values():
                position = entry.get("position", {}).get(
                    "abbreviation"
                )

                if position not in ("PG", "SG", "SF", "PF", "C"):
                    continue

                for rank, player in enumerate(
                    entry.get("athletes", []), 1
                ):
                    if str(player.get("id")) == str(athlete["id"]):
                        ranks[position] = min(
                            rank, ranks.get(position, rank)
                        )

        evidence.update({
            "url": url,
            "source_timestamp": data.get("timestamp"),
            "fetched_at": cached_response_time(url, {}),
            "candidates": [
                {"position": position, "depth_order": rank}
                for position, rank in sorted(ranks.items())
            ],
        })

        if ranks:
            best_rank = min(ranks.values())
            best = [
                position
                for position, rank in ranks.items()
                if rank == best_rank
            ]

            if len(best) == 1:
                position = best[0]
                evidence.update({
                    "source": "ESPN depth chart",
                    "position": position,
                    "note": (
                        "Unique highest depth-chart placement. "
                        "Not confirmed starting status or "
                        "position minutes."
                    ),
                })
                return position, evidence

        evidence["note"] = (
            "No unique depth-chart role. Using roster position; "
            "broad G/F labels receive no position adjustment."
        )

    except Exception as error:
        evidence["note"] = (
            "Depth-chart lookup unavailable: "
            f"{type(error).__name__}. Using roster position."
        )

    return roster_position, evidence


def engine_no_vig(card, rows=None):
    fields = (
        "Fixture ID", "Market ID", "Period", "Line", "Line Type",
    )
    pairs = {}

    for row in (st.session_state.get("v4_rows", []) if rows is None else rows):
        if row.get("Platform") != "draftkings":
            continue

        if any(
            str(row.get(field)) != str(card.get(field))
            for field in fields
        ):
            continue

        if normalize_player(row.get("Player", "")) != (
            normalize_player(card["Player"])
        ):
            continue

        try:
            price = float(row["Odds"])
        except (TypeError, ValueError, KeyError):
            continue

        if not math.isfinite(price) or abs(price) < 100:
            continue

        probability = (
            100 / (price + 100)
            if price > 0
            else -price / (-price + 100)
        )

        pair_identity = (
            str(row["Player ID"]),
            str(row["Market ID"]),
        )
        sides = pairs.setdefault(pair_identity, {})
        direction = row.get("Direction")

        if direction in ("MORE", "LESS"):
            sides.setdefault(direction, set()).add(
                (price, probability)
            )

    valid = []

    for sides in pairs.values():
        if set(sides) != {"MORE", "LESS"}:
            continue
        if any(len(sides[side]) != 1 for side in sides):
            continue

        over = next(iter(sides["MORE"]))
        under = next(iter(sides["LESS"]))
        total = over[1] + under[1]

        valid.append({
            "book": "draftkings",
            "over_price": over[0],
            "under_price": under[0],
            "more": over[1] / total,
            "less": under[1] / total,
        })

    return valid[0] if len(valid) == 1 else None
def calculate_player_projection(
    history, card, season, team_name, position,
    advanced=None, position_data=None,
):
    current_time = pd.Timestamp.now(tz="UTC")
    start = pd.to_datetime(
        card["Start (UTC)"], utc=True, errors="coerce"
    )

    if pd.isna(start) or start <= current_time:
        raise ValueError(
            "New projections require a future fixture start time."
        )

    current_year = (
        current_time.year
        if current_time.month >= 10
        else current_time.year - 1
    )
    current_season_label = (
        f"{current_year}-{str(current_year + 1)[-2:]}"
    )

    if season != current_season_label:
        raise ValueError(
            "Choose the current season for a pregame projection."
        )

    columns = market_columns(card)
    if not columns:
        raise ValueError(
            "This market's scoring is not mapped yet."
        )

    line = float(card["Line"])
    if not math.isfinite(line) or line <= 0:
        raise ValueError(
            "A verified positive prop line is required."
        )

    sample = history.copy()
    sample["GAME_DATE"] = pd.to_datetime(
        sample["GAME_DATE"], utc=True
    )
    sample = sample[
        (sample["GAME_DATE"] < current_time)
        & (sample["SEASON"] == season)
    ]
    sample["MIN"] = pd.to_numeric(
        sample["MIN"], errors="coerce"
    )
    sample = sample[
        sample["MIN"].between(0.01, 70)
    ]
    sample = (
        sample.drop_duplicates("GAME_ID")
        .sort_values("GAME_DATE", ascending=False)
    )

    if len(sample) < 3:
        raise ValueError(
            "At least 3 current-season appearances are needed."
        )

    minutes = min(
        float(sample.head(5)["MIN"].mean()), 48.0
    )
    estimates = {}
    components = []
    warnings = []

    if len(sample) < 10:
        warnings.append(
            "Small sample: fewer than 10 current-season appearances."
        )

    pace_factor = 1.0
    opponent = None
    advanced_frame = None

    if advanced is not None:
        advanced_frame = advanced["data"].copy()
        advanced_frame["_key"] = (
            advanced_frame["TEAM_NAME"].map(engine_team_key)
        )
        lookup = advanced_frame.set_index("_key")

        names = [
            engine_team_key(name)
            for name in str(card["Game"]).split(" vs ")
        ]
        own = engine_team_key(team_name)
        opponents = [
            name for name in names if name != own
        ]

        if (
            len(names) == 2
            and own in names
            and len(opponents) == 1
        ):
            candidate = opponents[0]

            if own in lookup.index and candidate in lookup.index:
                opponent = candidate
                own_pace = float(lookup.loc[own, "PACE"])
                opponent_pace = float(
                    lookup.loc[opponent, "PACE"]
                )
                game_pace = (own_pace + opponent_pace) / 2
                pace_factor = max(
                    0.90,
                    min(1.10, game_pace / own_pace),
                )

    if opponent is None:
        warnings.append(
            "Pace adjustment unavailable; pace factor is 1.0."
        )

    stat_labels = {
        "PTS": "Points",
        "REB": "Rebounds",
        "AST": "Assists",
        "FG3M": "Threes made",
        "STL": "Steals",
        "BLK": "Blocks",
        "TOV": "Turnovers",
    }

    position_rows = None

    if (
        position_data is not None
        and position in ("PG", "SG", "SF", "PF", "C")
    ):
        position_rows = position_data["data"]
        position_rows = position_rows[
            position_rows["Position"] == position
        ].copy()
        position_rows["_key"] = (
            position_rows["Team"].map(engine_team_key)
        )
    else:
        warnings.append(
            "Exact position or position-defense data unavailable; "
            "no position adjustment applied."
        )

    for stat in columns:
        values = pd.to_numeric(
            sample[stat], errors="coerce"
        )
        games = sample[
            values.notna() & values.ge(0)
        ].head(15).copy()

        if len(games) < 3:
            raise ValueError(
                f"Fewer than 3 usable {stat} appearances."
            )

        games[stat] = pd.to_numeric(games[stat])
        rate = float(
            games[stat].sum() / games["MIN"].sum()
        )
        matchup = 1.0

        if position_rows is not None and opponent is not None:
            label = stat_labels[stat]
            allowed = position_rows[
                position_rows["_key"] == opponent
            ]

            if len(allowed) == 1 and advanced_frame is not None:
                mean_allowed = float(
                    position_rows[label].mean()
                )
                pace_lookup = advanced_frame.set_index(
                    "_key"
                )["PACE"]
                opponent_pace = float(
                    pace_lookup.loc[opponent]
                )
                league_pace = float(
                    advanced_frame["PACE"].mean()
                )

                if mean_allowed > 0:
                    residual = (
                        float(allowed.iloc[0][label])
                        / mean_allowed
                        / (opponent_pace / league_pace)
                    )

                    matchup = max(
                        0.90,
                        min(
                            1.10,
                            1 + 0.25 * (residual - 1),
                        ),
                    )

        estimate = minutes * rate * pace_factor * matchup

        if not math.isfinite(estimate):
            raise ValueError(
                "Calculation produced an invalid value."
            )

        estimates[stat] = estimate
        components.append({
            "Stat": stat,
            "Games": len(games),
            "Per minute": rate,
            "Expected minutes": minutes,
            "Pace factor": pace_factor,
            "Position factor": matchup,
            "Projection": estimate,
        })

    projection = sum(estimates.values())

    return {
        "version": ENGINE_VERSION,
        "projection": projection,
        "direction": "MORE" if projection > line else "LESS",
        "line": line,
        "gap": projection - line,
        "tie": abs(projection - line) < 1e-9,
        "minutes": minutes,
        "team": team_name,
        "position": position,
        "components": components,
        "warnings": warnings,
        "games": sample.head(15).to_json(
            orient="records", date_format="iso"
        ),
        "advanced": (
            None if advanced is None else {
                "fetched_at": advanced["fetched_at"],
                "rows": advanced["data"].to_dict(
                    orient="records"
                ),
            }
        ),
        "position_data": (
            None if position_data is None else {
                "fetched_at": position_data["fetched_at"],
                "rows": position_data["data"].to_dict(
                    orient="records"
                ),
            }
        ),
    }


def render_calculated_pick(history, card, season):
    st.markdown("#### Calculated projection")

    try:
        matched_identity = resolve_player(card["Player"])
        if matched_identity is None:
            raise ValueError(
                "A unique ESPN player identity is required."
            )

        names = {
            engine_team_key(name)
            for name in str(card["Game"]).split(" vs ")
        }
        teams = [
            team for team in espn_teams()
            if engine_team_key(team["displayName"]) in names
        ]

        if len(teams) != 2:
            raise ValueError(
                "Both fixture teams must be identified."
            )

        matches = []

        for team in teams:
            url = ESPN_BASE + f"teams/{team['id']}/roster"
            roster = saved_fetch(
                url, {}, ttl_seconds=300
            )

            for athlete in roster.get("athletes", []):
                if str(athlete.get("id")) == str(matched_identity[0]):
                    matches.append((
                        team,
                        athlete,
                        cached_response_time(url, {}),
                    ))

        if len(matches) != 1:
            raise ValueError(
                "Player must match exactly one fixture roster."
            )

        team, athlete, roster_time = matches[0]
        position, position_evidence = engine_position(
            team, athlete, season
        )
        st.caption(
            f"Position source: {position_evidence['source']} · "
            f"{position_evidence['note']}"
        )

        advanced = None
        position_data = None
        missing = []

        try:
            advanced = load_team_advanced(season)
        except Exception:
            missing.append(
                "Team pace feed unavailable; no pace adjustment."
            )

        try:
            position_data = load_position_defense()
        except Exception:
            missing.append(
                "Position-defense feed unavailable; "
                "no position adjustment."
            )

        result = calculate_player_projection(
            history,
            card,
            season,
            team["displayName"],
            position,
            advanced,
            position_data,
        )

        market = engine_no_vig(card)
        result["position_evidence"] = position_evidence
        result["market"] = market
        result["kalshi"] = render_kalshi(card)
        result["game_context"] = engine_game_context(card)
        render_game_context(result["game_context"])
        result["roster"] = {
            "fetched_at": roster_time,
            "athlete": athlete,
        }
        result["availability_check"] = st.session_state.get(
            f"availability_v3_{card['Fixture ID']}_{matched_identity[0]}"
        )
        result["warnings"].extend(missing)
        result["warnings"].append(
            "Conditional on playing the estimated minutes. "
            "Injury-driven role changes, confirmed lineups, "
            "and blowout minutes changes are not modeled."
        )

        event, competition = workflow.event_for(card)
        availability = workflow.availability(
            matched_identity[0], team["id"]
        )
        result = workflow.enrich(
            result, history, card, athlete, event["id"],
            result["game_context"], availability
        )
        workflow.freeze(
            card, result, season,
            {"roster": athlete, "roster_fetched_at": roster_time}
        )
        saved = True

    except Exception as error:
        st.error(
            "Projection unavailable: "
            f"{type(error).__name__}: {error}"
        )
        return

    icon = (
        "🟢 ↑" if result["direction"] == "MORE" else "🔴 ↓"
    )
    st.subheader(
        f"{icon} {result['direction']} · "
        f"Projection {result['projection']:.2f}"
    )
    st.write(
        f"Line: **{result['line']:g}** · "
        f"Projected difference: **{result['gap']:+.2f}**"
    )
    st.write(
        f"Estimated minutes: **{result['minutes']:.1f}** · "
        f"Matchup position: **{position}**"
    )

    if result.get("estimated_hit_probability") is not None:
        st.write(
            "**Estimated hit probability:** "
            f"{result['estimated_hit_probability']:.1%} · "
            f"{result.get('probability_sample_games', 0)} recent games"
        )
        st.caption(result.get(
            "probability_note",
            "Experimental estimate; see the hit-rate tracker "
            "for realized performance."
        ))

    if result["tie"]:
        st.caption(
            "Projection equals line. LESS is only a fixed "
            "tie-break, with no projected edge."
        )

    st.dataframe(
        pd.DataFrame(result["components"]).round(4),
        hide_index=True,
        use_container_width=True,
    )

    if market:
        st.write(
            f"DraftKings no-vig MORE: **{market['more']:.1%}** · "
            f"LESS: **{market['less']:.1%}**"
        )
        st.caption(
            "Matching same-market prices only. "
            "These are market estimates, not the model's "
            "win probabilities. They are not blended into "
            "the projection."
        )
    else:
        st.caption(
            "No-vig unavailable: no unique matching "
            "DraftKings over/under price pair."
        )

    st.caption(
        "Initial formula: last-5 mean minutes × pooled last-15 "
        "stat/minute, using available current-season appearances. "
        "Pace adjustment capped at ±10%. Position adjustment "
        "uses 25% of the tempo-adjusted difference from the "
        "position average, capped at ±10%. These initial "
        "settings have not been validated."
    )

    for warning in dict.fromkeys(result["warnings"]):
        st.caption(warning)

    st.caption(
        "No OddsPAPI requests are made by this section. "
        "Learning reviews settled forecasts; initial formulas "
        "remain until a model passes its chronological holdout."
    )
    st.caption(
        "Pregame calculation recorded. The first frozen forecast "
        "is retained for official grading; reopening does not rewrite it."
        if saved else
        "Fixture started before saving; no pregame record saved."
    )
    st.caption(
        "Use the full workspace backup for forecasts, grades, "
        "paper records and models. Configure DATABASE_URL "
        "for permanent storage."
    )
def render_card_history(card):
    columns = market_columns(card)
    if columns is None:
        st.info(
            "History unavailable for this market or period. "
            "Scoring mapping needs verification."
        )
        return

    matched = resolve_player(str(card["Player"]))
    if matched is None:
        st.info(
            "No unique current roster match was found. "
            "Unmatched or ambiguous players remain unavailable."
        )
        return

    player_id, name = matched
    season = st.session_state.get(
        "nba_history_season", current_season()
    )
    key = f"{player_id}:{season}"
    history = st.session_state.get("deep_history", {}).get(key)

    if history is None:
        try:
            with st.spinner(
                "Loading this player's current and previous season..."
            ):
                history = fetch_player_history(player_id, name, season)
            st.session_state.setdefault("deep_history", {})[key] = history
        except Exception as error:
            st.error(
                "Historical data unavailable: "
                f"{type(error).__name__}: {error}"
            )
            return

    cutoff = pd.to_datetime(
        card["Start (UTC)"], utc=True, errors="coerce"
    )
    if pd.isna(cutoff):
        st.info(
            "Missing fixture start time: "
            "historical comparison unavailable."
        )
        return

    cutoff = min(cutoff, pd.Timestamp.now(tz="UTC"))
    sample = history[history["GAME_DATE"] < cutoff].copy()
    sample["VALUE"] = score_frame(sample, card)
    missing = int(sample["VALUE"].isna().sum())
    sample = sample.dropna(subset=["VALUE"]).sort_values(
        "GAME_DATE", ascending=False
    )

    summaries = []
    for n in (5, 10, 15, 20):
        label = (
            f"Last {n}" if len(sample) >= n
            else f"Last {n} ({len(sample)} available)"
        )
        summaries.append(
            window_summary(sample.head(n), float(card["Line"]), label)
        )

    current = sample[sample["SEASON"] == season]
    summaries.append(
        window_summary(current, float(card["Line"]), f"{season} season")
    )
    for location in ("Home", "Away"):
        summaries.append(
            window_summary(
                current[current["HOME_AWAY"] == location],
                float(card["Line"]),
                f"{season} {location}",
            )
        )

    h2h_games = None
    try:
        participants = str(card["Game"]).split(" vs ")
        lookup = {
            normalize_player(team["displayName"]): str(team["id"])
            for team in espn_teams()
        }
        ids = [
            lookup.get(normalize_player(participant))
            for participant in participants
        ]
        latest_team = (
            str(sample.iloc[0]["TEAM_ID"]) if not sample.empty else None
        )
        opponents = [
            team_id for team_id in ids
            if team_id and team_id != latest_team
        ]
        if (
            len(ids) == 2 and all(ids)
            and latest_team in ids and len(opponents) == 1
        ):
            h2h_games = sample[
                sample["OPPONENT_ID"] == opponents[0]
            ].head(15)
            summaries.append(
                window_summary(
                    h2h_games, float(card["Line"]), "H2H (up to 15 available)"
                )
            )
        else:
            st.caption(
                "H2H unavailable: current matchup/team "
                "mapping needs confirmation."
            )
    except Exception:
        st.caption("H2H unavailable: ESPN team mapping did not load.")

    st.markdown("#### Recent form & splits")
    summary_columns = st.columns(3)
    for column, label, frame in zip(summary_columns, ['Last 5 average','Last 15 average','H2H average'],
                                    [sample.head(5), sample.head(15), h2h_games]):
        value = None if frame is None or frame.empty else float(frame['VALUE'].mean())
        column.metric(label, f'{value:.2f}' if value is not None else '—')
    with st.expander("Game log · recent appearances"):
        limit = st.selectbox('Games to show', [15, 5, 10, 20, 50], key='detail_log_count_' + identity(card))
        log = sample.head(limit)[['GAME_DATE', 'MATCHUP', 'MIN', 'VALUE']].copy()
        log['GAME_DATE'] = pd.to_datetime(log['GAME_DATE'], utc=True).dt.tz_convert('America/Los_Angeles').dt.strftime('%b %d, %Y')
        log = log.rename(columns={'GAME_DATE':'Date (Pacific)','MATCHUP':'Opponent','MIN':'Minutes','VALUE':ui_stat(card)})
        st.dataframe(log, hide_index=True, use_container_width=True, height=350)
        st.caption(f'{len(log)} displayed · {len(sample)} verified appearances loaded across the selected seasons.')
    st.markdown("#### Window averages & home/away")
    st.dataframe(
        pd.DataFrame(summaries),
        hide_index=True,
        use_container_width=True,
    )
    st.caption(
        "Regular-season appearances only. Rolling windows may span "
        "two seasons. Home/away uses the selected season. MORE/LESS "
        "percentages include ties in the denominator; platform "
        "settlement is not applied. Historical rates are not "
        "predicted probabilities."
    )
    if missing:
        st.warning(
            f"{missing} games excluded because a required stat was missing."
        )
    if sample.empty:
        return

    st.caption(
        f"Loaded coverage: {sample['GAME_DATE'].min():%Y-%m-%d} "
        f"to {sample['GAME_DATE'].max():%Y-%m-%d}."
    )
    if not current.empty:
        st.caption(
            f"Current season average minutes: {current['MIN'].mean():.1f}"
        )
    else:
        st.caption("No selected-season appearances returned.")

    st.markdown("#### Head-to-head stat averages")
    if h2h_games is None:
        st.info("H2H unavailable: opponent mapping could not be confirmed.")
    elif h2h_games.empty:
        st.info("No H2H appearances found in the loaded seasons.")
    else:
        labels = {
            "MIN": "Minutes", "PTS": "Points", "REB": "Rebounds",
            "AST": "Assists", "STL": "Steals", "BLK": "Blocks",
            "TOV": "Turnovers", "FG3M": "Threes made",
        }
        h2h_rows = []
        for column, label in labels.items():
            if column in (["MIN"] + columns) and column in h2h_games.columns:
                values = pd.to_numeric(
                    h2h_games[column], errors="coerce"
                ).dropna()
                h2h_rows.append({
                    "Stat": label,
                    "Average": (
                        round(float(values.mean()), 2)
                        if not values.empty else None
                    ),
                    "Games with data": len(values),
                })

        st.dataframe(
            pd.DataFrame(h2h_rows),
            hide_index=True,
            use_container_width=True,
        )
        seasons_shown = ", ".join(
            sorted(h2h_games["SEASON"].dropna().unique())
        )
        st.caption(
            f"{len(h2h_games)} H2H appearances · Seasons: {seasons_shown} · "
            f"{h2h_games['GAME_DATE'].min():%Y-%m-%d} to "
            f"{h2h_games['GAME_DATE'].max():%Y-%m-%d}. "
            "Historical results against the team, not a specific defender."
        )



# ---------------- SAVED WORKSPACE AND KALSHI ----------------

def workspace_store(key, value):
    con = history_connection()
    try:
        with con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS workspace "
                "(key TEXT PRIMARY KEY, payload TEXT)"
            )
            con.execute(
                "INSERT OR REPLACE INTO workspace VALUES (?,?)",
                (key, encode(value)),
            )
    finally:
        con.close()


def workspace_load(key):
    con = history_connection()
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS workspace "
            "(key TEXT PRIMARY KEY, payload TEXT)"
        )
        row = con.execute(
            "SELECT payload FROM workspace WHERE key=?",
            (key,),
        ).fetchone()
        return json.loads(row[0]) if row else None
    finally:
        con.close()


BOARD_FIELDS = (
    "v4_rows", "v4_summary", "v4_errors", "v4_scan_at",
)


def validate_board(board):
    if (
        not isinstance(board, dict)
        or not all(key in board for key in BOARD_FIELDS)
    ):
        raise ValueError("Not a Sports Lab board backup.")

    if (
        not isinstance(board["v4_rows"], list)
        or len(board["v4_rows"]) > 100000
    ):
        raise ValueError("Invalid board rows.")

    required = {
        "Game", "Fixture ID", "Start (UTC)", "Platform",
        "Player", "Stat", "Stat Key", "Period", "Line",
        "Raw Handicap", "Direction", "Line Status", "Line Type",
        "Odds", "Market ID", "Outcome ID", "Player ID", "Decoded",
    }

    for row in board["v4_rows"]:
        if (
            not isinstance(row, dict)
            or not required.issubset(row)
        ):
            raise ValueError("Board row fields are missing.")

        if (
            row["Platform"] not in BOOKS
            or type(row["Decoded"]) is not bool
        ):
            raise ValueError("Invalid platform or decoding flag.")

        if row["Decoded"]:
            line = float(row["Line"])
            if (
                not 0 < line <= 1000
                or row["Direction"] not in ("MORE", "LESS")
            ):
                raise ValueError("Invalid decoded threshold.")

    if (
        not isinstance(board["v4_summary"], list)
        or not isinstance(board["v4_errors"], list)
    ):
        raise ValueError("Invalid board metadata.")

    stamp = pd.to_datetime(
        board["v4_scan_at"], utc=True, errors="raise"
    )
    if (
        pd.isna(stamp)
        or stamp > pd.Timestamp.now(tz="UTC") + pd.Timedelta(minutes=5)
    ):
        raise ValueError("Invalid scan time.")

    return {key: board[key] for key in BOARD_FIELDS}


def render_saved_workspace(controls=True):
    if "v4_rows" not in st.session_state:
        try:
            board = workspace_load("board")
            if board:
                st.session_state.update(validate_board(board))
        except Exception as error:
            st.warning(f"Saved board could not be restored: {error}")

    if not controls:
        return

    with st.expander("Save / restore scanned board — no odds calls"):
        st.caption(
            "Download this backup before updating code. "
            "Restore it if local storage was reset. "
            "Original scan times are preserved; restoring "
            "does not refresh prices."
        )
        if "v4_rows" in st.session_state:
            board = {
                key: st.session_state.get(key)
                for key in BOARD_FIELDS
            }
            st.download_button(
                "Download board backup",
                encode(board),
                "sports_lab_board.json",
                "application/json",
            )

        uploaded = st.file_uploader(
            "Restore board backup",
            type=["json"],
            key="restore_board_upload",
        )
        if st.button(
            "Restore saved board",
            disabled=uploaded is None,
        ):
            try:
                if uploaded.size > 25 * 1024 * 1024:
                    raise ValueError("Backup exceeds 25 MB.")

                board = validate_board(
                    json.loads(uploaded.getvalue())
                )
                workspace_store("board", board)
                st.session_state.update(board)
                st.session_state.pop("selected_prop_card", None)
                st.rerun()
            except Exception as error:
                st.error(f"Restore failed: {error}")

    if "v4_scan_at" in st.session_state:
        stamp = pd.to_datetime(
            st.session_state["v4_scan_at"], utc=True
        )
        age = max(
            0,
            (pd.Timestamp.now(tz="UTC") - stamp).total_seconds() / 60,
        )
        st.caption(
            f"Saved board age: {age:.0f} minutes. "
            "New prices require the explicit scan button."
        )
        if age > 30:
            st.warning(
                "Saved odds are over 30 minutes old. "
                "Projections can use this board, but confirm "
                "current lines and prices before acting."
            )


@st.cache_data(ttl=60, show_spinner=False)
def kalshi_public(path, params=None):
    response = requests.get(
        "https://external-api.kalshi.com/trade-api/v2/" + path,
        params=params or {},
        timeout=15,
    )
    if response.status_code == 404:
        return {}
    response.raise_for_status()
    return response.json()


def native_kalshi_quote(card):
    columns = market_columns(card)
    series_map = {
        "KXNBAPTS": ["PTS"],
        "KXNBAREB": ["REB"],
        "KXNBAAST": ["AST"],
        "KXNBA3PT": ["FG3M"],
        "KXNBASTL": ["STL"],
        "KXNBABLK": ["BLK"],
        "KXNBAPA": ["PTS", "AST"],
        "KXNBAPR": ["PTS", "REB"],
        "KXNBARA": ["REB", "AST"],
        "KXNBAPRA": ["PTS", "REB", "AST"],
    }
    series = {
        frozenset(value): key
        for key, value in series_map.items()
    }.get(frozenset(columns or []))

    if not series:
        raise ValueError(
            "Native contract mapping is not available for this stat."
        )

    line = float(card["Line"])
    if (
        not math.isfinite(line)
        or abs((line + 0.5) - round(line + 0.5)) > 1e-6
    ):
        raise ValueError(
            "Only exact half-point thresholds map "
            "to these Kalshi contracts."
        )

    start = pd.to_datetime(card["Start (UTC)"], utc=True)
    if pd.Timestamp.now(tz="UTC") >= start:
        raise ValueError("Pregame quotes are closed for this fixture.")

    names = {
        engine_team_key(name)
        for name in str(card["Game"]).split(" vs ")
    }
    teams = [
        team for team in espn_teams()
        if engine_team_key(team["displayName"]) in names
    ]
    if len(teams) != 2:
        raise ValueError(
            "Matchup teams could not be uniquely verified."
        )

    aliases = {
        "GS": "GSW", "NY": "NYK", "NO": "NOP", "SA": "SAS",
        "WSH": "WAS", "PHO": "PHX", "UTAH": "UTA",
    }
    codes = [
        aliases.get(team["abbreviation"], team["abbreviation"])
        for team in teams
    ]
    date = start.tz_convert(
        "America/New_York"
    ).strftime("%y%b%d").upper()

    found = {}
    for first, second in (codes, codes[::-1]):
        event_ticker = f"{series}-{date}{first}{second}"
        event = kalshi_public("events/" + event_ticker)
        metadata = event.get("event", {})

        if (
            metadata.get("event_ticker") != event_ticker
            or metadata.get("series_ticker") != series
        ):
            continue

        for market in event.get(
            "markets", metadata.get("markets", [])
        ):
            title = str(
                market.get("title")
                or market.get("yes_sub_title", "")
            )
            player_name = title.split(":", 1)[0]

            if (
                normalize_player(player_name)
                != normalize_player(card["Player"])
            ):
                continue
            if (
                market.get("strike_type") != "greater"
                or market.get("market_type") != "binary"
            ):
                continue
            if abs(
                float(market.get("floor_strike", -999)) - line
            ) > 1e-6:
                continue
            if market.get("event_ticker") != event_ticker:
                continue

            found[market["ticker"]] = market

    if len(found) != 1:
        raise ValueError(
            "No unique native Kalshi contract matches "
            "this player, fixture and exact line."
        )

    ticker = next(iter(found))
    market = kalshi_public("markets/" + ticker).get("market", {})

    if (
        market.get("ticker") != ticker
        or market.get("status") not in ("active", "open")
    ):
        raise ValueError("Matched contract is not active.")

    if (
        pd.to_datetime(market["close_time"], utc=True)
        <= pd.Timestamp.now(tz="UTC")
    ):
        raise ValueError("Matched contract has closed.")

    if float(market.get("notional_value_dollars", "1")) != 1:
        raise ValueError("Unsupported contract payout.")

    quotes = []
    for side, direction in (("yes", "MORE"), ("no", "LESS")):
        ask = float(market.get(side + "_ask_dollars") or "nan")
        if math.isfinite(ask) and 0 < ask < 1:
            quotes.append({
                "Contract": side.upper(),
                "Direction": direction,
                "Ask ($ / contract)": ask,
                "Break-even before fees": f"{ask:.1%}",
            })

    return {
        "ticker": ticker,
        "quotes": quotes,
        "rules": market.get("rules_primary", ""),
        "secondary_rules": market.get("rules_secondary", ""),
        "raw": market,
        "read_at": datetime.now(timezone.utc).isoformat(),
    }


def render_kalshi(card):
    if str(card.get("Platform", "")).lower() != "kalshi":
        return None

    st.markdown("#### Kalshi contract pricing")
    try:
        quote = native_kalshi_quote(card)
        st.caption(quote["ticker"])

        if quote["quotes"]:
            st.dataframe(
                pd.DataFrame(quote["quotes"]),
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.info("No valid ask quote returned.")

        st.caption(
            "Indicative public quotes, cached up to 60 seconds. "
            "Exchange prices are separate from sportsbook "
            "no-vig estimates and the model projection."
        )

        with st.expander("Kalshi cost and fee estimate"):
            quantity = int(st.number_input(
                "Contracts",
                min_value=1,
                max_value=10000,
                value=1,
                key="kalshi_qty",
            ))
            precision = st.selectbox(
                "Balance rounding",
                [
                    "Direct member ($0.0001)",
                    "Non-direct member ($0.01)",
                ],
                key="kalshi_precision",
            )

            try:
                fees = kalshi_fee_metadata(
                    kalshi_public, quote["ticker"]
                )
                book = kalshi_public(
                    "markets/" + quote["ticker"] + "/orderbook"
                ).get("orderbook_fp", {})

                costs = []
                for row in quote["quotes"]:
                    side = row["Contract"].lower()
                    opposite = "no" if side == "yes" else "yes"
                    ask = row["Ask ($ / contract)"]

                    depth = sum(
                        float(level[1])
                        for level in book.get(
                            opposite + "_dollars", []
                        )
                        if abs(
                            1 - float(level[0]) - ask
                        ) < 1e-6
                    )

                    estimate = fee_estimate(
                        ask,
                        quantity,
                        fees["multiplier"],
                        ".0001" if precision.startswith("Direct")
                        else ".01",
                    )
                    costs.append({
                        "Side": side.upper(),
                        "Contracts": quantity,
                        "At best ask": depth,
                        "Enough displayed depth": depth >= quantity,
                        "Fee estimate ($)": estimate["fee"],
                        "Total cost estimate ($)": estimate["total"],
                        "Break-even with fee": (
                            f"{estimate['break_even']:.2%}"
                        ),
                    })

                quote["fee_metadata"] = fees
                st.dataframe(
                    pd.DataFrame(costs),
                    hide_index=True,
                    use_container_width=True,
                )
                st.caption(
                    "Estimate assumes one all-taker fill at the "
                    "displayed ask and selected balance precision. "
                    "Partial fills, rebates and broker-specific "
                    "fees may differ. No order is submitted."
                )
            except Exception as error:
                st.info(
                    f"Fee/depth estimate unavailable: {error}"
                )

        with st.expander("Kalshi settlement rules"):
            st.write(quote["rules"])
            st.write(quote["secondary_rules"])

        return quote

    except Exception as error:
        st.info(f"Kalshi price unavailable: {error}")
        return {
            "available": False,
            "reason": str(error),
        }


# Resolve directly against current matchup rosters.

_seed_resolve_player = resolve_player


def _resolve_player_current(name):
    card = st.session_state.get("selected_prop_card", {})
    if not card:
        return _seed_resolve_player(name)

    names = {
        engine_team_key(value)
        for value in str(card.get("Game", "")).split(" vs ")
    }
    matches = {}

    for team in espn_teams():
        if engine_team_key(team["displayName"]) not in names:
            continue

        roster = saved_fetch(
            ESPN_BASE + f"teams/{team['id']}/roster",
            {},
            ttl_seconds=300,
        )
        for athlete in roster.get("athletes", []):
            if (
                normalize_player(athlete.get("displayName", ""))
                == normalize_player(name)
            ):
                matches[str(athlete["id"])] = athlete["displayName"]

    return next(iter(matches.items())) if len(matches) == 1 else None


def resolve_player(name):
    try:
        return _resolve_player_current(name)
    except Exception:
        return None


# ---------------- PRIZEPICKS FANTASY SCORING ----------------

_base_market_columns = market_columns


def fantasy_weights(card):
    name = str(card.get("Stat", "")).lower()
    if (
        "fantasy" in name
        and card.get("Platform") == "prizepicks"
        and str(card.get("Period", "")).lower() == "result"
    ):
        return {
            "PTS": 1.0,
            "REB": 1.2,
            "AST": 1.5,
            "STL": 3.0,
            "BLK": 3.0,
            "TOV": -1.0,
        }
    return None


def market_columns(card):
    weights = fantasy_weights(card)
    return list(weights) if weights else _base_market_columns(card)


def score_frame(frame, card):
    columns = market_columns(card)
    weights = fantasy_weights(card) or {
        column: 1.0 for column in columns
    }
    numeric = frame[columns].apply(
        pd.to_numeric, errors="coerce"
    )
    return numeric.mul(
        pd.Series(weights)
    ).sum(axis=1, min_count=len(columns))


_base_calculate_player_projection = calculate_player_projection


def calculate_player_projection(
    history, card, season, team_name, position,
    advanced=None, position_data=None,
):
    result = _base_calculate_player_projection(
        history, card, season, team_name, position,
        advanced, position_data,
    )
    weights = fantasy_weights(card)

    if weights:
        for component in result["components"]:
            component["Fantasy weight"] = weights[component["Stat"]]

        result["projection"] = sum(
            component["Projection"] * weights[component["Stat"]]
            for component in result["components"]
        )
        result["gap"] = result["projection"] - float(card["Line"])
        result["direction"] = (
            "MORE" if result["gap"] > 0 else "LESS"
        )
        result["tie"] = abs(result["gap"]) < 1e-9
        result["scoring"] = {
            "platform": "PrizePicks",
            "weights": weights,
            "source": (
                "https://www.prizepicks.com/playbook-article/"
                "how-to-play-prizepicks-nba-fantasy-scoring-system"
            ),
        }

    return result


# Emergency mode deliberately uses only browser-session memory.
# It never creates a second permanent database or changes Neon records.
class OfflineSessionStore:
    remote = False
    offline = True

    def __init__(self):
        self.records = st.session_state.setdefault("_sports_lab_offline_records_v1", {})

    def put(self, namespace, key, value, immutable=False):
        record_key = (namespace, key)
        if not immutable or record_key not in self.records:
            self.records[record_key] = value

    def get(self, namespace, key, default=None):
        return self.records.get((namespace, key), default)

    def items(self, namespace):
        return [(key, value) for (ns, key), value in self.records.items() if ns == namespace]

    def export(self):
        return encode({"format": "sports-lab-workspace-v2", "records": [
            [ns, key, encode(value), now()] for (ns, key), value in self.records.items()
        ]})

    def restore(self, value):
        if value.get("format") != "sports-lab-workspace-v2":
            raise ValueError("Unsupported workspace backup")
        checked = []
        for entry in value.get("records", []):
            if not isinstance(entry, list) or len(entry) != 4:
                raise ValueError("Invalid workspace record")
            ns, key, payload, _updated = entry
            if not isinstance(ns, str) or not isinstance(key, str) or not isinstance(payload, str):
                raise ValueError("Invalid workspace record")
            checked.append((ns, key, json.loads(payload)))
        for ns, key, payload in checked:
            self.put(ns, key, payload, immutable=True)
        return len(checked)

# ---------------- COMPLETE NBA WORKFLOW ----------------

def current_season():
    stamp = datetime.now(timezone.utc)
    year = stamp.year if stamp.month >= 10 else stamp.year - 1
    return f"{year}-{str(year + 1)[-2:]}"


_database_url = (
    os.getenv("DATABASE_URL")
    or setting("DATABASE_URL", "")
)

# Prefer Turso when configured; otherwise use PostgreSQL. The UI remains
# available with an explicitly temporary browser-session workspace.
_database_online = False
_db_failure = None
_turso_url = setting('TURSO_DATABASE_URL', '')
_turso_token = setting('TURSO_AUTH_TOKEN', '')
if _turso_url or (_database_url and str(_database_url).strip()):
    try:
        workflow_store = TursoStore(_turso_url, _turso_token) if _turso_url else Store(url=_database_url)
        _database_online = True
    except Exception as exc:
        _db_failure = type(exc).__name__
        _db_hint = str(exc) if _turso_url and type(exc) in (RuntimeError, ValueError) else storage_failure_hint(exc)
        print(f"SPORTS_LAB_DB_DIAGNOSTIC stage=connect exception_type={_db_failure} mode=offline_session", flush=True)

if not _database_online:
    workflow_store = OfflineSessionStore()
    st.info(globals().get("_db_hint", "Add top-level TURSO_DATABASE_URL and TURSO_AUTH_TOKEN in Streamlit Secrets for permanent storage."))
    st.caption('Storage configuration detected: Turso URL ' + ('present' if _turso_url else 'missing') +
               ' · Turso token ' + ('present' if _turso_token else 'missing') +
               ' · Attempted provider: ' + ('Turso' if _turso_url else 'PostgreSQL' if _database_url else 'none'))
    st.warning(
        "Permanent storage is unavailable. Sports Lab is running in "
        "TEMPORARY SESSION MODE: new saves, frozen picks and ROI changes "
        "are NOT stored permanently and may disappear when the session ends. "
        "Existing remote records have not been changed."
    )
    st.download_button(
        "Download temporary workspace backup",
        data=workflow_store.export(),
        file_name="sports_lab_temporary_workspace.json",
        mime="application/json",
        key="offline_backup_top",
    )

workflow = Workflow(globals(), workflow_store)
_local_workspace_load = workspace_load


def workspace_store(key, value):
    workflow_store.put("board", key, value)
def workspace_load(key):
    value = workflow_store.get("board", key)
    if value is None:
        value = _local_workspace_load(key)
        if value is not None:
            workspace_store(key, value)
    return value


def pp_stat_key(label):
    label = re.sub(r"[^a-z0-9]", "", str(label).lower())
    mapping = {
        "points": ("PTS",), "rebounds": ("REB",),
        "assists": ("AST",), "steals": ("STL",),
        "blocks": ("BLK",), "turnovers": ("TOV",),
        "3ptmade": ("FG3M",), "3pointersmade": ("FG3M",),
        "ptsrebs": ("PTS", "REB"),
        "ptsasts": ("PTS", "AST"),
        "rebsasts": ("REB", "AST"),
        "ptsrebsasts": ("PTS", "REB", "AST"),
        "blksstls": ("BLK", "STL"),
        "fantasyscore": ("FANTASY",),
        "nbafantasyscore": ("FANTASY",),
    }
    return tuple(sorted(mapping.get(label, ())))


def pp_card_key(card):
    columns = (
        ("FANTASY",) if fantasy_weights(card)
        else tuple(sorted(market_columns(card) or ()))
    )
    start = pd.to_datetime(card.get("Start (UTC)"), utc=True)
    return normalize_player(card["Player"]), columns, start.isoformat()


def pp_nba_team_code(value):
    if isinstance(value, dict):
        value = value.get('abbreviation') or value.get('displayName') or value.get('name')
    key = normalize_player(value or '')
    teams = {
        'ATL':'Atlanta Hawks', 'BOS':'Boston Celtics', 'BKN':'Brooklyn Nets',
        'CHA':'Charlotte Hornets', 'CHI':'Chicago Bulls', 'CLE':'Cleveland Cavaliers',
        'DAL':'Dallas Mavericks', 'DEN':'Denver Nuggets', 'DET':'Detroit Pistons',
        'GSW':'Golden State Warriors', 'HOU':'Houston Rockets', 'IND':'Indiana Pacers',
        'LAC':'Los Angeles Clippers', 'LAL':'Los Angeles Lakers', 'MEM':'Memphis Grizzlies',
        'MIA':'Miami Heat', 'MIL':'Milwaukee Bucks', 'MIN':'Minnesota Timberwolves',
        'NOP':'New Orleans Pelicans', 'NYK':'New York Knicks', 'OKC':'Oklahoma City Thunder',
        'ORL':'Orlando Magic', 'PHI':'Philadelphia 76ers', 'PHX':'Phoenix Suns',
        'POR':'Portland Trail Blazers', 'SAC':'Sacramento Kings', 'SAS':'San Antonio Spurs',
        'TOR':'Toronto Raptors', 'UTA':'Utah Jazz', 'WAS':'Washington Wizards',
    }
    aliases = {'gs':'GSW','ny':'NYK','no':'NOP','sa':'SAS','wsh':'WAS','pho':'PHX','utah':'UTA','laclippers':'LAC'}
    if key in aliases:
        return aliases[key]
    for code,name in teams.items():
        if key in (normalize_player(code),normalize_player(name)):
            return code
    return None


def pp_nearby_fantasy_candidates(rows, item, source_start):
    """Allow a small feed-time offset only with independent two-team agreement."""
    player = item.get('player') or {}
    event = item.get('event') or {}
    team = pp_nba_team_code(player.get('team'))
    opponent = pp_nba_team_code(event.get('opponent'))
    if not team or not opponent or team == opponent:
        return []
    name = normalize_player(player.get('name',''))
    candidates = []
    for row in rows:
        if not row.get('Decoded') or str(row.get('Period','')).lower() != 'result':
            continue
        if normalize_player(row.get('Player','')) != name:
            continue
        if str(row.get('Player ID','')).lower() in ('','none','nan'):
            continue
        scheduled = pd.to_datetime(row.get('Start (UTC)'),utc=True,errors='coerce')
        if pd.isna(scheduled) or not today_pregame(scheduled):
            continue
        if scheduled.tz_convert(SLATE_TIMEZONE).date() != source_start.tz_convert(SLATE_TIMEZONE).date():
            continue
        if abs((scheduled-source_start).total_seconds()) > 15*60:
            continue
        teams = [pp_nba_team_code(t) for t in str(row.get('Game','')).split(' vs ')]
        if len(teams) != 2 or None in teams or set(teams) != {team,opponent}:
            continue
        candidates.append(row)
    return candidates


def pp_merge_tiers(rows, projections, fetched, fantasy_only=False):
    templates = {}
    player_games = {}
    for row in rows:
        row_key = pp_card_key(row)
        if row.get("Platform") == "prizepicks":
            templates.setdefault(row_key, []).append(row)
        if (row.get("Decoded") and str(row.get("Period", "")).lower() == "result"
                and str(row.get("Player ID", "")).lower() not in ("", "none", "nan")):
            player_games.setdefault((row_key[0], row_key[2]), []).append(row)
    imported, matched = [], set()
    labels = {"standard": "Standard", "goblin": "Goblin", "demon": "Demon"}
    for item in projections:
        if item.get("is_demo") is not False or item.get("status") != "active":
            continue
        if str(item.get("league", "")).upper() != "NBA":
            continue
        market = item.get("market") or {}
        if str(market.get("period", "")).lower() != "full game":
            continue
        start = pd.to_datetime((item.get("event") or {}).get("starts_at"), utc=True, errors="coerce")
        if pd.isna(start) or start <= pd.Timestamp.now(tz="UTC"):
            continue
        key = (
            normalize_player((item.get("player") or {}).get("name", "")),
            pp_stat_key(market.get("stat")), start.isoformat(),
        )
        candidates = templates.get(key, [])
        if not candidates and key[1] == ("FANTASY",):
            # Borrow only a uniquely matched player/game identity; never its stat or line.
            candidates = player_games.get((key[0], key[2]), [])
        if not candidates and key[1] == ("FANTASY",):
            candidates = pp_nearby_fantasy_candidates(rows, item, start)
        if not key[1] or not candidates:
            continue
        if len({str(c["Player ID"]) for c in candidates}) != 1:
            continue
        if len({str(c["Fixture ID"]) for c in candidates}) != 1:
            continue
        line = numeric_line(item.get("line"))
        if line is None or not item.get("id"):
            continue
        groups = {}
        for selection in item.get("selections", []):
            side = str(selection.get("side", "")).upper()
            tier = labels.get(selection.get("tier"))
            if selection.get("available") is True and side in ("MORE", "LESS") and tier:
                groups.setdefault(tier, set()).add(side)
        for tier, sides in groups.items():
            for side in sorted(sides):
                row = dict(candidates[0])
                if key[1] == ("FANTASY",):
                    row.update({"Platform": "prizepicks", "Stat": "Fantasy Score", "Stat Key": "fantasy_score", "Period": "result"})
                row.update({
                    "Line": line, "Raw Handicap": line,
                    "Line Type": tier, "Direction": side,
                    "Market ID": "ppfeed:" + str(item["id"]),
                    "Outcome ID": "ppfeed:" + str(item["id"]) + ":" + side,
                    "Odds": None, "mainLine Flag": None,
                    "Decoded": True, "Line Status": "Provider tier-labelled threshold",
                    "Available Sides": sorted(sides),
                    "Tier Source": "prizepicksapi.com",
                    "Tier Observed At": item.get("observed_at"),
                    "Tier Fetched At": fetched,
                    "Tier Projection ID": str(item["id"]),
                    "Tier Source Start (UTC)": start.isoformat(),
                    "Tier Time Match": ("Exact" if pd.to_datetime(row["Start (UTC)"],utc=True) == start else "Two-team verified; within 15 minutes"),
                })
                imported.append(row)
                matched.add((key, line))
    kept = [r for r in rows if not (
        r.get("Platform") == "prizepicks"
        and (not fantasy_only or bool(fantasy_weights(r)))
        and (r.get("Tier Source") == "prizepicksapi.com" or
             (pp_card_key(r), numeric_line(r.get("Line"))) in matched)
    )]
    if not imported:
        raise ValueError("No unique future player/game/stat matches. Existing board preserved.")
    return kept + imported, len(imported)


def pp_fantasy_check(rows, payload):
    if not payload or not isinstance(payload.get('items'), list):
        return {'Feed': 'No saved feed', 'Fantasy records returned': 0}, []
    items = payload['items']
    fantasy = [item for item in items if isinstance(item, dict)
               and 'fantasy' in str((item.get('market') or {}).get('stat','')).lower()]
    details = []
    for item in fantasy:
        market = item.get('market') or {}
        start = (item.get('event') or {}).get('starts_at')
        if pp_stat_key(market.get('stat')) != ('FANTASY',):
            reason = 'Unrecognized fantasy stat label'
        elif item.get('is_demo') is not False:
            reason = 'Demo/live status not verified'
        elif str(item.get('league','')).upper() != 'NBA':
            reason = 'Not NBA'
        elif item.get('status') != 'active':
            reason = 'Inactive quote'
        elif str(market.get('period','')).lower() != 'full game':
            reason = 'Not full game'
        elif not today_pregame(start):
            reason = 'Not an upcoming game today (Pacific)'
        else:
            try:
                pp_merge_tiers(rows, [item], payload.get('fetched_at'))
                reason = 'Ready to import'
            except (ValueError, KeyError, TypeError):
                reason = 'No unique player/game/time match or no verified offered side/tier'
        local_start = pd.to_datetime(start, utc=True, errors='coerce')
        local_text = (local_start.tz_convert(SLATE_TIMEZONE).strftime('%b %d, %Y · %I:%M %p PT')
                      if pd.notna(local_start) else 'Unavailable')
        details.append({'Player':(item.get('player') or {}).get('name'),
                        'Source stat':market.get('stat'), 'Line':item.get('line'),
                        'Start (Pacific)': local_text, 'Start (UTC)': start, 'Import check':reason})
    return {'Feed fetched (UTC)':payload.get('fetched_at'),
            'Total feed records':len(items), 'Fantasy records returned':len(fantasy),
            'Ready fantasy records':sum(d['Import check']=='Ready to import' for d in details),
            'Feed download incomplete':bool(payload.get('partial')),
            'Imported fantasy lines today':len({identity(r) for r in rows if fantasy_weights(r)})}, details


def pp_download_pages(store, cache_key, fetch_page, filters, max_pages=2, reference=None, continuation_seconds=1800):
    """Persist each successful page; explicit calls only, with a hard request cap."""
    current = pd.Timestamp.now(tz='UTC') if reference is None else pd.to_datetime(reference, utc=True)
    saved = store.get('source', cache_key) or {}
    stamp = pd.to_datetime(saved.get('updated_at'), utc=True, errors='coerce')
    reusable = (saved.get('version') == 3 and saved.get('filters') == filters
                and saved.get('slate_day') == slate_day(current)
                and pd.notna(stamp) and 0 <= (current-stamp).total_seconds() < continuation_seconds)
    if reusable and not saved.get('partial') and (current-stamp).total_seconds() < 300:
        return saved, 0
    continuing = reusable and saved.get('partial') and saved.get('next_cursor')
    state = dict(saved) if continuing else {
        'version':3, 'filters':dict(filters), 'slate_day':slate_day(current),
        'items':[], 'fetched_at':current.isoformat(), 'updated_at':current.isoformat(),
        'next_cursor':None, 'partial':True, 'seen_cursors':[], 'pages_downloaded':0,
    }
    items = {str(item['id']):item for item in state['items'] if isinstance(item,dict) and item.get('id')}
    cursor = state.get('next_cursor') if continuing else None
    seen = set(state.get('seen_cursors', []))
    requests_made = 0
    for _ in range(min(10, max(0, int(max_pages)))):
        params = dict(filters)
        if cursor:
            params['cursor'] = cursor
        body = fetch_page(params)
        requests_made += 1
        meta = body.get('meta') or {}
        if meta.get('mode') != 'live' or not isinstance(body.get('data'), list):
            raise ValueError('Expected live projection data. Previous saved pages preserved.')
        next_cursor = meta.get('next_cursor')
        if next_cursor is not None and not isinstance(next_cursor,str):
            raise ValueError('Invalid continuation cursor. Previous saved pages preserved.')
        if next_cursor and (next_cursor == cursor or next_cursor in seen):
            raise ValueError('Repeated pagination cursor. Previous saved pages preserved.')
        for item in body['data']:
            if not isinstance(item,dict) or not item.get('id'):
                raise ValueError('Projection record missing ID. Previous saved pages preserved.')
            items[str(item['id'])] = item
        if cursor:
            seen.add(cursor)
        state.update({'items':list(items.values()), 'next_cursor':next_cursor,
                      'partial':bool(next_cursor), 'seen_cursors':sorted(seen),
                      'updated_at':current.isoformat(),
                      'pages_downloaded':state.get('pages_downloaded',0)+1})
        store.put('source',cache_key,state)
        cursor = next_cursor
        if not cursor:
            break
    return state, requests_made


def pp_cached_combined():
    full = workflow_store.get('source','pp_tiers_v1') or {}
    fantasy = workflow_store.get('source','pp_fantasy_v3') or {}
    items = {}
    for payload in (full,fantasy):
        for item in payload.get('items',[]):
            if isinstance(item,dict) and item.get('id'):
                items[str(item['id'])] = item
    if not full and not fantasy:
        return None
    return {'items':list(items.values()), 'fetched_at':fantasy.get('updated_at') or full.get('fetched_at'),
            'partial':bool(full.get('partial') or fantasy.get('partial'))}



# Scheduled maintenance uses this same app.py.
# It exits before the UI and never executes the paid scan.

import sys


def player_health_key(card):
    # Names only group queries; Workflow.player still verifies the ESPN ID
    # against both current matchup rosters before any status is accepted.
    return str(card.get('Game')) + '|' + str(card.get('Player'))


def feed_request_cap():
    try:
        value = int(setting('PRIZEPICKS_DAILY_REQUEST_CAP', '10'))
        if not 1 <= value <= 100:
            raise ValueError('Invalid cap')
        return value
    except (ValueError, TypeError):
        return 10


def reserve_prizepicks_request(store):
    """One atomic counter shared by manual refreshes and scheduled workers."""
    if not store.remote:
        raise ValueError('Permanent storage is required before requesting provider pages.')
    day, cap = slate_day(), feed_request_cap()
    previous = store.get('automation', day) or {}
    # Initialize from the old ledger during upgrades; never reset today's use.
    initial = int(previous.get('feed_requests', 0))
    budget = store.query('SELECT payload FROM sports_records WHERE namespace=? AND key=?',
                         ('request_budget', day + ':prizepicks'), fetch=True)
    if budget:
        initial = int(budget[0][0])
    if initial >= cap:
        raise ValueError('Daily PrizePicks request cap reached; saved pages retained.')
    rows = store.query('INSERT INTO sports_records VALUES (?,?,?,?) '
        'ON CONFLICT(namespace,key) DO UPDATE SET '
        'payload=CAST(CAST(sports_records.payload AS INTEGER)+1 AS TEXT), updated=excluded.updated '
        'WHERE CAST(sports_records.payload AS INTEGER)<? RETURNING payload',
        ('request_budget', day + ':prizepicks', str(initial + 1), now(), cap), fetch=True)
    if not rows:
        raise ValueError('Daily PrizePicks request cap reached; saved pages retained.')
    return int(rows[0][0])


def refresh_input_checks(wf):
    """Exercise the same parsers used by the engine; never substitute zeros."""
    report = {'checked_at': now(), 'sources': {}}
    for name, args, expected in [('load_team_advanced', (wf.e['current_season'](),), 30),
                                  ('load_position_defense', (), 150)]:
        try:
            value = wf.e[name](*args)
            count = len(value['data'])
            if count != expected:
                raise ValueError('Unexpected source row count')
            report['sources'][name] = {'available': True, 'rows': count,
                'fetched_at': value.get('fetched_at'), 'checked_at': now()}
        except Exception as error:
            report['sources'][name] = {'available': False,
                'reason': type(error).__name__ + ': source validation failed', 'checked_at': now()}
    # News is displayed as attributed research context, not converted into
    # unverified numerical minute/usage adjustments.
    try:
        data = wf.e['saved_fetch'](wf.e['ESPN_BASE'] + 'news', {}, ttl_seconds=900)
        if not isinstance(data.get('articles'), list):
            raise ValueError('Invalid news feed')
        articles = []
        for item in data['articles']:
            published = pd.to_datetime(item.get('published'), utc=True, errors='coerce')
            if pd.isna(published) or not 0 <= (pd.Timestamp.now(tz='UTC') - published).total_seconds() <= 172800:
                continue
            articles.append({'headline': str(item.get('headline') or ''),
                'published': published.isoformat(), 'categories': item.get('categories') or [],
                'url': ((item.get('links') or {}).get('web') or {}).get('href')})
        report['sources']['news'] = {'available': True, 'articles': articles, 'checked_at': now()}
    except Exception as error:
        report['sources']['news'] = {'available': False,
            'reason': type(error).__name__ + ': news feed unavailable', 'checked_at': now()}
    report['limitations'] = ['Confirmed starting lineups are not connected.',
        'News is research context; injury-driven role changes and minutes restrictions are not modeled.']
    wf.store.put('input_health', 'latest', report)
    return report


def refresh_board_health(wf, board):
    """Check saved players without changing original forecasts or odds."""
    previous = wf.store.get('health', slate_day()) or {}
    players = {}
    for card in preparation_rows(board.get('v4_rows') or []):
        key = player_health_key(card)
        if key in players:
            continue
        try:
            team, player, roster_time = wf.player(card)
            availability = wf.availability(str(player['id']), str(team['id']))
            entries = availability.get('entries') or []
            statuses = sorted({str(x.get('status') or 'Unknown').strip() for x in entries})
            blocked = any(x.lower() in ('out', 'suspended') for x in statuses)
            old = (previous.get('players') or {}).get(key) or {}
            if not availability.get('available'):
                statuses = old.get('statuses') or []
            players[key] = {'player_id': str(player['id']), 'team_id': str(team['id']),
                'roster_checked_at': roster_time, 'checked_at': now(),
                'injuries_available': bool(availability.get('available')),
                'statuses': statuses, 'blocked': blocked if availability.get('available') else old.get('blocked', False),
                'note': ('Injury report unavailable; previous status: ' + ', '.join(statuses)) if not availability.get('available') else
                    (', '.join(statuses) if statuses else 'No matching injury entry; playing and starting are not confirmed.')}
        except Exception as error:
            players[key] = {'checked_at': now(), 'injuries_available': False,
                'blocked': True, 'note': calculation_failure_details(error)['reason']}
    report = {'checked_at': now(), 'players': players,
              'roster_matched': sum('player_id' in v for v in players.values()),
              'injury_checked': sum(v.get('injuries_available', False) for v in players.values()),
              'blocked': sum(v.get('blocked', False) for v in players.values()),
              'limitations': ['Confirmed starting lineups are not connected.',
                  'Injury-driven role changes and minutes restrictions are not modeled.']}
    wf.store.put('health', slate_day(), report)
    return report


def board_coverage(wf, board):
    """Count missed deadlines separately so a shrinking slate cannot look complete."""
    index = forecast_index(wf.store.items('forecast'))
    statuses = dict(wf.store.items('calculation_status'))
    cards = {}
    for card in board.get('v4_rows') or []:
        start = pd.to_datetime(card.get('Start (UTC)'), utc=True, errors='coerce')
        if card.get('Decoded') and market_columns(card) and pd.notna(start) and slate_day(start) == slate_day():
            cards.setdefault(market_signature(card), card)
    report = {'checked_at': now(), 'imported': len(cards), 'ready': 0,
              'upcoming_pending': 0, 'upcoming_unavailable': 0, 'missed_start': 0}
    for card in cards.values():
        frozen = saved_prediction(index, card)
        created = pd.to_datetime((frozen or {}).get('created_at'), utc=True, errors='coerce')
        start = pd.to_datetime(card['Start (UTC)'], utc=True)
        if frozen and pd.notna(created) and created < start:
            report['ready'] += 1
        elif not today_pregame(card['Start (UTC)']):
            report['missed_start'] += 1
        elif (statuses.get(identity(card)) or {}).get('Status') == 'Unavailable':
            report['upcoming_unavailable'] += 1
        else:
            report['upcoming_pending'] += 1
    wf.store.put('coverage', slate_day(), report)
    return report


def automatic_board_run(wf, refresh=True, budget_seconds=1200):
    if not wf.store.remote:
        raise RuntimeError('Permanent database required for automatic preparation')
    day = slate_day()
    ledger = wf.store.get('automation', day) or {'odds_requests': 0, 'feed_requests': 0}
    ledger['feed_cap'] = feed_request_cap()
    budget = wf.store.get('request_budget', day + ':prizepicks')
    if budget is not None:
        ledger['feed_requests'] = max(ledger.get('feed_requests', 0), int(budget))
    board = wf.store.get('board', 'board') or {}
    errors = []
    def reserve(kind, maximum):
        if ledger.get(kind, 0) >= maximum:
            raise ValueError('Daily ' + kind + ' budget exhausted; saved board preserved')
        ledger[kind] = ledger.get(kind, 0) + 1
        wf.store.put('automation', day, ledger)
    # At most two slate refreshes per Pacific day, including interrupted attempts.
    if refresh and ledger.get('refreshes', 0) < 2:
        ledger['refreshes'] = ledger.get('refreshes', 0) + 1
        wf.store.put('automation', day, ledger)
        reserve('odds_requests', 34)
        games = upcoming_nba()
        if games:
            reserve('odds_requests', 34)
            catalog = market_catalog()
            decoded, summaries, successful = [], [], set()
            for game in games:
                try:
                    reserve('odds_requests', 34)
                    rows, summary = decode_game(game, game_odds(str(game['fixtureId'])), catalog)
                    if rows:
                        decoded.extend(rows); summaries.extend(summary)
                        successful.add(str(game['fixtureId']))
                except Exception as error:
                    # api_get produces credential-free messages. Never display arbitrary exceptions.
                    import re
                    message = str(error)
                    safe = message if re.fullmatch(r'OddsPAPI /[a-z]+: HTTP [0-9]{3}\.', message) or message in (
                        'Unable to connect to OddsPAPI.', 'OddsPAPI returned invalid JSON.',
                        'Daily odds_requests budget exhausted; saved board preserved') else type(error).__name__ + ': fixture import unavailable'
                    errors.append('Fixture ' + str(game['fixtureId']) + ': ' + safe)
            ledger['games_imported'] = len(successful)
            ledger['games_found'] = len(games)
            board['v4_rows'] = merge_scan_rows(board.get('v4_rows', []), decoded, successful)
            board.update(v4_summary=summaries, v4_errors=errors, v4_scan_at=now())
            wf.store.put('board', 'board', board)
    # Give Fantasy its own filtered cursor so it cannot sit behind thousands
    # of ordinary props. Both feeds share the SAME existing daily call cap.
    key = setting('PRIZEPICKS_API_KEY', '')
    if key and preparation_rows(board.get('v4_rows', [])):
        def fetch_page(params):
            ledger['feed_requests'] = reserve_prizepicks_request(wf.store)
            wf.store.put('automation', day, ledger)
            response = requests.get('https://prizepicksapi.com/api/v1/projections',
                headers={'Authorization': 'Bearer ' + key}, params=params, timeout=25)
            if response.status_code != 200:
                raise ValueError('PrizePicks feed unavailable')
            return response.json()
        base_filters = {'league': 'NBA', 'status': 'active', 'limit': 200}
        feeds = [('pp_fantasy_v3', dict(base_filters, stat='Fantasy Score'), 'fantasy_partial', 2),
                 ('pp_tiers_v1', base_filters, 'feed_partial', 10)]
        downloaded = {}
        observed_at = None
        for cache_key, filters, flag, allowance in feeds:
            saved_feed = wf.store.get('source', cache_key) or {}
            same_day = saved_feed.get('slate_day') == day and saved_feed.get('filters') == filters
            needs_download = not same_day or saved_feed.get('partial', True) or refresh
            remaining = ledger['feed_cap'] - ledger.get('feed_requests', 0)
            if needs_download and remaining > 0:
                try:
                    saved_feed, calls = pp_download_pages(wf.store, cache_key, fetch_page,
                        filters, max_pages=min(allowance, remaining), continuation_seconds=86400)
                except Exception as error:
                    errors.append(type(error).__name__ + ': ' + ('Fantasy Score' if flag == 'fantasy_partial' else 'tier') + ' feed unavailable; saved pages retained')
                    saved_feed = wf.store.get('source', cache_key) or saved_feed
            ledger[flag] = not (saved_feed.get('slate_day') == day and not saved_feed.get('partial', True))
            # Even when the budget is exhausted, reprocess saved pages so a
            # prior interruption cannot leave already-downloaded lines hidden.
            if saved_feed.get('slate_day') == day and saved_feed.get('items'):
                for item in saved_feed['items']:
                    if isinstance(item, dict) and item.get('id'):
                        downloaded[str(item['id'])] = item
                observed_at = saved_feed.get('fetched_at') or observed_at
        if downloaded:
            try:
                merged, count = pp_merge_tiers(board.get('v4_rows', []), list(downloaded.values()), observed_at)
                board['v4_rows'] = merged
            except (ValueError, TypeError, KeyError):
                errors.append('Saved PrizePicks pages could not be matched to upcoming games; existing board retained.')
        wf.store.put('board', 'board', board)
    # Both checks operate on saved lines and free ESPN inputs, never new odds.
    last_check = wf.store.get('input_health', 'latest') or {}
    checked_at = pd.to_datetime(last_check.get('checked_at'), utc=True, errors='coerce')
    if pd.isna(checked_at) or (pd.Timestamp.now(tz='UTC') - checked_at).total_seconds() >= 3600:
        refresh_input_checks(wf)
    refresh_board_health(wf, board)
    report = prepare_saved_board(wf, board, budget_seconds=budget_seconds, max_picks=10000)
    ledger['coverage'] = board_coverage(wf, board)
    ledger.update(last_run=now(), errors=errors, preparation=report)
    wf.store.put('automation', day, ledger)
    return ledger


if '--check-inputs' in sys.argv:
    if not _database_online:
        raise SystemExit('Permanent database unavailable; input checks were not saved.')
    report = refresh_input_checks(workflow)
    print(encode(report))
    raise SystemExit(0 if all(item.get('available') for item in report['sources'].values()) else 1)


if '--auto-board' in sys.argv:
    if not _database_online:
        raise SystemExit('Permanent database unavailable; no feed or odds requests made.')
    try:
        report = automatic_board_run(workflow, refresh='--refresh' in sys.argv)
        print(encode(report))
        coverage = report.get('coverage') or {}
        if report.get('feed_partial') or report.get('fantasy_partial'):
            print('::warning::Provider feed is incomplete. A successful worker run does not confirm full slate coverage.', flush=True)
        if coverage.get('missed_start') or coverage.get('upcoming_unavailable'):
            print('::warning::Some imported lines missed tipoff or remain unavailable. See saved coverage and calculation reasons.', flush=True)
    except Exception as error:
        raise SystemExit('Automatic board paused: ' + type(error).__name__ + '. Saved records preserved.') from None
    raise SystemExit(0)

if "--prepare-saved" in sys.argv:
    if not _database_online or not workflow_store.remote:
        raise SystemExit("Permanent database unavailable: preparation cannot run safely in a temporary session.")
    request = workflow_store.get("preparation", "request") or {}
    board = workflow_store.get("board", "board") or {}
    if request.get("enabled") and request.get("day") == slate_day():
        print(encode(prepare_saved_board(workflow, board, budget_seconds=420, max_picks=300)))
    else:
        print("No active preparation request for today's Pacific date. No feed or odds requests made.")
    raise SystemExit(0)


if "--maintenance" in sys.argv:
    if not _database_online:
        raise SystemExit("Permanent database unavailable; maintenance skipped without changing saved data.")

    _report = workflow.maintenance()
    print({
        "run_at": _report["run_at"],
        "grading": _report["grading"],
        "model_reviews": len(_report["learning"]),
    })
    raise SystemExit(0)


# Optional password for this shared personal workspace.

import hmac

_password = (
    os.getenv("APP_PASSWORD")
    or setting("APP_PASSWORD", "")
)

if _password:
    entered = st.text_input("App password", type="password")
    if not hmac.compare_digest(entered, _password):
        st.stop()


# ---------------- MOBILE PLAYER BOARD ----------------
import html as _html


def ui_text(value):
    return _html.escape(str(value if value is not None else ''))


def ui_number(value, default=None):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def ui_name(value):
    text = str(value)
    if ',' in text:
        last, first = text.split(',', 1)
        return first.strip() + ' ' + last.strip()
    return text


def ui_stat(card):
    if fantasy_weights(card):
        return 'Fantasy Score'
    labels = {'PTS': 'PTS', 'REB': 'REB', 'AST': 'AST', 'FG3M': '3PM',
              'TOV': 'TO', 'STL': 'STL', 'BLK': 'BLK'}
    columns = market_columns(card)
    return ' + '.join(labels.get(c, c) for c in columns) if columns else str(card.get('Stat', ''))


def ui_photo(frozen):
    result = (frozen or {}).get('result', {})
    athlete = result.get('roster', {}).get('athlete', {})
    if not athlete:
        athlete = (frozen or {}).get('snapshot', {}).get('roster', {})
    photo = athlete.get('headshot', {})
    href = photo.get('href', '') if isinstance(photo, dict) else str(photo or '')
    # Only use source-provided HTTPS images; no name-based guesses or new requests.
    return href if href.startswith('https://') else ''


def ui_matches_family(a, b):
    fields = ('Fixture ID', 'Platform', 'Player ID', 'Stat Key', 'Period')
    return all(str(a.get(k)) == str(b.get(k)) for k in fields)


def ui_save_pick(card, frozen):
    key = identity(card)
    old = workflow_store.get('watchlist', key)
    if old and old.get('active', True):
        old['active'] = False
        workflow_store.put('watchlist', key, old)
    else:
        workflow_store.put('watchlist', key, {
            'active': True, 'saved_at': now(), 'card': json_safe(dict(card)),
            'forecast_id': key if frozen else None,
            'result': (frozen or {}).get('result'),
            'board_observed_at': st.session_state.get('v4_scan_at'),
        })


def ui_tier_marker(card):
    """Confirmed PrizePicks specials only; a blank icon never verifies an unknown tier."""
    if str(card.get('Platform', '')).lower() != 'prizepicks':
        return ''
    tier = str(card.get('Line Type', '')).strip().lower()
    return {'goblin': '👹', 'demon': '😈'}.get(tier, '')


def ui_card(card, frozen, saved=False):
    """Mobile-first pick card. Only frozen model values are displayed."""
    result = (frozen or {}).get('result') or {}
    health = ((workflow_store.get('health', slate_day()) or {}).get('players') or {}).get(player_health_key(card)) or {}
    direction = str(result.get('direction') or '').upper()
    projection = ui_number(result.get('projection'))
    probability = ui_number(result.get('estimated_hit_probability'))
    photo = ui_photo(frozen)
    name = ui_name(card.get('Player', 'Unknown player'))
    portrait = (f'<img class="sl-photo" src="{ui_text(photo)}" alt="Player portrait">'
                if photo else '<div class="sl-photo sl-initial">' + ui_text(''.join(w[0] for w in name.split()[:2])) + '</div>')
    line = ui_number(card.get('Line'))
    line_text = f'{line:g}' if line is not None else '—'
    is_more = direction == 'MORE'
    direction_class = 'more' if is_more else 'less' if direction == 'LESS' else 'pending'
    direction_text = ('↑ MORE' if is_more else '↓ LESS' if direction == 'LESS' else (workflow_store.get('calculation_status', identity(card)) or {}).get('Status', 'Queued'))
    platform = str(card.get('Platform', '')).upper()
    tier = str(card.get('Line Type', '')).strip() if platform == 'PRIZEPICKS' else ''
    marker = ui_tier_marker(card)
    tier_note = 'Line type unverified' if platform == 'PRIZEPICKS' and tier.lower() not in ('standard', 'regular', 'goblin', 'demon') else ''
    marker_html = f'<span title="{ui_text(tier)}">{marker}</span>' if marker else ''
    proj_text = f'{projection:.2f}' if projection is not None else '—'
    prob_text = f'{probability:.0%}' if probability is not None else '—'
    stat = ui_stat(card)
    st.markdown(f'''<div class="sl-card sl-compact">
      <div class="sl-person">{portrait}<div class="sl-player-info">
      <h3>{ui_text(name)}{marker_html}</h3><div class="sl-team">{ui_text(card.get('Game', ''))}</div>
      <div class="sl-prop">{ui_text(stat)} <span class="sl-dot">·</span> Line {line_text}</div>
      <div class="sl-muted">{ui_text(platform)}{(' · ' + ui_text(tier_note)) if tier_note else ''}</div>
      </div></div>
      <div class="sl-pickline"><div class="sl-side {direction_class}">{ui_text(direction_text)}</div>
      <div class="sl-proj"><small>Projection</small><b>{proj_text}</b></div>
      <div class="sl-proj"><small>Experimental estimate</small><b>{prob_text}</b></div></div>
      </div>''', unsafe_allow_html=True)
    if not result:
        state = workflow_store.get('calculation_status', identity(card)) or {}
        if state.get('reason'):
            st.caption(state['reason'])
    if health.get('blocked'):
        st.warning('Unavailable for current plays: ' + health.get('note', 'Player status requires review') + '. Original prediction retained for the audit.')
    elif health.get('statuses'):
        st.caption('Latest injury status: ' + ', '.join(health['statuses']) + ' · checked ' + str(health.get('checked_at')))
    if result and not result.get('selection_allowed', True):
        st.caption('This direction is not offered for the selected platform/tier.')
    key = identity(card)
    left, right = st.columns([3, 1])
    if left.button('Research  ›', key='detail_' + key, use_container_width=True):
        st.session_state['selected_prop_card'] = dict(card)
        st.rerun()
    if right.button('★ Saved' if saved else '☆ Save', key='save_' + key, use_container_width=True):
        ui_save_pick(card, frozen)
        st.rerun()


def ui_history(card):
    matched = resolve_player(str(card['Player']))
    if matched is None:
        st.info('No unique player match is available for the research breakdown.')
        return None
    player_id, name = matched
    season = current_season()
    key = f'{player_id}:{season}'
    history = st.session_state.get('deep_history', {}).get(key)
    if history is None:
        with st.spinner('Loading player research…'):
            history = fetch_player_history(player_id, name, season)
        st.session_state.setdefault('deep_history', {})[key] = history
    return history


def ui_fantasy_rates(card, history, result):
    st.markdown('#### Fantasy points per minute')
    cutoff = min(pd.to_datetime(card['Start (UTC)'], utc=True), pd.Timestamp.now(tz='UTC'))
    games = history.copy()
    games['GAME_DATE'] = pd.to_datetime(games['GAME_DATE'], utc=True)
    games = games[games['GAME_DATE'] < cutoff].drop_duplicates('GAME_ID')
    games['FP'] = score_frame(games, card)
    games['MIN'] = pd.to_numeric(games['MIN'], errors='coerce')
    games = games[games['FP'].notna() & games['MIN'].gt(0)].sort_values('GAME_DATE', ascending=False)
    rows = []
    for label, sample in [('Last 5', games.head(5)), ('Last 15', games.head(15)),
                          ('Current season', games[games['SEASON'] == current_season()])]:
        minutes = sample['MIN'].sum()
        rows.append({'Window': label, 'Games': len(sample), 'Historical FP/min': round(float(sample['FP'].sum() / minutes), 3) if minutes > 0 else None})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    minutes = ui_number(result.get('minutes'))
    projection = ui_number(result.get('projection'))
    if minutes and projection is not None:
        a, b = st.columns(2)
        a.metric('Projected FP/min', f'{projection / minutes:.3f}')
        b.metric('Projected minutes', f'{minutes:.1f}')
    weights = fantasy_weights(card)
    contributions = []
    for component in result.get('components', []):
        value = ui_number(component.get('Projection'))
        stat = component.get('Stat')
        if value is not None and stat in weights:
            contributions.append({'Stat': stat, 'Projected stat': round(value, 2),
                                  'Scoring weight': weights[stat], 'FP contribution': round(value * weights[stat], 2)})
    if contributions:
        with st.expander('Six-stat fantasy calculation'):
            st.dataframe(pd.DataFrame(contributions), hide_index=True, use_container_width=True)
            st.caption('Contributions show the matchup calculation before any learned blend. The displayed final projection may differ when a learned model is active.')
    st.caption('Historical FP/min uses total fantasy points ÷ total played minutes in each window. Projected FP/min is the frozen final projection ÷ projected minutes.')


def pick_history_sample(history, card, window='L15', location='All', reference=None, opponent_id=None):
    """Select pregame appearances once for every chart control."""
    games = history.copy()
    current = pd.Timestamp.now(tz='UTC') if reference is None else pd.to_datetime(reference, utc=True)
    start = pd.to_datetime(card.get('Start (UTC)'), utc=True, errors='coerce')
    if pd.isna(start):
        raise ValueError('A verified fixture start is required for historical comparisons.')
    games['GAME_DATE'] = pd.to_datetime(games['GAME_DATE'], utc=True, errors='coerce')
    games = games[games['GAME_DATE'] < min(start, current)].drop_duplicates('GAME_ID')
    games['VALUE'] = score_frame(games, card)
    games['VALUE'] = pd.to_numeric(games['VALUE'], errors='coerce')
    games = games[games['VALUE'].notna() & np.isfinite(games['VALUE'])]
    if location != 'All':
        games = games[games['HOME_AWAY'] == location]
    if window == 'Season':
        year = current.tz_convert('America/Los_Angeles').year
        if current.tz_convert('America/Los_Angeles').month < 10:
            year -= 1
        games = games[games['SEASON'] == f'{year}-{str(year+1)[-2:]}']
    elif window == 'H2H':
        if opponent_id is None:
            raise ValueError('Opponent identity could not be verified for H2H.')
        games = games[games['OPPONENT_ID'].astype(str) == str(opponent_id)]
    games = games.sort_values('GAME_DATE', ascending=False)
    if window == 'H2H':
        games = games.head(15)
    elif window.startswith('L'):
        games = games.head(int(window[1:]))
    return games


def pick_chart_spec(games, line, direction):
    """One interactive chart: same line, direction, scale and labels for all windows."""
    rows = []
    for _, row in games.sort_values('GAME_DATE').iterrows():
        value = float(row['VALUE'])
        tie = value == line
        hit = value > line if direction == 'MORE' else value < line
        stamp = row['GAME_DATE'].tz_convert('America/Los_Angeles')
        matchup = str(row.get('MATCHUP', 'Opponent unavailable'))
        rows.append({'Game': stamp.strftime('%m/%d') + ' · ' + matchup,
                     'Date': stamp.strftime('%Y-%m-%d'), 'Matchup': matchup,
                     'Value': value, 'Outcome': 'Tie' if tie else 'Hit' if hit else 'Miss'})
    values = [r['Value'] for r in rows] + [float(line), 0.0]
    low, high = min(values), max(values)
    padding = max((high-low)*.12, 1.0)
    domain = [low-padding if low < 0 else 0, high+padding]
    return {'height': 270, 'data': {'values': rows}, 'layer': [
        {'mark': {'type': 'bar', 'cornerRadiusTopLeft': 4, 'cornerRadiusTopRight': 4},
         'encoding': {
             'x': {'field': 'Game', 'type': 'ordinal', 'sort': None,
                   'axis': {'title': None, 'labelAngle': -45, 'labelLimit': 110}},
             'y': {'field': 'Value', 'type': 'quantitative', 'scale': {'domain': domain}, 'axis': {'title': None}},
             'color': {'field': 'Outcome', 'type': 'nominal', 'scale': {'domain': ['Hit','Miss','Tie'], 'range': ['#22e6a5','#fa7079','#94a3b8']}, 'legend': None},
             'tooltip': [{'field':'Date'}, {'field':'Matchup'}, {'field':'Value','title':'Stat result'}, {'field':'Outcome'}]}},
        {'data': {'values': [{'Line': float(line)}]},
         'mark': {'type':'rule','strokeDash':[7,4],'color':'#ffffff','strokeWidth':2},
         'encoding': {'y': {'field':'Line','type':'quantitative'}, 'tooltip':[{'field':'Line','title':'Prop line'}]}},
        {'data': {'values': [{'Line':float(line), 'Label':f'Line {line:g}'}]},
         'mark': {'type':'text','align':'left','baseline':'bottom','dy':-5,'color':'#ffffff','fontSize':12},
         'encoding': {'x': {'value':5}, 'y':{'field':'Line','type':'quantitative'},'text':{'field':'Label'}}}
    ], 'config': {'background':'transparent','view':{'stroke':None}, 'axis':{'labelColor':'#b5c5d8','gridColor':'#28384a','domainColor':'#43566d'}}}


def ui_recent_chart(card, history):
    if history is None or history.empty:
        st.info('No verified pregame game log is available for this player.')
        return
    frozen = workflow_store.get('forecast', identity(card), {})
    result = frozen.get('result') or {}
    direction = result.get('direction')
    if direction not in ('MORE','LESS'):
        st.info('Calculate this pick first so the chart can show results for its actual MORE or LESS selection.')
        return
    line = ui_number(card.get('Line'))
    if line is None:
        st.info('The comparison line is unavailable.')
        return
    st.markdown('#### ' + ui_stat(card) + ' · ' + direction + f' {line:g}')
    window = st.radio('Window', ['L5','L10','L15','L20','Season','H2H'], index=2,
                      horizontal=True, key='single_chart_window_' + identity(card))
    location = st.radio('Location', ['All','Home','Away'], horizontal=True,
                       key='single_chart_location_' + identity(card))
    opponent_id = None
    if window == 'H2H':
        teams = espn_teams()
        lookup = {engine_team_key(t['displayName']): str(t['id']) for t in teams}
        team_key = engine_team_key(result.get('team', ''))
        participants = [engine_team_key(p) for p in str(card.get('Game','')).split(' vs ')]
        opponents = [lookup[p] for p in participants if p != team_key and p in lookup]
        if team_key in participants and len(opponents) == 1:
            opponent_id = opponents[0]
    try:
        sample = pick_history_sample(history, card, window, location, opponent_id=opponent_id)
    except (ValueError, KeyError) as error:
        st.info(str(error))
        return
    if sample.empty:
        st.info('No verified appearances match these filters.')
        return
    st.vega_lite_chart(pick_chart_spec(sample, line, direction), use_container_width=True)
    values = sample['VALUE']
    hits = int((values > line).sum() if direction == 'MORE' else (values < line).sum())
    ties = int((values == line).sum())
    misses = len(values) - hits - ties
    decided = hits + misses
    rate = f'{hits/decided:.0%}' if decided else '—'
    st.markdown(f'''<div style="display:flex;gap:14px;flex-wrap:wrap;padding:12px 14px;border:1px solid #33465a;border-radius:12px;margin-top:8px">
<span><b>{direction} hits</b> {hits}/{len(values)}</span><span><b>Misses</b> {misses}</span><span><b>Ties</b> {ties}</span><span><b>Hit rate</b> {rate}</span></div>''', unsafe_allow_html=True)
    st.caption('Green: selected side hit · Red: missed · Gray: tie. Historical hit rate excludes ties; it is not a predicted probability or platform settlement.')




def ui_pick_context_strip(card, result):
    """Display saved context only; never refresh a quote or revise a forecast."""
    context = result.get('game_context') or {}
    available = context.get('available') is True
    minutes = ui_number(result.get('minutes'))
    spread = ui_number(context.get('home_spread')) if available else None
    risk = str(context.get('risk') or 'Unavailable') if available else 'Unavailable'
    if risk not in ('Lower', 'Moderate', 'Higher'):
        risk = 'Unavailable'
    colors = {'Lower':'#53dfab','Moderate':'#f0ca78','Higher':'#ff8e99','Unavailable':'#a3b6ca'}
    spread_text = (str(context.get('home', 'Home')) + f' {spread:+g}') if spread is not None else 'Unavailable'
    st.markdown('''<style>
    .sl-context-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:16px 0 6px}
    .sl-context-tile{background:#102234;border:1px solid #30485e;border-radius:12px;padding:12px 14px;min-width:0}
    .sl-context-tile small{display:block;color:#a5b8cb;font-size:.74rem;margin-bottom:6px}
    .sl-context-tile strong{display:block;color:#f1f6fc;font-size:1.05rem;overflow-wrap:anywhere}
    @media(max-width:600px){.sl-context-grid{gap:6px}.sl-context-tile{padding:10px 8px}.sl-context-tile strong{font-size:.86rem}}
    </style>''', unsafe_allow_html=True)
    st.markdown('<div class="sl-context-grid">'
        + '<div class="sl-context-tile"><small>Projected minutes</small><strong>'+ (f'{minutes:.1f}' if minutes is not None else 'Unavailable') + '</strong></div>'
        + '<div class="sl-context-tile"><small>Saved game spread</small><strong>'+ui_text(spread_text)+'</strong></div>'
        + '<div class="sl-context-tile"><small>Blowout context</small><strong style="color:'+colors[risk]+'">'+ui_text(risk)+'</strong></div></div>', unsafe_allow_html=True)
    st.caption('Saved with the forecast · Spread-based blowout context, not a calibrated probability or an automatic minutes deduction.')


def ui_frozen_sources(result):
    with st.expander('Calculation & saved data sources'):
        if not result:
            st.info('No saved calculation for this pick.')
            return
        st.json({key: result[key] for key in ('minutes','components','learning_model','probability_method','probability_note','availability','warnings') if key in result})


def ui_detail(card, forecasts):
    if st.button('‹  Plays', key='back_to_plays'):
        st.session_state.pop('selected_prop_card', None)
        st.rerun()
    frozen = forecasts.get(identity(card))
    result = (frozen or {}).get('result') or {}
    name = ui_name(card.get('Player', ''))
    photo = ui_photo(frozen)
    portrait = (f'<img class="sl-photo sl-detail-photo" src="{ui_text(photo)}" alt="Player portrait">'
                if photo else '<div class="sl-photo sl-detail-photo sl-initial">' + ui_text(''.join(w[0] for w in name.split()[:2])) + '</div>')
    line = ui_number(card.get('Line'))
    line_text = f'{line:g}' if line is not None else '—'
    st.markdown(f'''<div class="sl-detail-heading"><div class="sl-person">{portrait}<div>
    <div class="sl-eyebrow">PLAYER BREAKDOWN</div><h2>{ui_text(name)} {ui_tier_marker(card)}</h2>
    <div class="sl-team">{ui_text(card.get('Game', ''))}</div>
    <div class="sl-prop">{ui_text(ui_stat(card))} · {ui_text(str(card.get('Platform', '')).upper())}{(' · Line type unverified') if str(card.get('Platform', '')).lower() == 'prizepicks' and str(card.get('Line Type', '')).strip().lower() not in ('standard', 'regular', 'goblin', 'demon') else ''}</div>
    </div></div></div>''', unsafe_allow_html=True)
    if result:
        direction = str(result.get('direction', '')).upper()
        side = ('↑ MORE' if direction == 'MORE' else '↓ LESS' if direction == 'LESS' else 'Not calculated')
        side_class = 'more' if direction == 'MORE' else 'less' if direction == 'LESS' else 'pending'
        projection = ui_number(result.get('projection'))
        probability = ui_number(result.get('estimated_hit_probability'))
        st.markdown(f'''<div class="sl-hero"><div class="sl-side {side_class}">{ui_text(side)} {line_text}</div>
        <div class="sl-proj"><small>Model projection</small><b>{f'{projection:.2f}' if projection is not None else '—'}</b></div>
        <div class="sl-proj"><small>Estimated hit</small><b>{f'{probability:.0%}' if probability is not None else '—'}</b></div></div>''', unsafe_allow_html=True)
        st.caption('Frozen pregame forecast · Experimental probability estimate')
        if not result.get('selection_allowed', True):
            st.warning('This direction is not offered on the selected platform/tier.')
    else:
        st.info('No frozen forecast yet. Use Calculate board in Settings; no sample projection is substituted.')
    ui_pick_context_strip(card, result)
    tab = st.segmented_control('Pick research', ['Overview', 'Matchup', 'Market', 'History'],
                               default='Overview', key='pick_sections_' + identity(card),
                               label_visibility='collapsed', width='stretch') or 'Overview'
    history = None
    try:
        if tab == 'Overview':
            history = ui_history(card)
            if fantasy_weights(card) and history is not None:
                ui_fantasy_rates(card, history, result)
            availability = result.get('availability') or {}
            statuses = [str(entry.get('status', 'Unknown')) for entry in availability.get('entries', [])]
            st.caption('Availability at forecast: ' + (', '.join(statuses) if statuses else 'No confirmed playing or starting status in the saved report.'))
            ui_frozen_sources(result)
        elif tab == 'Matchup':
            render_player_availability(card)
            render_team_advanced(card)
            render_position_defense(card)
            with st.expander('Additional team scoring context'):
                if st.button('Load team scoring context', key='detail_team_scoring_' + identity(card)):
                    render_team_scoring(card)
        elif tab == 'Market':
            if card.get('Platform') == 'kalshi':
                render_kalshi(card)
            market = engine_no_vig(card)
            if market:
                st.write(f"Matching sportsbook no-vig MORE: **{market['more']:.1%}** · LESS: **{market['less']:.1%}**")
            else:
                st.caption('No unique matching sportsbook over/under pair available.')
            context = result.get('game_context') or {'available': False, 'reason': 'No game market context saved with this forecast.'}
            if st.button('Refresh game market context', key='detail_context_refresh_' + identity(card)):
                st.session_state['detail_context_' + identity(card)] = engine_game_context(card)
            refreshed = st.session_state.get('detail_context_' + identity(card))
            if refreshed is not None:
                st.caption('Refreshed display context; the original forecast is retained.')
            render_game_context(refreshed if refreshed is not None else context)
        else:
            render_card_history(card)
            history = ui_history(card)
            ui_frozen_sources(result)
        if history is not None:
            st.divider()
            st.markdown('#### Recent performance')
            ui_recent_chart(card, history)
    except Exception:
        st.warning('This research section could not load. Your frozen forecast remains unchanged.')


def ui_board(page):
    # Browsing renders saved forecasts only; worker controls live in Settings.
    rows = st.session_state.get('v4_rows', [])
    forecasts = forecast_index(workflow_store.items('forecast'))
    health = workflow_store.get('health', slate_day()) or {}
    coverage = workflow_store.get('coverage', slate_day()) or {}
    if page == 'Plays':
        if coverage:
            st.caption(f"Saved board: {coverage['ready']}/{coverage['imported']} calculated before tipoff · {coverage['missed_start']} missed their start · checked {coverage['checked_at']}")
        if health:
            st.caption(f"Player checks: {health['roster_matched']}/{len(health['players'])} roster matches · {health['injury_checked']} injury checks · {health['blocked']} unavailable · checked {health['checked_at']}")
        auto = workflow_store.get('automation', slate_day()) or {}
        if auto.get('feed_partial') or auto.get('fantasy_partial'):
            st.warning('The imported board is incomplete. Missing provider pages are excluded from these counts; see Settings for feed status.')
    for card in rows:
        frozen = saved_prediction(forecasts, card)
        if frozen:
            forecasts[identity(card)] = frozen
    if page == 'Plays' and any(
        today_pregame(f.get('card', {}).get('Start (UTC)'))
        and f.get('result', {}).get('availability', {}).get('identity_parser') != 2
        for f in forecasts.values()):
        st.warning('Some saved picks predate the injury-matching repair. Their original projections are preserved; recheck player availability before using them.')
    saved = {key: value for key, value in workflow_store.items('watchlist') if value.get('active', True)}
    selected = st.session_state.get('selected_prop_card')
    if selected is not None and page != 'Saved' and not today_pregame(selected.get('Start (UTC)')):
        st.session_state.pop('selected_prop_card', None)
        selected = None
    if selected is not None:
        ui_detail(selected, forecasts)
        return
    st.subheader('Saved plays' if page == 'Saved' else 'Your NBA plays')
    st.caption('NBA is connected. Other sports will appear here when their engines and feeds are ready.')
    platform = st.radio('Platform', ['All', 'PrizePicks', 'Kalshi', 'DraftKings'], horizontal=True, key='mobile_platform')
    kind = st.radio('Pick type', ['Player Props', 'Fantasy'], horizontal=True, key='mobile_kind')
    view = st.radio('Board view', ['Top plays', 'All props'], horizontal=True, key='mobile_view') if page != 'Saved' else 'All props'
    search = st.text_input('Find player', placeholder='Search by name…', key='mobile_search')
    if page == 'Saved':
        records = [v['card'] for v in saved.values()]
        st.caption('Saved lines stay fixed. Comparisons use the latest imported board; opening this page does not refresh prices.')
    else:
        records = rows
    unique = {}
    for card in records:
        if not card.get('Decoded') or not market_columns(card):
            continue
        if page != 'Saved':
            if not today_pregame(card.get('Start (UTC)')):
                continue
        if platform != 'All' and str(card.get('Platform')).lower() != platform.lower():
            continue
        if bool(fantasy_weights(card)) != (kind == 'Fantasy'):
            continue
        if search.strip() and search.strip().lower() not in ui_name(card['Player']).lower():
            continue
        unique.setdefault(market_signature(card), card)
    records = list(unique.values())
    if kind == 'Fantasy':
        if platform not in ('All', 'PrizePicks'):
            st.info('Fantasy Score is currently supported for PrizePicks NBA. Choose PrizePicks or All platforms.')
        elif not records:
            st.info('No PrizePicks Fantasy Score lines are imported for today. In Settings, rebuild the saved PrizePicks feed without API calls. The feed must contain an actual fantasy line.')
        elif view == 'Top plays' and not any(identity(c) in forecasts for c in records):
            st.info('Fantasy lines are imported but not calculated yet. Choose All props to inspect them; calculate the board in Settings to create forecasts.')
    a, b = st.columns(2)
    stats = ['All'] + sorted({ui_stat(card) for card in records})
    stat = a.selectbox('Stat', stats, key='mobile_stat')
    tiers = ['All', 'Standard', 'Goblin', 'Demon', 'Unverified']
    tier = b.selectbox('PrizePicks line type', tiers, key='mobile_tier')
    records = [c for c in records if (stat == 'All' or ui_stat(c) == stat)
               and (tier == 'All' or (c.get('Platform') == 'prizepicks' and c.get('Line Type') == tier))]
    if view == 'Top plays':
        records = [c for c in records if identity(c) in forecasts
                   and not (health.get('players', {}).get(player_health_key(c)) or {}).get('blocked', False)
                   and forecasts[identity(c)]['result'].get('selection_allowed', True)
                   and not forecasts[identity(c)]['result'].get('tie', False)]
        records.sort(key=lambda c: ui_number(forecasts[identity(c)]['result'].get('standardized_gap'), -1), reverse=True)
        st.caption('Ranked by projected gap relative to recent variability. This is not a proven probability or profit ranking. All props remains available.')
    else:
        records.sort(key=lambda c: (str(c['Player']), ui_stat(c), float(c['Line'])))
    # Display-only grouping: alternate thresholds are still retained as
    # separate immutable forecast identities for audit and grading.
    if page != 'Saved' and records:
        compact_lines = st.checkbox(
            'Group repeated lines (show strongest threshold per player/stat)',
            value=(view == 'Top plays'),
            key='mobile_group_repeated_lines',
            help='Only changes the visible cards. All original lines and frozen forecasts remain saved. Turn off to inspect every threshold.',
        )
        if compact_lines:
            groups = {}
            for candidate in records:
                group_key = (
                    str(candidate.get('Fixture ID', '')),
                    str(candidate.get('Platform', '')).lower(),
                    str(candidate.get('Player', '')).casefold().strip(),
                    str(candidate.get('Stat Key', '')),
                    str(candidate.get('Period', '')),
                    str(candidate.get('Line Type', '')),
                )
                groups.setdefault(group_key, []).append(candidate)
            grouped = []
            hidden = 0
            for alternatives in groups.values():
                if len(alternatives) == 1:
                    grouped.append(alternatives[0])
                    continue
                # Frozen, eligible forecasts rank ahead of uncalculated lines.
                # Do not infer confidence from unvalidated probability fields.
                def display_priority(candidate):
                    frozen = forecasts.get(identity(candidate))
                    if not frozen:
                        return (0, -1.0, -1.0)
                    result = frozen.get('result', {})
                    if not result.get('selection_allowed', True) or result.get('tie'):
                        return (1, -1.0, -1.0)
                    score = ui_number(result.get('standardized_gap'), -1)
                    gap = abs(ui_number(result.get('gap'), 0))
                    return (2, score, gap)
                grouped.append(max(alternatives, key=display_priority))
                hidden += len(alternatives) - 1
            records = grouped
            if view == 'Top plays':
                records.sort(key=lambda c: ui_number(
                    forecasts[identity(c)]['result'].get('standardized_gap'), -1
                ) if identity(c) in forecasts else -1, reverse=True)
            else:
                records.sort(key=lambda c: (str(c['Player']), ui_stat(c), float(c['Line'])))
            if hidden:
                st.caption(
                    f'{hidden} alternate threshold(s) hidden from display only. '
                    'Turn off grouping to see all lines; saved picks and grading are unchanged.'
                )
    stamp = st.session_state.get('v4_scan_at')
    if stamp:
        st.caption('Base board scan (UTC): ' + str(stamp) + ' · Native feeds may have separate timestamps. Browsing uses saved data.')
    st.caption(f'{len(records)} matching picks · Estimated hit percentages are experimental.')
    if not records:
        st.info("No matching upcoming picks for today (Pacific time). Try All props or clear filters. The app will not substitute tomorrow's games. Imports and calculation are under Settings.")
        return
    per_page = 24
    pages = max(1, (len(records) + per_page - 1) // per_page)
    page_number = int(st.number_input('Page', min_value=1, max_value=pages, value=1, step=1, key='mobile_page_' + page + '_' + kind + '_' + platform + '_' + str(len(records))))
    visible = records[(page_number - 1) * per_page:page_number * per_page]
    for offset in range(0, len(visible), 2):
        card_columns = st.columns(2, gap='small')
        for column, card in zip(card_columns, visible[offset:offset + 2]):
            with column:
                key = identity(card)
                frozen = forecasts.get(key)
                ui_card(card, frozen, key in saved)
                if page == 'Saved':
                    saved_value = saved[key]
                    st.caption('Saved at (UTC): ' + saved_value['saved_at'])
                    candidates = [row for row in rows if ui_matches_family(card, row)]
                    lines = sorted({float(row['Line']) for row in candidates if ui_number(row.get('Line')) is not None})
                    if lines:
                        st.caption('Latest imported thresholds: ' + ', '.join(f'{n:g}' for n in lines) + '. Alternate lines are not proof of line movement. A uniquely matched quote history is needed for a movement claim.')
                    else:
                        st.caption('No matching quote in the current imported board. Original saved line preserved.')



# ---------------- APP HEADER ----------------
st.markdown("""<style>
:root { color-scheme:dark; }
.stApp {background:radial-gradient(ellipse at 50% 0%,#102438 0%,#07121f 44%,#050e19 100%);color:#f3f8ff;}
.stApp h1,.stApp h2,.stApp h3,.stApp h4,.stApp p,.stApp label {color:#f3f8ff;}
.stApp [data-testid="stCaptionContainer"] p {color:#a9b9cb;}
.block-container {max-width:1040px;padding-top:1.2rem;padding-bottom:6rem;}
.sl-card {background:linear-gradient(125deg,#122638,#0b1a2a);border:1px solid #395268;border-radius:19px;padding:17px 17px 12px;margin:18px 0 6px;box-shadow:0 12px 28px #0003;}
.sl-person {display:flex;align-items:center;gap:16px;min-width:0;}
.sl-person h3 {margin:0;color:white;font-size:1.3rem;line-height:1.25;}
.sl-photo {width:78px;height:86px;object-fit:cover;border-radius:14px;background:#263d50;flex-shrink:0;}
.sl-initial {display:flex;align-items:center;justify-content:center;font-size:24px;color:#24ed9c;}
.sl-team {color:#37d6f7;font-weight:700;font-size:.93rem;margin-top:3px;}
.sl-muted {color:#9fb2c5;font-size:.77rem;margin-top:5px;}
.sl-prop {color:#f1f7fd;font-size:.92rem;margin-top:4px;}
.sl-dot {color:#23e6a2;}
.sl-pickline,.sl-hero {display:flex;align-items:center;justify-content:space-between;gap:9px;background:linear-gradient(120deg,#0c2b2b,#0d1d2b);border:1px solid #426075;border-radius:14px;padding:12px;margin-top:14px;}
.sl-side {font-size:1.08rem;font-weight:850;white-space:nowrap;text-align:center;border-radius:13px;padding:10px 14px;min-width:115px;}
.sl-side.more {background:linear-gradient(135deg,#008d46,#00d36b);border:2px solid #36ff99;color:white;box-shadow:0 0 17px #00d46c44;}
.sl-side.less {background:linear-gradient(135deg,#a71927,#d43a43);border:2px solid #ff6876;color:white;}
.sl-side.pending {background:#2c3b4c;color:#d9e2eb;font-size:.85rem;}
.sl-proj {display:flex;flex-direction:column;align-items:center;min-width:0;text-align:center;}
.sl-proj small {font-size:.69rem;color:#c1cedb;white-space:nowrap;}
.sl-proj b {font-size:1.45rem;color:#28ed99;line-height:1.3;}
.sl-detail-heading {margin:8px 0 12px;}.sl-detail-heading h2 {margin:3px 0;font-size:1.7rem;}
.sl-detail-photo {width:100px;height:110px;}.sl-eyebrow {font-size:.7rem;font-weight:800;letter-spacing:.12em;color:#9eb0c3;}
.sl-hero {border:2px solid #22e6a5;padding:18px 14px;margin:16px 0 8px;}
.sl-hero .sl-proj b {font-size:2rem;}
.stApp [data-testid="stButton"] button {border-radius:13px;border:1px solid #456073;background:#142536;color:#f0f6fc;}
.stApp [data-testid="stButton"] button:hover {border-color:#22e6a5;color:#22e6a5;}
.stApp [data-testid="stRadio"] [role="radiogroup"] {gap:.35rem;}
.stApp [data-testid="stRadio"] label {border-radius:12px;}
.stApp [data-testid="stMetric"] {background:#15283a;border:1px solid #395168;border-radius:12px;padding:10px;}
@media(max-width:600px){.block-container{padding:1rem .65rem 6rem;}.sl-card{padding:12px;}.sl-photo{width:65px;height:75px;}.sl-detail-photo{width:82px;height:94px;}.sl-person h3{font-size:1.09rem;}.sl-detail-heading h2{font-size:1.25rem;}.sl-pickline,.sl-hero{padding:9px 6px;gap:5px;}.sl-side{font-size:.83rem;min-width:90px;padding:9px 5px;}.sl-proj small{font-size:.57rem;}.sl-proj b{font-size:1.17rem;}.sl-hero .sl-proj b{font-size:1.45rem;}}

/* Board cards: compact two-column desktop layout, stacked by Streamlit on phones. */
.sl-card.sl-compact {border-radius:12px;padding:10px 11px 8px;margin:7px 0 2px;box-shadow:none;}
.sl-compact .sl-person {gap:9px;}
.sl-compact .sl-photo {width:46px;height:52px;border-radius:9px;}
.sl-compact .sl-initial {font-size:18px;}
.sl-compact .sl-player-info {min-width:0;flex:1;}
.sl-compact .sl-person h3 {font-size:1rem;line-height:1.2;overflow-wrap:anywhere;}
.sl-compact .sl-team {font-size:.7rem;line-height:1.2;margin-top:2px;font-weight:500;}
.sl-compact .sl-prop {font-size:.82rem;margin-top:3px;font-weight:700;}
.sl-compact .sl-muted {font-size:.65rem;margin-top:2px;line-height:1.2;}
.sl-compact .sl-pickline {padding:6px 7px;margin-top:7px;border-radius:9px;gap:6px;}
.sl-compact .sl-side {min-width:78px;padding:6px 7px;font-size:.82rem;border-radius:8px;box-shadow:none;border-width:1px;}
.sl-compact .sl-side.pending {font-size:.67rem;white-space:normal;}
.sl-compact .sl-proj small {font-size:.59rem;}
.sl-compact .sl-proj b {font-size:1.08rem;line-height:1.15;}
@media(max-width:600px){
 .sl-card.sl-compact {padding:8px 10px 7px;margin:5px 0 1px;}
 .sl-compact .sl-photo {width:42px;height:47px;}
 .sl-compact .sl-person h3 {font-size:.96rem;}
 .sl-compact .sl-pickline {padding:5px 7px;margin-top:6px;}
}
</style>""", unsafe_allow_html=True)
def render_pp_tier_refresh():
    rows = [row for row in st.session_state.get('v4_rows',[]) if today_pregame(row.get('Start (UTC)'))]
    with st.expander('PrizePicks Regular / Goblin / Demon feed'):
        st.caption('Separate provider, never OddsPAPI. Each refresh below makes at most 2 provider requests. Successful pages and continuation cursors are saved. Cached quotes may be delayed.')
        if st.button('Check Fantasy feed — no API calls'):
            summary, details = pp_fantasy_check(rows,pp_cached_combined())
            st.write(summary)
            if details:
                st.dataframe(pd.DataFrame(details),hide_index=True,use_container_width=True)
            else:
                st.info('No fantasy-labelled records in the saved feed. Use the targeted Fantasy Score refresh for today; website availability and feed availability can differ.')
        fantasy_refresh = st.button('Refresh Fantasy Score only (max 2 requests)',type='primary')
        full_refresh = st.button('Refresh PrizePicks lines and tiers')
        rebuild = st.button('Rebuild from saved PrizePicks feed — no API calls')
        if not (fantasy_refresh or full_refresh or rebuild):
            return
        try:
            if not rows:
                raise ValueError('No saved upcoming games for today to match against. Your old forecasts are preserved.')
            calls = 0
            if rebuild:
                payload = pp_cached_combined()
                if not payload:
                    raise ValueError('No saved feed available. No API request made.')
            else:
                key = setting('PRIZEPICKS_API_KEY','')
                if not key:
                    raise ValueError('PRIZEPICKS_API_KEY is missing.')
                filters = {'league':'NBA','status':'active','limit':200}
                if fantasy_refresh:
                    filters['stat'] = 'Fantasy Score'
                def fetch_page(params):
                    reserve_prizepicks_request(workflow_store)
                    try:
                        response = requests.get('https://prizepicksapi.com/api/v1/projections',
                            headers={'Authorization':'Bearer '+key,'X-User-Request':'Refresh my NBA Fantasy Score lines' if fantasy_refresh else 'Refresh my NBA tier-labelled board'},
                            params=params,timeout=25)
                    except requests.RequestException:
                        raise ValueError('Provider connection failed. Successful saved pages retained.') from None
                    if response.status_code != 200:
                        raise ValueError(f'Provider HTTP {response.status_code}. Successful saved pages retained.')
                    return response.json()
                payload,calls = pp_download_pages(workflow_store,
                    'pp_fantasy_v3' if fantasy_refresh else 'pp_tiers_v1',fetch_page,filters)
            items = payload['items']
            if fantasy_refresh:
                items = [item for item in items if pp_stat_key((item.get('market') or {}).get('stat')) == ('FANTASY',)]
            if not items:
                raise ValueError(f'The provider returned no {"Fantasy Score " if fantasy_refresh else ""}records for this request. Downloaded data saved; existing board preserved. Requests made: {calls}.')
            merged,count = pp_merge_tiers(rows,items,payload['fetched_at'],fantasy_only=fantasy_refresh)
            merged = [r for r in st.session_state.get('v4_rows', []) if not today_pregame(r.get('Start (UTC)'))] + merged
            st.session_state['v4_rows'] = merged
            st.session_state.pop('calculated_board',None)
            st.session_state.pop('selected_prop_card',None)
            workspace_store('board',{field:st.session_state.get(field) for field in BOARD_FIELDS})
            fantasy_count = len({identity(row) for row in merged if fantasy_weights(row)})
            st.session_state['pp_tier_notice'] = (
                f'Imported {count} outcomes; {fantasy_count} unique Fantasy Score lines. Requests made: {calls}. '
                'Calculate the board for new projections, then choose PrizePicks → Fantasy → All props. Existing frozen forecasts preserved. '
                + ('Download incomplete; cursor saved. Click the same refresh to continue, at most 2 more requests. ' if payload.get('partial') else 'This filtered download is complete. ')
                + 'Feed downloaded from (UTC): '+str(payload.get('fetched_at')))
            st.rerun()
        except Exception as error:
            st.error(str(error))
            st.caption('No automatic retry or additional refresh is performed. Check Fantasy feed can inspect saved pages without API calls.')


def import_native_kalshi_rows(rows, fetch, teams, stamp):
    series_map = {
        "KXNBAPTS": {"PTS"}, "KXNBAREB": {"REB"},
        "KXNBAAST": {"AST"}, "KXNBA3PT": {"FG3M"},
        "KXNBASTL": {"STL"}, "KXNBABLK": {"BLK"},
        "KXNBAPA": {"PTS", "AST"}, "KXNBAPR": {"PTS", "REB"},
        "KXNBARA": {"REB", "AST"}, "KXNBAPRA": {"PTS", "REB", "AST"},
    }
    aliases = {"GS": "GSW", "NY": "NYK", "NO": "NOP", "SA": "SAS", "WSH": "WAS", "PHO": "PHX", "UTAH": "UTA"}
    fixtures = {}
    for row in rows:
        if row.get("Decoded") and str(row.get("Period", "")).lower() == "result":
            if pd.to_datetime(row["Start (UTC)"], utc=True) > stamp:
                fixtures.setdefault(str(row["Fixture ID"]), []).append(row)
    imported, seen, warnings, available = [], set(), [], 0
    for fixture_rows in fixtures.values():
        game = fixture_rows[0]
        names = {engine_team_key(x) for x in str(game["Game"]).split(" vs ")}
        matched = [t for t in teams if engine_team_key(t["displayName"]) in names]
        if len(names) != 2 or len(matched) != 2:
            warnings.append(str(game["Game"]) + ": team mapping unavailable.")
            continue
        codes = [aliases.get(t["abbreviation"], t["abbreviation"]) for t in matched]
        day = pd.to_datetime(game["Start (UTC)"], utc=True).tz_convert("America/New_York").strftime("%y%b%d").upper()
        for series, columns in series_map.items():
            templates = {}
            for row in fixture_rows:
                if not fantasy_weights(row) and set(market_columns(row) or []) == columns:
                    templates.setdefault(normalize_player(row["Player"]), []).append(row)
            if not templates:
                continue
            for first, second in (codes, codes[::-1]):
                ticker = f"{series}-{day}{first}{second}"
                try:
                    data = fetch("events/" + ticker)
                except Exception:
                    warnings.append(ticker + ": public request failed.")
                    continue
                event = data.get("event") or {}
                if event.get("event_ticker") != ticker or event.get("series_ticker") != series:
                    continue
                available += 1
                for market in data.get("markets", event.get("markets", [])):
                    if market.get("event_ticker") != ticker or market.get("status") not in ("active", "open"):
                        continue
                    if market.get("strike_type") != "greater" or market.get("market_type") != "binary":
                        continue
                    if float(market.get("notional_value_dollars", "1")) != 1:
                        continue
                    close = pd.to_datetime(market.get("close_time"), utc=True, errors="coerce")
                    if pd.isna(close) or close <= stamp:
                        continue
                    title = str(market.get("title") or market.get("yes_sub_title", ""))
                    candidates = templates.get(normalize_player(title.split(":", 1)[0]), [])
                    if not candidates or len({str(r["Player ID"]) for r in candidates}) != 1:
                        continue
                    line = numeric_line(market.get("floor_strike"))
                    if line is None or abs(line + 0.5 - round(line + 0.5)) > 1e-6:
                        continue
                    contract = market.get("ticker")
                    if not contract:
                        continue
                    for side, direction in (("yes", "MORE"), ("no", "LESS")):
                        try:
                            ask = float(market.get(side + "_ask_dollars") or "nan")
                        except (ValueError, TypeError):
                            continue
                        if not math.isfinite(ask) or not 0 < ask < 1:
                            continue
                        key = (contract, side)
                        if key in seen:
                            continue
                        seen.add(key)
                        row = dict(candidates[0])
                        for field in ("Available Sides", "Tier Source", "Tier Observed At", "Tier Fetched At", "Tier Projection ID"):
                            row.pop(field, None)
                        row.update({
                            "Platform": "kalshi", "Line": line, "Raw Handicap": line,
                            "Line Type": "Unverified", "Direction": direction,
                            "Market ID": "native:" + contract, "Outcome ID": side,
                            "Odds": None, "Decoded": True, "mainLine Flag": None,
                            "Line Status": "Native Kalshi greater-than threshold",
                            "Kalshi Ticker": contract, "Kalshi Source": "public API",
                            "Kalshi Ask Dollars": ask, "Kalshi Fetched At": stamp.isoformat(),
                        })
                        imported.append(row)
    return imported, warnings, available


def render_native_kalshi_refresh():
    rows = [row for row in st.session_state.get("v4_rows", [])
            if today_pregame(row.get("Start (UTC)"))]
    if not rows:
        return
    with st.expander("Kalshi native player contracts"):
        st.caption("Loads available contracts for matched players and stats in your saved future games. Uses public Kalshi data, not OddsPAPI. Quotes need order-book and fee checks; an ask alone does not guarantee a fill.")
        if not st.button("Load Kalshi contracts for saved games"):
            return
        try:
            with st.spinner("Checking native Kalshi contracts..."):
                imported, warnings, events = import_native_kalshi_rows(
                    rows, kalshi_public, espn_teams(), pd.Timestamp.now(tz="UTC")
                )
            if not imported:
                st.warning(f"No uniquely matched open player contracts found ({events} matching events). Existing board preserved.")
                for warning in warnings[:5]:
                    st.caption(warning)
                return
            # Replace native imports only. Keep other providers and frozen forecasts.
            merged = [r for r in rows if r.get("Kalshi Source") != "public API"] + imported
            st.session_state["v4_rows"] = merged
            st.session_state.pop("calculated_board", None)
            st.session_state.pop("selected_prop_card", None)
            workspace_store("board", {field: st.session_state.get(field) for field in BOARD_FIELDS})
            st.session_state["native_kalshi_notice"] = f"Imported {len(imported)} Kalshi priced outcomes. Select Platform: kalshi and Line type: All. Calculate the board to save new forecasts."
            if warnings:
                st.session_state["native_kalshi_notice"] += f" {len(warnings)} event requests/mappings were unavailable; coverage is partial."
            st.rerun()
        except Exception as error:
            st.error(str(error))


# Navigation widgets rerun only this fragment, avoiding database startup.
@st.fragment
def render_app_pages():
    st.markdown("## ▥ SPORTS :green[LAB]")
    st.caption("Model picks · Research · Results")
    app_page = st.radio("Navigation", ["Plays", "Saved", "Results", "Settings"], horizontal=True, key="sports_lab_page")
    if st.session_state.get("last_ui_page") != app_page:
        st.session_state.pop("selected_prop_card", None)
        st.session_state["last_ui_page"] = app_page
    # Replace the previous page body before doing any page-specific reads.
    page_body = st.empty()
    page_body.empty()
    with page_body.container(key="sports_lab_page_body_" + app_page.lower()):
        loading = st.empty()
        loading.caption("Opening " + app_page + "…")
        render_saved_workspace(controls=(app_page == "Settings"))
        if app_page == "Settings":
            st.subheader("NBA data and board setup")
            st.caption("App build: navigation-3 · detail-layout-1")
            st.caption("Existing forecasts, grading, learning and paper ROI are preserved. These controls do not refresh unless you click them.")
            st.write("Odds key configured" if setting("ODDS_API_KEY", "") else "ODDS_API_KEY missing")

        # ---------------- NBA SCANNER ----------------


        # Grading runs in the scheduled worker or the explicit Results refresh control.
        # Board browsing never waits for a whole maintenance pass.

        if app_page in ("Results", "Settings"):
            render_workflow(workflow, st,
                            [row for row in st.session_state.get("v4_rows", [])
                             if today_pregame(row.get("Start (UTC)"))] if app_page == "Settings" else [],
                            mode="results" if app_page == "Results" else "settings")

        # PrizePicks tier feed: explicit refresh only.




        # Import native Kalshi contracts for the games already on the saved board.




        if app_page == "Settings":
            render_native_kalshi_refresh()
            if st.session_state.get("native_kalshi_notice"):
                st.info(st.session_state["native_kalshi_notice"])

            render_pp_tier_refresh()
            if st.session_state.get("pp_tier_notice"):
                st.info(st.session_state["pp_tier_notice"])

            scan_scope = st.radio('Today’s games to scan (Pacific time)',
                ['All today’s games', 'Limit game count'], horizontal=True, key='nba_scan_scope')
            max_games = None
            if scan_scope == 'Limit game count':
                max_games = int(st.number_input('Maximum games this scan', min_value=1,
                    value=3, step=1, key='nba_scan_limit'))
            if st.button('Load scan estimate and schedule preview', key='load_scan_preview_once'):
                try:
                    preview = scan_schedule_preview(slate_day(), saved_fetch)
                    estimated_games = len(preview) if max_games is None else min(len(preview), max_games)
                    st.info(f'Estimated scan: {estimated_games} upcoming games today · '
                        f'up to {estimated_games + 2} OddsPAPI HTTP requests if all data is uncached '
                        '(fixtures + market catalog + one odds request per game).')
                    st.caption('Estimate uses the public ESPN schedule, with no OddsPAPI call. '
                        'Provider coverage may differ. HTTP requests are not a guaranteed credit count; '
                        'your provider determines credit charges. Started games are excluded.')
                    with st.expander('Preview today’s upcoming games'):
                        if preview:
                            st.dataframe(pd.DataFrame(preview), hide_index=True, use_container_width=True)
                        else:
                            st.info('No upcoming games found for today in Pacific time.')
                except Exception:
                    st.warning('Public schedule preview unavailable. Request estimate unavailable. '
                        'A scan still uses only today’s upcoming provider games: one fixtures request, '
                        'one catalog request, and one odds request per game when uncached.')
            st.caption('Odds, fixtures and market catalog are cached for 30 minutes. '
                'Only the scan button below requests OddsPAPI data. '
                'Breakdown, grading and availability do not run this scan.')

            if st.button(
                "🔍 SCAN AND DECODE NBA LINES",
                use_container_width=True,
            ):
                try:
                    with st.spinner("Loading NBA fixtures and market lines..."):
                        games = upcoming_nba()
                        if max_games is not None:
                            games = games[:max_games]
                        all_rows, summaries, errors = [], [], []
                        successful_fixtures = set()

                        if not games:
                            st.warning(
                                "No upcoming NBA games returned "
                                "for today in Pacific time. Tomorrow's games are not included."
                            )

                        else:
                            catalog = market_catalog()

                            for game in games:
                                try:
                                    decoded_rows, summary = decode_game(
                                        game,
                                        game_odds(str(game["fixtureId"])),
                                        catalog,
                                    )

                                    if not decoded_rows:
                                        raise ValueError('No decoded lines returned; saved lines retained.')
                                    all_rows.extend(decoded_rows)
                                    summaries.extend(summary)
                                    successful_fixtures.add(str(game['fixtureId']))

                                except ValueError as error:
                                    errors.append(
                                        f"{game.get('participant1Name', '?')} vs "
                                        f"{game.get('participant2Name', '?')}: "
                                        f"{error}"
                                    )

                        st.session_state["v4_rows"] = merge_scan_rows(
                            st.session_state.get('v4_rows', []), all_rows, successful_fixtures)
                        st.session_state.pop('calculated_board', None)
                        st.session_state.pop('selected_prop_card', None)
                        st.session_state["v4_summary"] = summaries
                        st.session_state["v4_errors"] = errors
                        st.session_state["v4_scan_at"] = (
                            datetime.now(timezone.utc).isoformat()
                        )

                        workspace_store(
                            "board",
                            {
                                key: st.session_state[key]
                                for key in BOARD_FIELDS
                            },
                        )

                        st.success(f'Scanned {len(successful_fixtures)} of {len(games)} upcoming games. '
                            'Saved Fantasy lines and frozen forecasts preserved. '
                            'Rebuild from the saved PrizePicks feed to match Fantasy lines to newly scanned games, '
                            'then calculate and freeze the board. Neither step runs an OddsPAPI scan.')

                except ValueError as error:
                    st.error(str(error))


        # ---------------- RESULTS AND PLAYER LINES ----------------
        if app_page in ("Plays", "Saved"):
            ui_board(app_page)

        if app_page == "Settings":
            # ---------------- OPTIONAL DISCOVERY PREVIEW ----------------

            st.divider()

            with st.expander(
                "Optional NBA historical discovery preview"
            ):
                st.info(
                    "Player breakdowns resolve current roster identities "
                    "automatically. This optional discovery batch provides "
                    "a league preview. Saved ESPN responses use a local cache. "
                    "The main board and forecasts use permanent storage "
                    "when DATABASE_URL is configured."
                )

                _season_year = int(
                    current_season().split("-")[0]
                )

                _previous_season = (
                    f"{_season_year - 1}-{str(_season_year)[-2:]}"
                )

                history_season = st.selectbox(
                    "Historical season",
                    [
                        current_season(),
                        _previous_season,
                    ],
                    key="history_season",
                )

                if st.button("LOAD NBA DISCOVERY BATCH"):
                    try:
                        with st.spinner(
                            "Requesting real NBA player game logs..."
                        ):
                            history = load_nba_player_logs(
                                history_season
                            )

                        st.session_state["nba_history"] = history
                        st.session_state["nba_history_season"] = (
                            history_season
                        )

                    except Exception as error:
                        st.session_state.pop(
                            "nba_history",
                            None,
                        )

                        st.session_state.pop(
                            "nba_history_season",
                            None,
                        )

                        st.error(
                            "NBA historical connection failed."
                        )

                        st.code(
                            f"{type(error).__name__}: {error}",
                            language="text",
                        )

                        st.caption(
                            "No sample data was substituted."
                        )

                if (
                    "nba_history" in st.session_state
                    and st.session_state.get(
                        "nba_history_season"
                    ) == history_season
                ):
                    history = st.session_state["nba_history"]
                    logs = history["logs"]

                    st.success(
                        f"Loaded {len(logs):,} player-game records "
                        f"for {history_season}."
                    )

                    st.caption(
                        "Source: ESPN · Seven-calendar-day discovery batch · "
                        f"Regular season · Latest game returned: "
                        f"{logs['GAME_DATE'].max():%Y-%m-%d} · "
                        f"Retrieved at (UTC): {history['retrieved_at']} · "
                        "Cache: 1 hour"
                    )

                    st.dataframe(
                        logs[
                            [
                                "PLAYER_NAME",
                                "GAME_DATE",
                                "MATCHUP",
                                "MIN",
                                "PTS",
                                "REB",
                                "AST",
                                "STL",
                                "BLK",
                                "TOV",
                                "FG3M",
                            ]
                        ].head(30),
                        use_container_width=True,
                        hide_index=True,
                    )


            with st.expander(
                "What is connected and what remains"
            ):
                st.write(
                    "Connected: NBA odds scanner, market decoding, compact "
                    "player lines, historical research, rolling windows, H2H, "
                    "static charts, team efficiency, position defense, "
                    "roster/depth-chart matching, injury checks, projections, "
                    "game-market context, matching prop pricing, frozen forecasts, "
                    "automatic research grading, guarded learning reviews, "
                    "model paper ROI and percentage tracking by PrizePicks tier."
                )

                st.write(
                    "Unmatched players and unsupported markets remain unavailable. "
                    "Depth charts and active roster status are not confirmed "
                    "starting lineups. Initial percentage estimates are "
                    "experimental and are audited against real outcomes."
                )

                st.write(
                    "Outside this NBA release: other sports, salary-cap lineup "
                    "builders, additional fantasy scoring systems and verified "
                    "starting-lineup feeds. Permanent storage and scheduled "
                    "maintenance require DATABASE_URL."
                )


            if HISTORY_DB.exists():
                if st.button("Prepare historical cache backup", key="prepare_history_cache_backup"):
                    # Read only when requested, not on every visit to Settings.
                    st.session_state["_prepared_history_cache_backup"] = HISTORY_DB.read_bytes()
                cache_backup = st.session_state.get("_prepared_history_cache_backup")
                if cache_backup is not None:
                    st.download_button(
                        "Back up historical response cache",
                        data=cache_backup,
                        file_name="sports_lab_history.sqlite3",
                        mime="application/vnd.sqlite3",
                    )

        loading.empty()


render_app_pages()

# ================= END OF APP.PY =================


@st.fragment(run_every="4s" if st.session_state.get("_prepare_active") else None)
def _resume_preparation_fragment():
    if not st.session_state.get("_prepare_active") or st.session_state.get('sports_lab_page') != 'Settings':
        return
    board = workflow_store.get("board", "board") or {}
    if board.get("v4_rows") != st.session_state.get("v4_rows"):
        board = {field: st.session_state.get(field) for field in BOARD_FIELDS}
        workflow_store.put("board", "board", board)
    try:
        report = prepare_saved_board(workflow, board, budget_seconds=15, max_picks=5)
    except Exception as error:
        st.session_state['_prepare_active'] = False
        st.error(storage_failure_hint(error))
        st.info('Preparation paused. Existing forecasts remain saved; export a workspace backup before restarting.')
        return
    st.caption(f"Preparation: {report['ready']}/{report['total']} ready · "
               f"{report['pending']} queued · {report['unavailable']} unavailable")
    if report['state'] == 'complete':
        st.session_state['_prepare_active'] = False
        st.rerun()
    # Refresh cards after successful work, without requiring another user click.
    if report['attempted']:
        st.rerun()

_resume_preparation_fragment()


@st.fragment(run_every="4s" if st.session_state.get("_audit_active") else None)
def _resume_grading_fragment():
    if not st.session_state.get('_audit_active') or st.session_state.get('sports_lab_page') != 'Results':
        return
    try:
        with st.spinner('Grading all completed games…'):
            report = run_grading_pass(workflow, time_budget=15)
    except Exception as error:
        st.session_state['_audit_active'] = False
        st.error('Grading paused: ' + type(error).__name__ + '. Saved grades are preserved; check app logs.')
        return
    if report['state'] == 'complete':
        st.session_state['_audit_active'] = False
    st.rerun()

_resume_grading_fragment()
