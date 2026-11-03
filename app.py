"""Sports Lab: real NBA odds and historical research."""

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

BASE = "https://api.oddspapi.io/v4"
BOOKS = ("prizepicks", "kalshi", "draftkings")
ESPN_BASE = (
    "https://site.api.espn.com/apis/site/v2/"
    "sports/basketball/nba/"
)
HISTORY_DB = Path("sports_lab_history.sqlite3")


# ---------------- ODDS CONNECTION ----------------

def api_get(path, params):
    key = st.secrets.get("ODDS_API_KEY", "")
    if not key:
        raise ValueError("Set ODDS_API_KEY in Streamlit Secrets.")

    try:
        response = requests.get(
            BASE + path,
            params={**params, "apiKey": key},
            timeout=60,
        )
    except requests.RequestException:
        raise ValueError("Unable to connect to OddsPAPI.") from None

    if response.status_code != 200:
        raise ValueError(
            f"OddsPAPI {path}: HTTP {response.status_code}."
        )

    try:
        return response.json()
    except ValueError:
        raise ValueError("OddsPAPI returned invalid JSON.") from None


@st.cache_data(ttl=1800, show_spinner=False)
def upcoming_nba():
    start = datetime.now(timezone.utc)
    end = start + timedelta(days=3)
    data = api_get(
        "/fixtures",
        {
            "sportId": 11,
            "from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "statusId": 0,
        },
    )

    if not isinstance(data, list):
        raise ValueError("Unexpected fixtures response structure.")

    return sorted(
        [
            game for game in data
            if isinstance(game, dict)
            and game.get("fixtureId")
            and (
                "nba" in str(game.get("tournamentName", "")).lower()
                or str(game.get("tournamentSlug", "")).lower() == "nba"
            )
        ],
        key=lambda game: str(game.get("startTime", "")),
    )


@st.cache_data(ttl=1800, show_spinner=False)
def market_catalog():
    data = api_get("/markets", {"language": "en"})
    if isinstance(data, dict):
        data = data.get("markets", data.get("data", []))
    if not isinstance(data, list):
        raise ValueError("Unexpected market catalog structure.")

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
        return number if 0 <= number <= 1000 else None
    except (ValueError, TypeError):
        return None


def outcome_direction(definition, outcome_id):
    options = definition.get("outcomes", [])
    if not isinstance(options, list):
        return None
    for option in options:
        if (
            isinstance(option, dict)
            and str(option.get("outcomeId")) == str(outcome_id)
        ):
            name = str(option.get("outcomeName", "")).strip().lower()
            return {"over": "MORE", "under": "LESS"}.get(name)
    return None


