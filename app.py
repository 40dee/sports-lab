
"""Elite Individual Analytics Lab — consolidated NBA odds inventory V4.

Requires Streamlit secret ODDS_API_KEY. Data comes from OddsPAPI; no fabricated projections.
"""
from datetime import datetime, timedelta, timezone
import re
import requests
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Elite Individual Analytics Lab", page_icon="🎯", layout="wide")
st.title("🎯 Elite Individual Analytics Lab")
st.caption("Pregame NBA player-prop inventory • OddsPAPI • No simulated recommendations")

BASE = "https://api.oddspapi.io/v4"
BOOKS = ("prizepicks", "kalshi", "draftkings")


def api_get(path, params):
    key = st.secrets.get("ODDS_API_KEY", "")
    if not key:
        raise ValueError("Set ODDS_API_KEY in Streamlit Secrets first.")
    try:
        response = requests.get(BASE + path, params={**params, "apiKey": key}, timeout=30)
    except requests.RequestException:
        raise ValueError("Unable to connect to OddsPAPI.") from None
    if response.status_code != 200:
        raise ValueError(f"OddsPAPI {path}: HTTP {response.status_code}.")
    try:
        return response.json()
    except ValueError:
        raise ValueError(f"OddsPAPI {path} returned invalid JSON.") from None


@st.cache_data(ttl=1800, show_spinner=False)
def upcoming_nba():
    start = datetime.now(timezone.utc)
    end = start + timedelta(days=3)
    data = api_get("/fixtures", {
        "sportId": 11,
        "from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "statusId": 0,
    })
    if not isinstance(data, list):
        raise ValueError("Unexpected fixtures response structure.")
    return sorted((g for g in data if isinstance(g, dict) and g.get("fixtureId") and
                   ("nba" in str(g.get("tournamentName", "")).lower() or
                    str(g.get("tournamentSlug", "")).lower() == "nba")),
                  key=lambda g: str(g.get("startTime", "")))


@st.cache_data(ttl=1800, show_spinner=False)
def market_catalog():
    data = api_get("/markets", {"language": "en"})
    if isinstance(data, dict):
        data = data.get("markets", data.get("data", []))
    if not isinstance(data, list):
        raise ValueError("Unexpected market catalog structure.")
    return {str(m["marketId"]): m for m in data
            if isinstance(m, dict) and m.get("marketId") is not None and
            str(m.get("sportId")) == "11"}


@st.cache_data(ttl=1800, show_spinner=False)
def game_odds(fixture_id):
    return api_get("/odds", {"fixtureId": fixture_id,
                              "bookmakers": ",".join(BOOKS),
                              "language": "en", "verbosity": 3})


