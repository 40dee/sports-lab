"""Sports Lab: real NBA odds inventory and historical connection."""

from datetime import datetime, timedelta, timezone
import re

import pandas as pd
import requests
import streamlit as st


st.set_page_config(
    page_title="Elite Individual Analytics Lab",
    page_icon="🎯",
    layout="wide",
)

BASE = "https://api.oddspapi.io/v4"
BOOKS = ("prizepicks", "kalshi", "draftkings")


# ---------------- ODDS CONNECTION ----------------

def api_get(path, params):
    key = st.secrets.get("ODDS_API_KEY", "")
    if not key:
        raise ValueError("Set ODDS_API_KEY in Streamlit Secrets.")

    try:
        response = requests.get(
            BASE + path,
            params={**params, "apiKey": key},
            timeout=30,
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
            name = str(
                option.get("outcomeName", "")
            ).strip().lower()
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
            line = (
                raw_line
                if raw_line is not None and raw_line > 0
                else None
            )

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
                        "Decoded": (
                            line is not None and direction is not None
                        ),
                    })

        summaries.append({
            "Game": matchup,
            "Platform": book,
            "Returned Markets": len(markets),
            "Active Named Prop Outcomes": count,
            "Bookmaker Active Flag": book_data.get("bookmakerIsActive"),
        })

    return rows, summaries


# ---------------- HISTORICAL CONNECTION ----------------

@st.cache_data(ttl=3600, show_spinner=False)
def load_nba_player_logs(season):
    from nba_api.stats.endpoints import leaguegamelog

    frames = leaguegamelog.LeagueGameLog(
        season=season,
        season_type_all_star="Regular Season",
        player_or_team_abbreviation="P",
        timeout=30,
    ).get_data_frames()

    if not frames:
        raise ValueError("NBA Stats returned no tables.")

    logs = frames[0]
    required = {
        "PLAYER_ID", "PLAYER_NAME", "GAME_ID", "GAME_DATE",
        "MATCHUP", "MIN", "PTS", "REB", "AST",
        "STL", "BLK", "TOV", "FG3M",
    }

    missing = required - set(logs.columns)
    if missing:
        raise ValueError(
            "NBA Stats response is missing: " + ", ".join(sorted(missing))
        )
    if logs.empty:
        raise ValueError(f"No player game logs returned for {season}.")

    logs = logs.copy()
    logs["GAME_DATE"] = pd.to_datetime(
        logs["GAME_DATE"], errors="coerce"
    )
    if logs["GAME_DATE"].isna().any():
        raise ValueError("Some NBA game dates could not be read.")

    logs = logs.drop_duplicates(["PLAYER_ID", "GAME_ID"])

    return {
        "logs": logs.sort_values("GAME_DATE", ascending=False),
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------- APP HEADER ----------------

st.title("🎯 Elite Individual Analytics Lab")
st.caption("Real NBA market inventory · Historical data connection")

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

st.sidebar.info("Player-card history: not connected")
st.sidebar.info("P1/P2 projections: not connected")
st.sidebar.info("ROI tracking: not connected")


# ---------------- NBA SCANNER ----------------

st.subheader("🏀 NBA Pregame Scanner + Line Inventory")

max_games = st.slider("Games to scan", 1, 5, 3)
st.caption(
    f"Fresh scan uses up to {max_games + 2} API calls. "
    "Odds are cached for 30 minutes."
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
                st.warning("No upcoming NBA games returned for the next three days.")
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
            "Fixture ID", "Platform", "Player",
            "Stat Key", "Period",
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
            df.copy()
            if platform == "All"
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
            "Find a player",
            placeholder="Type a player's name...",
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
            "Fixture ID", "Platform", "Player ID", "Stat Key",
            "Period", "Line", "Line Type",
        ]
        cards = cards.drop_duplicates(card_keys).sort_values(
            ["Player", "Stat", "Line"]
        )

        st.caption(
            "Market lines only. Photos, player-card history and "
            "model recommendations are not connected yet."
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
            with columns[index % 3]:
                with st.container(border=True):
                    st.subheader(str(card["Player"]))
                    st.caption(str(card["Game"]))
                    st.write(f"**{card['Stat']} · {card['Line']:g}**")
                    st.caption(
                        f"{card['Platform']} · {card['Line Type']} "
                        f"· Period: {card['Period']}"
                    )
                    st.write("**Model pick:** Pending")
                    st.write("**Projection:** Unavailable")
                    with st.expander("Matchup & history"):
                        st.write("Defense matchup: Unavailable")
                        st.write("Head-to-head: Unavailable")
                        st.write("Last 15: Unavailable")
                        st.write("Home average: Unavailable")
                        st.write("Away average: Unavailable")

        with st.expander("View raw market data"):
            st.dataframe(
                filtered.drop(columns=["Stat Key", "Period"]),
                use_container_width=True,
                hide_index=True,
            )

        with st.expander("Grouped threshold inventory"):
            grouped = (
                df.groupby(
                    group_keys + ["Game", "Stat"],
                    dropna=False,
                )
                .agg(
                    Thresholds=(
                        "Line",
                        lambda values: ", ".join(
                            str(value)
                            for value in sorted(set(values.dropna()))
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
                grouped,
                use_container_width=True,
                hide_index=True,
            )

        st.download_button(
            "Download decoded inventory CSV",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name="nba_prop_inventory_v4.csv",
            mime="text/csv",
        )

        st.caption(
            "Zero or missing handicaps remain flagged for review. "
            "Multiple thresholds do not establish Standard/Goblin/Demon "
            "classification. Market data is not a model recommendation."
        )


# ---------------- HISTORICAL DATA PANEL ----------------

st.divider()
st.subheader("NBA historical stats")

history_season = st.selectbox(
    "Historical season",
    ["2026-27", "2025-26"],
    key="history_season",
)

if st.button("CONNECT NBA GAME LOGS"):
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
            f"{type(error).__name__}: {error}",
            language="text",
        )
        st.caption("No sample data was substituted.")

if (
    "nba_history" in st.session_state
    and st.session_state.get("nba_history_season") == history_season
):
    history = st.session_state["nba_history"]
    logs = history["logs"]

    st.success(
        f"Loaded {len(logs):,} player-game records "
        f"for {history_season}."
    )
    st.caption(
        f"Source: NBA Stats · Regular season · "
        f"Latest game returned: {logs['GAME_DATE'].max():%Y-%m-%d} · "
        f"Retrieved at (UTC): {history['retrieved_at']} · "
        f"Cache: 1 hour"
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
        "Showing 30 records as a preview. All returned records "
        "are held in the current session. Card integration is pending."
    )


with st.expander("What is connected and what remains"):
    st.write(
        "Connected: NBA odds scanner, market decoding, player cards, "
        "filters, grouped thresholds and CSV download."
    )
    st.write(
        "Historical connection: available through CONNECT NBA GAME LOGS. "
        "A successful response is required before using these stats."
    )
    st.write(
        "Pending: photos, historical stats inside player cards, "
        "defensive rankings, injuries, P1/P2 projections, "
        "NFL, DraftKings lineup builders, saved picks and ROI audits."
    )