def explicit_tier(*records):
    fields = ("lineType", "tier", "label", "badge", "projectionType")
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
        if re.search(r"\b" + token + r"\b", text):
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

    now = datetime.now(timezone.utc)
    if found:
        age = (
            now - datetime.fromisoformat(found[0])
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
    now = datetime.now(timezone.utc)
    if now < season_start:
        raise ValueError("The selected season has not started.")

    end = min(now, season_end - timedelta(days=1))
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


def fetch_player_history(player_id, player_name, season):
    year = int(season.split("-")[0])
    rows = []
    for start in (year, year - 1):
        label = f"{start}-{str(start + 1)[-2:]}"
        url = (
            "https://site.web.api.espn.com/apis/common/v3/"
            "sports/basketball/nba/athletes/"
            f"{player_id}/gamelog"
        )
        data = saved_fetch(url, {"season": start + 1})
        rows.extend(
            parse_player_log(data, player_id, player_name, label)
        )

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
    return frame.sort_values("GAME_DATE", ascending=False)


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
            "Load the NBA discovery batch first. "
            "A unique ESPN player match is required."
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
        st.caption("Click Refresh to check availability.")
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

def render_history_graph(sample, h2h_games, card):
    chart_window = st.radio(
        "Games to display",
        ["Last 5", "Last 10", "Last 15", "Last 20", "H2H"],
        index=3,
        horizontal=True,
        key="player_history_chart_window_v2",
    )

    if chart_window == "H2H":
        if h2h_games is None or h2h_games.empty:
            st.info("No confirmed H2H games are available for this chart.")
            return
        chart_data = h2h_games.sort_values(
            "GAME_DATE"
        ).reset_index(drop=True)
    else:
        window_size = int(chart_window.split()[-1])
        chart_data = (
            sample.sort_values("GAME_DATE", ascending=False)
            .head(window_size)
            .sort_values("GAME_DATE")
            .reset_index(drop=True)
        )

    if chart_data.empty:
        st.info("No games available for this chart.")
        return

    prop_line = float(card["Line"])
    chart_width, chart_height = 1000, 360
    left, right, top, bottom = 55, 20, 35, 65
    plot_width = chart_width - left - right
    plot_height = chart_height - top - bottom
    maximum = max(
        float(chart_data["VALUE"].max()), prop_line, 1.0
    ) * 1.2

    def chart_y(value):
        return top + plot_height * (1 - value / maximum)

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {chart_width} {chart_height}" '
        'style="width:100%;height:100%;font-family:Arial">'
    ]

    for tick in range(5):
        value = maximum * tick / 4
        y = chart_y(value)
        svg.append(
            f'<line x1="{left}" y1="{y}" '
            f'x2="{chart_width-right}" y2="{y}" '
            'stroke="#d1d5db" stroke-width="1"/>'
            f'<text x="{left-10}" y="{y+4}" '
            'text-anchor="end" fill="#64748b" '
            f'font-size="12">{value:g}</text>'
        )

    slot_width = plot_width / len(chart_data)
    bar_width = min(slot_width * 0.62, 55)

    for index, row in chart_data.iterrows():
        value = float(row["VALUE"])
        center = left + slot_width * (index + 0.5)
        y = chart_y(value)
        height = max(chart_y(0) - y, 0)
        color = (
            "#16a34a" if value > prop_line
            else "#dc2626" if value < prop_line
            else "#94a3b8"
        )
        opponent = html.escape(str(row["MATCHUP"]).split()[-1])
        date_label = row["GAME_DATE"].strftime("%m/%d")
        svg.append(
            f'<rect x="{center-bar_width/2}" y="{y}" '
            f'width="{bar_width}" height="{height}" '
            f'fill="{color}" rx="3"/>'
            f'<text x="{center}" y="{y-7}" '
            'text-anchor="middle" fill="#475569" '
            f'font-size="13">{value:g}</text>'
            f'<text x="{center}" y="{chart_y(0)+22}" '
            'text-anchor="middle" fill="#475569" '
            f'font-size="12">{opponent}</text>'
            f'<text x="{center}" y="{chart_y(0)+40}" '
            'text-anchor="middle" fill="#64748b" '
            f'font-size="10">{date_label}</text>'
        )

    line_y = chart_y(prop_line)
    svg.append(
        f'<line x1="{left}" y1="{line_y}" '
        f'x2="{chart_width-right}" y2="{line_y}" '
        'stroke="#2563eb" stroke-width="2" '
        'stroke-dasharray="7,5"/>'
        f'<text x="{chart_width-right}" y="{line_y-7}" '
        'text-anchor="end" fill="#2563eb" '
        f'font-size="13">Prop: {prop_line:g}</text>'
    )
    svg.append("</svg>")

    components.html(
        '<div style="height:360px;overflow:hidden">'
        + "".join(svg) + "</div>",
        height=370,
        scrolling=False,
    )
    st.caption(
        f"Showing {len(chart_data)} games, oldest to newest. "
        "Green: above line · Red: below line · Gray: tie."
    )
    st.caption(f"Comparison line: {prop_line:g}")
    st.dataframe(
        chart_data[
            ["GAME_DATE", "SEASON", "MATCHUP", "MIN", "VALUE"]
        ].sort_values("GAME_DATE", ascending=False),
        hide_index=True,
        use_container_width=True,
    )


# ---------------- PLAYER BREAKDOWN ----------------

@st.cache_data(ttl=3600, show_spinner=False)
def load_team_advanced(season):
    from html.parser import HTMLParser
    import math

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

    # First matching table is overall.
    # Subsequent tables contain last-five, home and road splits.
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
        "nba_history_season", "2026-27"
    )

    try:
        # One shared request per season per hour.
        # Opening another card reuses the cached table.
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
        "earlier fixture. Defense versus position and P2 adjustments "
        "are not connected yet."
    )