def numeric_line(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        num = float(value)
        return num if 0 <= num <= 1000 else None
    except (ValueError, TypeError):
        return None


def outcome_direction(definition, outcome_id):
    options = definition.get("outcomes", [])
    if not isinstance(options, list):
        return None
    for option in options:
        if isinstance(option, dict) and str(option.get("outcomeId")) == str(outcome_id):
            name = str(option.get("outcomeName", "")).strip().lower()
            return {"over": "MORE", "under": "LESS"}.get(name)
    return None


def explicit_tier(*records):
    """Only explicit tier text counts; mainLine is not a tier classifier."""
    fields = ("lineType", "tier", "label", "badge", "projectionType")
    values = [str(record[field]).lower() for record in records if isinstance(record, dict)
              for field in fields if record.get(field) is not None]
    text = " ".join(values)
    for token, result in (("goblin", "Goblin"), ("demon", "Demon"),
                          ("standard", "Standard"), ("regular", "Standard")):
        if re.search(r"\b" + token + r"\b", text):
            return result
    return "Unverified"


def decode_game(game, odds, catalog):
    fixture_id = str(game["fixtureId"])
    matchup = f"{game.get('participant1Name', '?')} vs {game.get('participant2Name', '?')}"
    books = odds.get("bookmakerOdds", {}) if isinstance(odds, dict) else {}
    books = books if isinstance(books, dict) else {}
    rows, summary, seen = [], [], set()
    for book in BOOKS:
        book_data = books.get(book, {})
        book_data = book_data if isinstance(book_data, dict) else {}
        markets = book_data.get("markets", {})
        markets = markets if isinstance(markets, dict) else {}
        eligible_count = 0
        for market_id, market in markets.items():
            if not isinstance(market, dict):
                continue
            definition = catalog.get(str(market_id), {})
            if definition.get("playerProp") is not True or market.get("marketActive") is not True:
                continue
            line = numeric_line(definition.get("handicap"))
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
                    if not isinstance(player, dict) or player.get("active") is not True:
                        continue
                    name = player.get("playerName")
                    if not isinstance(name, str) or not name.strip():
                        continue
                    key = (fixture_id, book, str(market_id), str(outcome_id), str(player_id))
                    if key in seen:
                        continue
                    seen.add(key)
                    eligible_count += 1
                    tier = explicit_tier(player, outcome, market, definition)
                    rows.append({
                        "Game": matchup, "Fixture ID": fixture_id,
                        "Start (UTC)": game.get("startTime"),
                        "Platform": book, "Player": name.strip(),
                        "Stat": definition.get("marketName", "Unknown"),
                        "Stat Key": definition.get("marketType", "Unknown"),
                        "Period": definition.get("period", "Unknown"),
                        "Line": line, "Direction": direction,
                        "Line Type": tier,
                        "Odds": player.get("priceAmerican"),
                        "Market ID": str(market_id), "Outcome ID": str(outcome_id),
                        "Player ID": str(player_id),
                        "mainLine Flag": player.get("mainLine", player.get("mainline")),
                        "Decoded": line is not None and direction is not None,
                    })
        summary.append({"Game": matchup, "Platform": book,
                        "Returned Markets": len(markets),
                        "Active Named Prop Outcomes": eligible_count,
                        "Bookmaker Active Flag": book_data.get("bookmakerIsActive")})
    return rows, summary


if st.secrets.get("ODDS_API_KEY", ""):
    st.sidebar.success("ODDS_API_KEY configured")
else:
    st.sidebar.warning("ODDS_API_KEY missing")
st.sidebar.success("ODDS_API_KEY configured") if st.secrets.get
st.sidebar.info("Historical box scores: not connected")
st.sidebar.info("P1/P2 projections: not connected")
st.sidebar.info("ROI tracking: not connected")

st.subheader("🏀 NBA Pregame Scanner + V4 Line Inventory")
max_games = st.slider("Games to scan", 1, 5, 3)
st.caption(f"Fresh scan uses up to {max_games + 2} API calls (fixtures + catalog + each game); 30-minute cache reduces repeat calls.")

if st.button("🔍 SCAN AND DECODE NBA LINES", use_container_width=True):
    try:
        with st.spinner("Loading fixtures, market definitions and active outcomes..."):
            games = upcoming_nba()[:max_games]
            if not games:
                st.warning("No upcoming NBA games returned for the next three days.")
            else:
                catalog = market_catalog()
                all_rows, summaries, errors = [], [], []
                for game in games:
                    try:
                        rows, summary = decode_game(game, game_odds(str(game["fixtureId"])), catalog)
                        all_rows.extend(rows)
                        summaries.extend(summary)
                    except ValueError as error:
                        errors.append(f"{game.get('participant1Name', '?')} vs {game.get('participant2Name', '?')}: {error}")
                st.session_state["v4_rows"] = all_rows
                st.session_state["v4_summary"] = summaries
                st.session_state["v4_errors"] = errors
    except ValueError as error:
        st.error(str(error))

if "v4_rows" in st.session_state:
    rows = st.session_state["v4_rows"]
    summary = st.session_state["v4_summary"]
    errors = st.session_state["v4_errors"]
    if errors:
        for message in errors:
            st.warning(message)
    if summary:
        st.subheader("Platform coverage")
        st.dataframe(pd.DataFrame(summary), use_container_width=True, hide_index=True)
    if not rows:
        st.info("No active, named NBA player-prop outcomes were decoded in the checked games.")
    else:
        df = pd.DataFrame(rows)
        # One group is one game/platform/player/stat/period, irrespective of direction.
        group_keys = ["Fixture ID", "Platform", "Player", "Stat Key", "Period"]
        line_counts = df.groupby(group_keys, dropna=False)["Line"].transform("nunique")
        df["Distinct Thresholds"] = line_counts.astype(int)
        df["Multiple Thresholds"] = df["Distinct Thresholds"] > 1
        decoded_count = int(df["Decoded"].sum())
        a, b, c = st.columns(3)
        a.metric("Active prop outcomes", len(df))
        b.metric("Line + direction decoded", decoded_count)
        c.metric("Tier explicitly verified", int((df["Line Type"] != "Unverified").sum()))
        platform = st.selectbox("Platform", ["All"] + list(BOOKS))
        show = st.radio("View", ["All active props", "Decoded only", "Multiple thresholds", "Unverified line types"], horizontal=True)
        filtered = df if platform == "All" else df[df["Platform"] == platform]
        if show == "Decoded only":
            filtered = filtered[filtered["Decoded"]]
        elif show == "Multiple thresholds":
            filtered = filtered[filtered["Multiple Thresholds"]]
        elif show == "Unverified line types":
            filtered = filtered[filtered["Line Type"] == "Unverified"]
        st.dataframe(filtered.drop(columns=["Stat Key", "Period"]), use_container_width=True, hide_index=True)
        st.subheader("Grouped threshold inventory")
        grouped = (df.groupby(group_keys + ["Game", "Stat"], dropna=False)
                   .agg(Thresholds=("Line", lambda s: ", ".join(str(x) for x in sorted(set(s.dropna())))),
                        Threshold_Count=("Line", "nunique"),
                        Active_Outcomes=("Player", "size"),
                        Explicit_Tiers=("Line Type", lambda s: ", ".join(sorted(set(s)))))
                   .reset_index())
        st.dataframe(grouped[["Game", "Platform", "Player", "Stat", "Thresholds", "Threshold_Count", "Active_Outcomes", "Explicit_Tiers"]],
                     use_container_width=True, hide_index=True)
        st.download_button("Download decoded inventory CSV", data=df.to_csv(index=False).encode("utf-8"),
                           file_name="nba_prop_inventory_v4.csv", mime="text/csv")
        st.caption("Multiple thresholds indicate alternatives, not which one is Standard/Goblin/Demon. "
                   "The mainLine flag is retained for inspection only. Active market data is not a betting recommendation.")

with st.expander("What is connected and what remains"):
    st.write("Connected: upcoming NBA fixtures, catalog-confirmed player props, market handicap, Over/Under mapping, active selection filtering, grouped thresholds and duplicate-record suppression.")
    st.write("Not connected: authoritative tier classification, last-15 player box scores, head-to-head matchups, defensive rankings, injuries, P1/P2 projections, NFL/MLB, and ROI audit.")