def render_card_history(card):
    render_player_availability(card)
    render_team_scoring(card)
    render_team_advanced(card)

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
            "Load the seven-day NBA discovery batch below first. "
            "Unmatched or ambiguous players remain unavailable."
        )
        return

    player_id, name = matched
    season = st.session_state.get("nba_history_season", "2026-27")
    key = f"{player_id}:{season}"
    card_key = ":".join(
        str(card[column]) for column in [
            "Fixture ID", "Platform", "Player ID",
            "Stat Key", "Period", "Line", "Line Type",
        ]
    )
    history = st.session_state.get("deep_history", {}).get(key)

    if history is None:
        if not st.button(
            "Load 5 / 10 / 15 / 20 history",
            key="history:" + card_key,
        ):
            st.caption(
                "Two season requests on first load. "
                "Saved responses are reused for one hour."
            )
            return
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
            "Missing fixture start time: historical comparison unavailable."
        )
        return

    cutoff = min(cutoff, pd.Timestamp.now(tz="UTC"))
    sample = history[history["GAME_DATE"] < cutoff].copy()
    sample["VALUE"] = sample[columns].sum(
        axis=1, min_count=len(columns)
    )
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
            ]
            summaries.append(
                window_summary(
                    h2h_games, float(card["Line"]), "H2H (two seasons)"
                )
            )
        else:
            st.caption(
                "H2H unavailable: current matchup/team "
                "mapping needs confirmation."
            )
    except Exception:
        st.caption("H2H unavailable: ESPN team mapping did not load.")

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
            if column in h2h_games.columns:
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

    render_history_graph(sample, h2h_games, card)


# ---------------- APP HEADER ----------------

st.title("🎯 Elite Individual Analytics Lab")
st.caption("Real NBA market inventory · Historical research")
st.sidebar.header("📡 Data connection")

if st.secrets.get("ODDS_API_KEY", ""):
    st.sidebar.success("ODDS_API_KEY configured")
else:
    st.sidebar.warning("ODDS_API_KEY missing")

if "nba_history" in st.session_state:
    st.sidebar.success(
        "Historical logs loaded: "
        + st.session_state.get("nba_history_season", "")
    )
else:
    st.sidebar.info("Historical box scores: not loaded")

st.sidebar.info("Player-card history: open View breakdown")
st.sidebar.info("Availability: manual ESPN refresh")
st.sidebar.info("P1/P2 projections: not connected")
st.sidebar.info("ROI tracking: not connected")


# ---------------- NBA SCANNER ----------------

st.subheader("🏀 NBA Pregame Scanner + Line Inventory")
max_games = st.slider("Games to scan", 1, 5, 3)
st.caption(
    f"Fresh scan uses up to {max_games + 2} API calls. "
    "Odds are cached for 30 minutes. "
    "Breakdown and availability buttons do not run this scan."
)

if st.button(
    "🔍 SCAN AND DECODE NBA LINES",
    use_container_width=True,
):
    try:
        with st.spinner("Loading NBA fixtures and market lines..."):
            games = upcoming_nba()[:max_games]
            all_rows, summaries, errors = [], [], []
            if not games:
                st.warning(
                    "No upcoming NBA games returned for the next three days."
                )
            else:
                catalog = market_catalog()
                for game in games:
                    try:
                        rows, summary = decode_game(
                            game,
                            game_odds(str(game["fixtureId"])),
                            catalog,
                        )
                        all_rows.extend(rows)
                        summaries.extend(summary)
                    except ValueError as error:
                        errors.append(
                            f"{game.get('participant1Name', '?')} vs "
                            f"{game.get('participant2Name', '?')}: {error}"
                        )

            st.session_state["v4_rows"] = all_rows
            st.session_state["v4_summary"] = summaries
            st.session_state["v4_errors"] = errors
            st.session_state["v4_scan_at"] = (
                datetime.now(timezone.utc).isoformat()
            )
    except ValueError as error:
        st.error(str(error))


# ---------------- RESULTS AND PLAYER CARDS ----------------

if "v4_rows" in st.session_state:
    for message in st.session_state.get("v4_errors", []):
        st.warning(message)

    summary = st.session_state.get("v4_summary", [])
    if summary:
        with st.expander("Platform coverage"):
            st.dataframe(
                pd.DataFrame(summary),
                use_container_width=True,
                hide_index=True,
            )

    rows = st.session_state["v4_rows"]
    if not rows:
        st.info("No active named NBA player-prop outcomes returned.")
    else:
        df = pd.DataFrame(rows)
        group_keys = [
            "Fixture ID", "Platform", "Player", "Stat Key", "Period",
        ]
        df["Distinct Thresholds"] = df.groupby(
            group_keys, dropna=False
        )["Line"].transform("nunique").astype(int)
        df["Multiple Thresholds"] = df["Distinct Thresholds"] > 1

        a, b, c = st.columns(3)
        a.metric("Active prop outcomes", len(df))
        b.metric("Line + direction decoded", int(df["Decoded"].sum()))
        c.metric(
            "Tier explicitly verified",
            int((df["Line Type"] != "Unverified").sum()),
        )
        st.caption(
            "Last scan completed (UTC): "
            + st.session_state.get("v4_scan_at", "Unavailable")
            + " · Source responses may be cached for up to 30 minutes."
        )

        platform = st.selectbox("Platform", ["All"] + list(BOOKS))
        show = st.radio(
            "View",
            [
                "All active props", "Decoded only",
                "Multiple thresholds", "Unverified line types",
            ],
            horizontal=True,
        )
        filtered = (
            df.copy() if platform == "All"
            else df[df["Platform"] == platform].copy()
        )
        if show == "Decoded only":
            filtered = filtered[filtered["Decoded"]]
        elif show == "Multiple thresholds":
            filtered = filtered[filtered["Multiple Thresholds"]]
        elif show == "Unverified line types":
            filtered = filtered[filtered["Line Type"] == "Unverified"]

        st.subheader("Player cards")
        search = st.text_input(
            "Find a player", placeholder="Type a player's name..."
        )
        stat_filter = st.selectbox(
            "Stat",
            ["All"] + sorted(filtered["Stat"].dropna().unique().tolist()),
        )
        tier_filter = st.selectbox(
            "Line type",
            ["All", "Standard", "Goblin", "Demon", "Unverified"],
        )

        cards = filtered[filtered["Decoded"]].copy()
        if search.strip():
            cards = cards[
                cards["Player"].str.contains(
                    search.strip(), case=False, regex=False, na=False
                )
            ]
        if stat_filter != "All":
            cards = cards[cards["Stat"] == stat_filter]
        if tier_filter != "All":
            cards = cards[cards["Line Type"] == tier_filter]

        card_keys = [
            "Fixture ID", "Platform", "Player ID",
            "Stat Key", "Period", "Line", "Line Type",
        ]
        cards = cards.drop_duplicates(card_keys).sort_values(
            ["Player", "Stat", "Line"]
        )
        st.caption(
            "Historical research is available in View breakdown. "
            "Model recommendations and photos are pending."
        )

        total_pages = max(1, (len(cards) + 11) // 12)
        page = st.selectbox(
            "Page",
            range(1, total_pages + 1),
            format_func=lambda number: f"{number} of {total_pages}",
        )
        visible = cards.iloc[(page - 1) * 12:page * 12]
        if visible.empty:
            st.info("No decoded lines match these filters.")

        columns = st.columns(3)
        for index, (_, card) in enumerate(visible.iterrows()):
            card_id = "|".join(str(card[field]) for field in card_keys)
            with columns[index % 3]:
                with st.container(border=True):
                    st.subheader(str(card["Player"]))
                    st.caption(str(card["Game"]))
                    st.write(f"**{card['Stat']} · {card['Line']:g}**")
                    st.caption(
                        f"{str(card['Platform']).upper()} · "
                        f"{card['Line Type']} · Period: {card['Period']}"
                    )
                    st.write("**Model pick:** Pending")
                    st.write("**Projection:** Unavailable")
                    if st.button(
                        "View breakdown",
                        key=f"breakdown_{card_id}",
                        use_container_width=True,
                    ):
                        st.session_state["selected_prop_card"] = (
                            card.to_dict()
                        )

        selected_card = st.session_state.get("selected_prop_card")
        if selected_card is not None:
            st.divider()
            with st.container(border=True):
                st.subheader(
                    f"{selected_card['Player']} — Player breakdown"
                )
                st.caption(str(selected_card["Game"]))
                st.write(
                    f"**{str(selected_card['Platform']).upper()} · "
                    f"{selected_card['Stat']} · "
                    f"{selected_card['Line']:g}**"
                )
                st.caption(
                    "Historical results are shown below. "
                    "Model projections and pick explanations "
                    "are not connected yet."
                )
                render_card_history(selected_card)

        with st.expander("View raw market data"):
            st.dataframe(
                filtered.drop(columns=["Stat Key", "Period"]),
                use_container_width=True,
                hide_index=True,
            )

        with st.expander("Grouped threshold inventory"):
            grouped = (
                df.groupby(group_keys + ["Game", "Stat"], dropna=False)
                .agg(
                    Thresholds=(
                        "Line",
                        lambda values: ", ".join(
                            str(value) for value in sorted(
                                set(values.dropna())
                            )
                        ),
                    ),
                    Threshold_Count=("Line", "nunique"),
                    Active_Outcomes=("Player", "size"),
                    Explicit_Tiers=(
                        "Line Type",
                        lambda values: ", ".join(sorted(set(values))),
                    ),
                )
                .reset_index()
            )
            st.dataframe(
                grouped, use_container_width=True, hide_index=True
            )

        st.download_button(
            "Download decoded inventory CSV",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name="nba_prop_inventory_v4.csv",
            mime="text/csv",
        )
        st.caption(
            "Zero or missing handicaps remain flagged for review. "
            "Multiple thresholds do not establish "
            "Standard/Goblin/Demon classification. "
            "Market data is not a model recommendation."
        )


# ---------------- HISTORICAL DATA PANEL ----------------

st.divider()
st.subheader("NBA historical stats")
st.info(
    "First load the discovery batch. Then open a player breakdown "
    "to load current and previous season history. "
    "If no board has been scanned, the Scan button loads odds. "
    "Saved ESPN responses use a local SQLite cache. Streamlit "
    "can erase local files on redeploy; external durable storage "
    "is still pending."
)

history_season = st.selectbox(
    "Historical season",
    ["2026-27", "2025-26"],
    key="history_season",
)

if st.button("LOAD NBA DISCOVERY BATCH"):
    try:
        with st.spinner("Requesting real NBA player game logs..."):
            history = load_nba_player_logs(history_season)
        st.session_state["nba_history"] = history
        st.session_state["nba_history_season"] = history_season
    except Exception as error:
        st.session_state.pop("nba_history", None)
        st.session_state.pop("nba_history_season", None)
        st.error("NBA historical connection failed.")
        st.code(
            f"{type(error).__name__}: {error}", language="text"
        )
        st.caption("No sample data was substituted.")

if (
    "nba_history" in st.session_state
    and st.session_state.get("nba_history_season") == history_season
):
    history = st.session_state["nba_history"]
    logs = history["logs"]
    st.success(
        f"Loaded {len(logs):,} player-game records for {history_season}."
    )
    st.caption(
        "Source: ESPN · Seven-calendar-day discovery batch · "
        f"Regular season · Latest game returned: "
        f"{logs['GAME_DATE'].max():%Y-%m-%d} · "
        f"Retrieved at (UTC): {history['retrieved_at']} · Cache: 1 hour"
    )
    st.dataframe(
        logs[
            [
                "PLAYER_NAME", "GAME_DATE", "MATCHUP", "MIN",
                "PTS", "REB", "AST", "STL", "BLK", "TOV", "FG3M",
            ]
        ].head(30),
        use_container_width=True,
        hide_index=True,
    )
    st.caption(
        "Showing 30 discovery records as a preview. "
        "Open a player breakdown to load deeper history."
    )

with st.expander("What is connected and what remains"):
    st.write(
        "Connected: NBA odds scanner, market decoding, player cards, "
        "filters, historical research for supported markets, "
        "H2H averages, static game charts, team scoring comparisons, "
        "manual ESPN roster and injury checks, grouped thresholds "
        "and CSV download."
    )
    st.write(
        "Unmatched players and unsupported markets remain unavailable. "
        "Injury checks are not confirmed starting lineups."
    )
    st.write(
        "Pending: photos, pace and efficiency rankings, "
        "defense versus position, confirmed lineups, "
        "P1/P2 projections, NFL, fantasy scoring mappings, "
        "DraftKings lineup builders, saved picks, ROI audits "
        "and external durable storage."
    )

if HISTORY_DB.exists():
    st.download_button(
        "Back up historical response cache",
        data=HISTORY_DB.read_bytes(),
        file_name="sports_lab_history.sqlite3",
        mime="application/vnd.sqlite3",
    )
