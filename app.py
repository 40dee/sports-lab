
import streamlit as st
import pandas as pd

st.set_page_config(
    page_title="Elite Individual Analytics Lab",
    page_icon="🎯",
    layout="wide"
)

st.title("🎯 Elite Individual Analytics Lab")
st.subheader("Autonomous Matchup-Adjusted Player Prop Scanner")
st.divider()

st.sidebar.header("📡 Core System Status")

api_key = st.secrets.get("ODDS_API_KEY", "")
if api_key:
    st.sidebar.success("API key configured")
else:
    st.sidebar.warning("API key not configured")

st.sidebar.info("Defensive rankings: Not connected")
st.sidebar.info("PrizePicks board: Not connected")

if "show_board" not in st.session_state:
    st.session_state.show_board = False

if st.button(
    "⚡ EXECUTE SYSTEM-WIDE MATCHUP SYNC",
    use_container_width=True
):
    st.session_state.show_board = True

if st.session_state.show_board:
    st.warning(
        "DEMO MODE: These are sample predictions, "
        "not verified current picks."
    )

    m1, m2, m3 = st.columns(3)
    m1.metric("Highest Sample Edge", "+38.1 Yds")
    m2.metric("Defensive Rankings", "Demo")
    m3.metric("Injury Verification", "Not connected")

    st.markdown("### 🏆 Individual Player Prop Board")

    master_data = pd.DataFrame([
        {
            "Player": "Bijan Robinson",
            "Sport": "NFL",
            "Opponent": "Dallas Cowboys",
            "Line": "74.5 Rush Yds",
            "Season Avg": "100.6 Yds",
            "Defense Rank": "29th (Sample)",
            "Edge": "+38.1 Yds",
            "Recommendation": "MORE (Demo)"
        },
        {
            "Player": "CeeDee Lamb",
            "Sport": "NFL",
            "Opponent": "Atlanta Falcons",
            "Line": "88.5 Rec Yds",
            "Season Avg": "93.0 Yds",
            "Defense Rank": "8th (Sample)",
            "Edge": "-14.0 Yds",
            "Recommendation": "LESS (Demo)"
        },
        {
            "Player": "Paul Reed",
            "Sport": "NBA",
            "Opponent": "Brooklyn Nets",
            "Line": "15.5 Points",
            "Season Avg": "16.2 Pts",
            "Defense Rank": "14th (Sample)",
            "Edge": "+0.7 Pts",
            "Recommendation": "PASS (Demo)"
        }
    ])

    st.dataframe(
        master_data,
        use_container_width=True,
        hide_index=True
    )

    st.divider()
    st.markdown("### 🔍 Individual Player Profile")

    selected_player = st.selectbox(
        "Select a player:",
        master_data["Player"].tolist()
    )

    profiles = {
        "Bijan Robinson": {
            "baseline": 100.6,
            "projection": 112.6,
            "line": 74.5,
            "rank": "29th",
            "market": "Rushing Yards",
            "direction": "MORE"
        },
        "CeeDee Lamb": {
            "baseline": 93.0,
            "projection": 74.5,
            "line": 88.5,
            "rank": "8th",
            "market": "Receiving Yards",
            "direction": "LESS"
        },
        "Paul Reed": {
            "baseline": 16.2,
            "projection": 16.2,
            "line": 15.5,
            "rank": "14th",
            "market": "Points",
            "direction": "PASS"
        }
    }

    p = profiles[selected_player]

    st.subheader(selected_player)

    a, b, c = st.columns(3)
    a.metric("Sample Baseline", p["baseline"])
    b.metric("Sample Projection", p["projection"])
    c.metric("Sample Opponent Rank", p["rank"])

    st.write("Market:", p["market"])
    st.write("Sample Line:", p["line"])
    st.write("Demo Direction:", p["direction"])

    st.info(
        "These values are placeholders. Live statistics, "
        "matchup adjustments, injury verification, "
        "and actual projections will be connected next."
    )

else:
    st.info(
        "Welcome to Elite Individual Analytics Lab. "
        "Click the button above to open the demonstration board."
    )

st.divider()
st.subheader("🔌 OddsPAPI Connection Test")

if st.button("TEST ODDS API CONNECTION"):
    import requests

    key = st.secrets.get("ODDS_API_KEY", "")

    if not key:
        st.error("API key missing from Streamlit Secrets.")
    else:
        try:
            response = requests.get(
                "https://api.oddspapi.io/v4/account",
                params={"apiKey": key},
                timeout=15
            )

            if response.status_code == 200:
                st.success("OddsPAPI connection successful!")
                st.info("Your API authentication is working.")
            else:
                st.error(
                    f"Connection failed. HTTP status: "
                    f"{response.status_code}"
                )

        except requests.RequestException:
            st.error("Unable to connect to OddsPAPI.")

from datetime import datetime, timedelta, timezone
import requests

st.divider()
st.subheader("🏀 Live NBA Pregame Scanner")

if st.button("🏀 FETCH REAL NBA GAMES", use_container_width=True):
    key = st.secrets.get("ODDS_API_KEY", "")

    if not key:
        st.error("OddsPAPI key is missing.")
    else:
        with st.spinner("Retrieving NBA schedule..."):
            try:
                start = datetime.now(timezone.utc)
                end = start + timedelta(days=3)

                response = requests.get(
                    "https://api.oddspapi.io/v4/fixtures",
                    params={
                        "apiKey": key,
                        "sportId": 11,
                        "from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "to": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "statusId": 0
                    },
                    timeout=20
                )

                response.raise_for_status()
                fixtures = response.json()

                if not isinstance(fixtures, list):
                    st.error("Unexpected API response format.")
                else:
                    nba = [
                        game for game in fixtures
                        if (
                            "nba" in str(
                                game.get("tournamentName", "")
                            ).lower()
                            or game.get("tournamentSlug", "").lower()
                            == "nba"
                        )
                    ]

                    if nba:
                        rows = []
                        for game in nba:
                            rows.append({
                                "Game": (
                                    f"{game.get('participant1Name', '?')}"
                                    " vs "
                                    f"{game.get('participant2Name', '?')}"
                                ),
                                "Start (UTC)": game.get("startTime"),
                                "Odds Available": game.get("hasOdds"),
                                "Fixture ID": game.get("fixtureId")
                            })

                        st.success(f"Found {len(rows)} NBA games.")
                        st.dataframe(
                            pd.DataFrame(rows),
                            use_container_width=True,
                            hide_index=True
                        )
                    else:
                        st.warning(
                            "No NBA games returned for this window."
                        )

            except requests.HTTPError:
                st.error(
                    f"OddsPAPI returned HTTP {response.status_code}."
                )
            except requests.RequestException:
                st.error("Unable to retrieve the NBA schedule.")
            except ValueError:
                st.error("OddsPAPI returned invalid JSON.")


st.divider()
st.subheader("🏀 NBA Market Discovery")

st.caption("Inspect available markets for one NBA game.")

fixture_id = st.text_input(
    "NBA Fixture ID",
    value="id1100013273719476",
    help="Enter a fixture ID from your NBA game table."
)

if st.button("🔎 DISCOVER NBA MARKETS"):
    key = st.secrets.get("ODDS_API_KEY", "")

    if not key:
        st.error("API key missing.")
    elif not fixture_id.strip():
        st.error("Enter a fixture ID.")
    else:
        import requests

        with st.spinner("Checking available markets..."):
            try:
                response = requests.get(
                    "https://api.oddspapi.io/v4/odds",
                    params={
                        "apiKey": key,
                        "fixtureId": fixture_id.strip(),
                        "verbosity": 3
                    },
                    timeout=25
                )
                response.raise_for_status()
                data = response.json()

                bookmaker_odds = data.get(
                    "bookmakerOdds", {}
                )

                rows = []

                for bookmaker, details in bookmaker_odds.items():
                    markets = details.get("markets", {})

                    for market_id, market in markets.items():
                        rows.append({
                            "Bookmaker": bookmaker,
                            "Market ID": str(market_id),
                            "Outcome Types": len(
                                market.get("outcomes", {})
                            ),
                            "Bookmaker Market ID": str(
                                market.get(
                                    "bookmakerMarketId", ""
                                )
                            )
                        })

                if rows:
                    import pandas as pd

                    df = pd.DataFrame(rows)

                    st.success(
                        f"Found {len(df)} bookmaker-market entries."
                    )

                    st.dataframe(
                        df,
                        use_container_width=True,
                        hide_index=True
                    )

                    st.write(
                        "Bookmakers:",
                        df["Bookmaker"].nunique()
                    )
                    st.write(
                        "Unique Market IDs:",
                        df["Market ID"].nunique()
                    )

                    st.info(
                        "Market IDs are not yet decoded "
                        "into market names."
                    )
                else:
                    st.warning(
                        "No bookmaker markets were returned "
                        "for this fixture."
                    )

            except requests.HTTPError:
                st.error(
                    f"API error: HTTP {response.status_code}"
                )
            except requests.RequestException:
                st.error("Could not retrieve market data.")
            except (ValueError, AttributeError):
                st.error("Unexpected API response format.")

st.divider()
st.subheader("🏀 NBA Market Decoder")

if st.button("DECODE NBA MARKETS"):
    key = st.secrets.get("ODDS_API_KEY", "")

    if not key:
        st.error("API key missing.")
    else:
        try:
            response = requests.get(
                "https://api.oddspapi.io/v4/markets",
                params={
                    "apiKey": key,
                    "language": "en"
                },
                timeout=30
            )
            response.raise_for_status()
            markets = response.json()

            nba_markets = [
                market for market in markets
                if str(market.get("sportId")) == "11"
            ]

            rows = []
            for market in nba_markets:
                rows.append({
                    "Market ID": market.get("marketId"),
                    "Market Name": market.get("marketName"),
                    "Player Prop": market.get("playerProp"),
                    "Type": market.get("marketType"),
                    "Period": market.get("period")
                })

            st.success(
                f"Decoded {len(rows)} basketball markets!"
            )

            if rows:
                df = pd.DataFrame(rows)
                st.dataframe(
                    df,
                    use_container_width=True,
                    hide_index=True
                )

                st.subheader("Player Props Only")
                st.dataframe(
                    df[df["Player Prop"] == True],
                    use_container_width=True,
                    hide_index=True
                )
            else:
                st.warning("No basketball markets returned.")

        except requests.RequestException as error:
            st.error(f"Market request failed: {error}")
        except ValueError:
            st.error("Invalid API response.")

st.divider()
st.subheader("🏀 NBA Player Market Finder")

if st.button("FIND NBA PROP CATEGORIES"):
    try:
        response = requests.get(
            "https://api.oddspapi.io/v4/markets",
            params={
                "apiKey": st.secrets["ODDS_API_KEY"],
                "language": "en"
            },
            timeout=30
        )
        response.raise_for_status()
        markets = response.json()

        props = [
            m for m in markets
            if str(m.get("sportId")) == "11"
            and m.get("playerProp") is True
        ]

        categories = {}

        for market in props:
            name = market.get("marketName", "Unknown")
            market_type = market.get("marketType", "Unknown")
            category = (name, market_type)
            categories[category] = categories.get(category, 0) + 1

        rows = [
            {
                "Market Name": name,
                "Market Type": market_type,
                "Market IDs": count
            }
            for (name, market_type), count in categories.items()
        ]

        if rows:
            df = pd.DataFrame(rows)
            search = st.text_input(
                "Search categories",
                placeholder="points, rebounds, assists, fantasy"
            )
            if search:
                df = df[
                    df["Market Name"].str.contains(
                        search, case=False, na=False
                    )
                ]
            st.dataframe(df, hide_index=True, use_container_width=True)
            st.success(f"Found {len(categories)} player-prop categories.")
        else:
            st.warning("No player-prop categories found.")

    except requests.RequestException as error:
        st.error(f"API request failed: {error}")
    except (ValueError, KeyError, TypeError) as error:
        st.error(f"Unable to process markets: {error}")

st.divider()
st.subheader("🎯 Pick'em App Availability Check")

if st.button("VERIFY PICK'EM APPS"):
    import requests

    platforms = {
        "PrizePicks": ["prizepicks"],
        "Sleeper": ["sleeper"],
        "Underdog": ["underdog"],
        "ParlayPlay": ["parlayplay", "parlay-play"],
        "Kalshi": ["kalshi"],
        "DraftKings Pick6": ["pick6", "pick-6"]
    }

    try:
        response = requests.get(
            "https://api.oddspapi.io/v4/bookmakers",
            params={
                "apiKey": st.secrets["ODDS_API_KEY"]
            },
            timeout=30
        )
        response.raise_for_status()
        bookmakers = response.json()

        if not isinstance(bookmakers, list):
            st.error("Unexpected bookmaker response.")
        else:
            rows = []

            for platform, keywords in platforms.items():
                matches = [
                    book for book in bookmakers
                    if any(
                        word in (
                            str(book.get("bookmakerName", "")) +
                            " " +
                            str(book.get("slug", ""))
                        ).lower().replace(" ", "").replace("-", "")
                        for word in [
                            k.replace("-", "") for k in keywords
                        ]
                    )
                ]

                rows.append({
                    "Platform": platform,
                    "In Catalog": "YES" if matches else "NOT FOUND",
                    "Bookmaker Slugs": ", ".join(
                        str(m.get("slug", "")) for m in matches
                    ) if matches else "—",
                    "Pregame Props": "Not tested"
                })

            st.dataframe(
                pd.DataFrame(rows),
                hide_index=True,
                use_container_width=True
            )

            st.info(
                "Catalog presence does not guarantee "
                "actual pregame player-prop lines."
            )

    except requests.RequestException as error:
        st.error(f"API request failed: {error}")
    except (KeyError, ValueError, TypeError) as error:
        st.error(f"Unable to process data: {error}")

st.divider()
st.subheader("🎯 PrizePicks & Kalshi Pregame Test")

fixture_id = st.text_input(
    "NBA Fixture ID",
    value="id1100013273719476",
    key="pickem_fixture_id"
)

if st.button("TEST ACTUAL PICK'EM MARKETS"):
    key = st.secrets.get("ODDS_API_KEY", "")

    if not key:
        st.error("OddsPAPI key missing.")
    else:
        try:
            response = requests.get(
                "https://api.oddspapi.io/v4/odds",
                params={
                    "apiKey": key,
                    "fixtureId": fixture_id.strip(),
                    "bookmakers": "prizepicks,kalshi",
                    "language": "en",
                    "verbosity": 3
                },
                timeout=30
            )
            response.raise_for_status()
            data = response.json()

            books = data.get("bookmakerOdds", {})

            rows = []
            for slug in ["prizepicks", "kalshi"]:
                book = books.get(slug, {})
                markets = book.get("markets", {})

                rows.append({
                    "Platform": slug,
                    "Fixture Returned": bool(book),
                    "Market Count": len(markets),
                    "Active": book.get(
                        "bookmakerIsActive", False
                    ),
                    "Suspended": book.get(
                        "suspended", None
                    )
                })

            st.dataframe(
                pd.DataFrame(rows),
                hide_index=True,
                use_container_width=True
            )

            st.write("Fixture:", data.get("fixtureId"))
            st.write("Game:", (
                str(data.get("participant1Name", "")) +
                " vs " +
                str(data.get("participant2Name", ""))
            ))
            st.write("Status:", data.get("statusName"))

            for slug in ["prizepicks", "kalshi"]:
                book = books.get(slug, {})
                markets = book.get("markets", {})

                with st.expander(
                    f"{slug}: Market details"
                ):
                    if markets:
                        st.write(
                            "Market IDs:",
                            list(markets.keys())[:30]
                        )
                        first_id = next(iter(markets))
                        st.json({
                            "marketId": first_id,
                            "market": markets[first_id]
                        })
                    else:
                        st.info(
                            "No markets returned for this fixture."
                        )

        except requests.HTTPError:
            st.error(
                f"OddsPAPI returned HTTP {response.status_code}."
            )
        except requests.RequestException as error:
            st.error(f"Connection error: {error}")
        except (ValueError, TypeError, AttributeError) as error:
            st.error(f"Unexpected response: {error}")

st.divider()
st.subheader("🎯 PrizePicks Player-Prop Decoder")

if st.button("LOAD PRIZEPICKS PLAYER PROPS"):
    import requests
    import pandas as pd

    key = st.secrets.get("ODDS_API_KEY", "")
    fixture = st.session_state.get(
        "pickem_fixture_id",
        "id1100013273719476"
    )

    if not key:
        st.error("OddsPAPI key missing.")
    else:
        try:
            with st.spinner("Loading PrizePicks markets..."):
                odds_response = requests.get(
                    "https://api.oddspapi.io/v4/odds",
                    params={
                        "apiKey": key,
                        "fixtureId": fixture,
                        "bookmakers": "prizepicks",
                        "language": "en",
                        "verbosity": 3
                    },
                    timeout=30
                )
                odds_response.raise_for_status()
                odds_data = odds_response.json()

                market_response = requests.get(
                    "https://api.oddspapi.io/v4/markets",
                    params={
                        "apiKey": key,
                        "language": "en"
                    },
                    timeout=30
                )
                market_response.raise_for_status()
                definitions = market_response.json()

            market_lookup = {
                str(m.get("marketId")): m
                for m in definitions
                if isinstance(m, dict)
            }

            bookmaker = odds_data.get(
                "bookmakerOdds", {}
            ).get("prizepicks", {})

            markets = bookmaker.get("markets", {})
            rows = []

            for market_id, market in markets.items():
                if not isinstance(market, dict):
                    continue

                definition = market_lookup.get(
                    str(market_id), {}
                )

                outcomes = market.get("outcomes", {})

                for outcome_id, outcome in outcomes.items():
                    if not isinstance(outcome, dict):
                        continue

                    players = outcome.get("players", {})

                    for player_id, player in players.items():
                        if not isinstance(player, dict):
                            continue

                        rows.append({
                            "Player": player.get(
                                "playerName", "Unknown"
                            ),
                            "Market": definition.get(
                                "marketName", "Unknown"
                            ),
                            "Market ID": str(market_id),
                            "Outcome ID": str(outcome_id),
                            "Player ID": str(player_id),
                            "Decimal Odds": player.get("price"),
                            "American Odds": player.get(
                                "priceAmerican"
                            ),
                            "Market Active": market.get(
                                "marketActive"
                            ),
                            "Selection Active": player.get(
                                "active"
                            ),
                            "Main Line": player.get(
                                "mainline"
                            ),
                            "Line": "Unverified",
                            "MORE/LESS": "Unverified"
                        })

            if not rows:
                st.warning(
                    "No player outcomes returned "
                    "for this fixture."
                )
            else:
                df = pd.DataFrame(rows)

                st.success(
                    f"Decoded {len(df)} player outcomes."
                )

                st.metric(
                    "Active player outcomes",
                    int(
                        (
                            (df["Market Active"] == True)
                            & (df["Selection Active"] == True)
                        ).sum()
                    )
                )

                active_only = st.checkbox(
                    "Show active selections only",
                    value=True,
                    key="pp_active_only"
                )

                if active_only:
                    df = df[
                        (df["Market Active"] == True)
                        & (df["Selection Active"] == True)
                    ]

                st.dataframe(
                    df,
                    use_container_width=True,
                    hide_index=True
                )

                st.info(
                    "Player names and odds are decoded. "
                    "Lines and MORE/LESS remain unverified "
                    "until outcome metadata is mapped."
                )

        except requests.RequestException as error:
            st.error(f"OddsPAPI request failed: {error}")
        except (ValueError, TypeError, AttributeError) as error:
            st.error(f"Unable to decode response: {error}")

# ============================================
# SPORTS LAB - VERIFIED PICK'EM DECODER
# ============================================

import requests
import pandas as pd
import streamlit as st

st.divider()
st.header("🎯 Verified Pick'em Outcome Inventory")

API_BASE = "https://api.oddspapi.io/v4"


@st.cache_data(ttl=900, show_spinner=False)
def fetch_pickem_data(fixture_id, api_key):
    odds_response = requests.get(
        f"{API_BASE}/odds",
        params={
            "apiKey": api_key,
            "fixtureId": fixture_id,
            "bookmakers": "prizepicks,kalshi",
            "language": "en",
            "verbosity": 3
        },
        timeout=30
    )
    odds_response.raise_for_status()

    markets_response = requests.get(
        f"{API_BASE}/markets",
        params={
            "apiKey": api_key,
            "language": "en"
        },
        timeout=30
    )
    markets_response.raise_for_status()

    return odds_response.json(), markets_response.json()


def decode_pickem_outcomes(odds_data, definitions):
    market_lookup = {
        str(m.get("marketId")): m
        for m in definitions
        if isinstance(m, dict)
    }

    rows = []
    summary = []

    bookmaker_odds = odds_data.get("bookmakerOdds", {})

    for slug in ["prizepicks", "kalshi"]:
        bookmaker = bookmaker_odds.get(slug, {})
        markets = bookmaker.get("markets", {})

        active_count = 0
        inactive_count = 0

        for market_id, market in markets.items():
            if not isinstance(market, dict):
                continue

            definition = market_lookup.get(str(market_id), {})
            market_name = definition.get(
                "marketName", "Unknown"
            )
            market_type = definition.get(
                "marketType", "Unknown"
            )

            outcomes = market.get("outcomes", {})

            for outcome_id, outcome in outcomes.items():
                if not isinstance(outcome, dict):
                    continue

                players = outcome.get("players", {})

                for player_id, player in players.items():
                    if not isinstance(player, dict):
                        continue

                    market_active = (
                        market.get("marketActive") is True
                    )
                    selection_active = (
                        player.get("active") is True
                    )

                    eligible = (
                        market_active and selection_active
                    )

                    if eligible:
                        active_count += 1
                    else:
                        inactive_count += 1

                    rows.append({
                        "Platform": slug.title(),
                        "Player": player.get(
                            "playerName", "Unknown"
                        ),
                        "Market": market_name,
                        "Market Type": market_type,
                        "Market ID": str(market_id),
                        "Outcome ID": str(outcome_id),
                        "Player ID": str(player_id),
                        "Decimal Odds": player.get("price"),
                        "American Odds": player.get(
                            "priceAmerican"
                        ),
                        "Market Active": market_active,
                        "Selection Active": selection_active,
                        "Eligible": eligible,
                        "Line": "Unverified",
                        "Direction": "Unverified",
                        "Line Type": "Unverified"
                    })

        summary.append({
            "Platform": slug.title(),
            "Markets": len(markets),
            "Active Outcomes": active_count,
            "Inactive Outcomes": inactive_count,
            "Total Player Outcomes": (
                active_count + inactive_count
            )
        })

    return pd.DataFrame(rows), pd.DataFrame(summary)


fixture_default = st.session_state.get(
    "pickem_fixture_id",
    "id1100013273719476"
)

fixture_input = st.text_input(
    "Fixture ID",
    value=fixture_default,
    key="verified_pickem_fixture"
)

if st.button(
    "LOAD VERIFIED OUTCOMES",
    use_container_width=True
):
    api_key = st.secrets.get("ODDS_API_KEY", "")

    if not api_key:
        st.error(
            "ODDS_API_KEY is missing from Streamlit Secrets."
        )
    elif not fixture_input.strip():
        st.error("Please enter a fixture ID.")
    else:
        try:
            with st.spinner(
                "Retrieving real player outcomes..."
            ):
                odds_data, definitions = fetch_pickem_data(
                    fixture_input.strip(),
                    api_key
                )

                df, summary_df = decode_pickem_outcomes(
                    odds_data,
                    definitions
                )

            st.subheader("📊 Platform Summary")
            st.dataframe(
                summary_df,
                use_container_width=True,
                hide_index=True
            )

            if df.empty:
                st.warning(
                    "No player outcomes were returned "
                    "for this fixture."
                )
            else:
                total = len(df)
                eligible = int(df["Eligible"].sum())
                inactive = total - eligible

                c1, c2, c3 = st.columns(3)

                c1.metric(
                    "Player Outcomes",
                    total
                )
                c2.metric(
                    "Active Outcomes",
                    eligible
                )
                c3.metric(
                    "Inactive Outcomes",
                    inactive
                )

                st.subheader("🏀 Player Outcomes")

                platform_filter = st.selectbox(
                    "Choose Platform",
                    ["All", "Prizepicks", "Kalshi"]
                )

                active_only = st.checkbox(
                    "Show Active Only",
                    value=True,
                    key="verified_active_only"
                )

                filtered = df.copy()

                if platform_filter != "All":
                    filtered = filtered[
                        filtered["Platform"]
                        == platform_filter
                    ]

                if active_only:
                    filtered = filtered[
                        filtered["Eligible"]
                    ]

                st.dataframe(
                    filtered,
                    use_container_width=True,
                    hide_index=True
                )

                st.caption(
                    "These are real API records. "
                    "Line thresholds, MORE/LESS directions, "
                    "and Goblin/Demon classifications "
                    "remain unverified."
                )

     
        except requests.HTTPError as error:
            status = error.response.status_code

            if status == 403:
                st.error(
                    "OddsPAPI denied this request (403). "
                    "Check API permissions and fixture access."
                )
            else:
                st.error(
                    f"OddsPAPI returned HTTP {status}."
                )

        except requests.RequestException:
            st.error("Unable to connect to OddsPAPI.")

        except (ValueError, TypeError, AttributeError):
            st.error("Unable to decode the API response.")
   
# ==============================================
# SPORTS LAB - AUTOMATIC NBA PREGAME SCANNER
# ==============================================

from datetime import datetime, timedelta, timezone
import requests
import pandas as pd
import streamlit as st

st.divider()
st.header("🏀 Automatic NBA Pregame Scanner")
st.caption(
    "Checks upcoming NBA games for PrizePicks, "
    "Kalshi and DraftKings. No simulated picks."
)

SCAN_BASE = "https://api.oddspapi.io/v4"
SCAN_BOOKS = ["prizepicks", "kalshi", "draftkings"]

def scan_request(endpoint, params):
    key = st.secrets.get("ODDS_API_KEY", "")
    if not key:
        raise ValueError("ODDS_API_KEY is missing.")

    response = requests.get(
        SCAN_BASE + endpoint,
        params={**params, "apiKey": key},
        timeout=30
    )

    if response.status_code != 200:
        raise ValueError(
            f"{endpoint} returned HTTP {response.status_code}"
        )

    return response.json()


@st.cache_data(ttl=1800, show_spinner=False)
def scan_nba_games():
    start = datetime.now(timezone.utc)
    end = start + timedelta(days=3)

    fixtures = scan_request(
        "/fixtures",
        {
            "sportId": 11,
            "from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "statusId": 0,
            "language": "en"
        }
    )

    if not isinstance(fixtures, list):
        raise ValueError("Unexpected fixtures response.")

    nba = [
        game for game in fixtures
        if (
            "nba" in str(
                game.get("tournamentName", "")
            ).lower()
            or str(
                game.get("tournamentSlug", "")
            ).lower() == "nba"
        )
        and game.get("fixtureId")
    ]

    return sorted(
        nba,
        key=lambda game: game.get("startTime", "")
    )


@st.cache_data(ttl=1800, show_spinner=False)
def scan_game_odds(fixture_id):
    return scan_request(
        "/odds",
        {
            "fixtureId": fixture_id,
            "bookmakers": ",".join(SCAN_BOOKS),
            "language": "en",
            "verbosity": 3
        }
    )


def classify_market(market):
    outcomes = market.get("outcomes", {})
    if not isinstance(outcomes, dict):
        return False

    for outcome in outcomes.values():
        if not isinstance(outcome, dict):
            continue

        players = outcome.get("players", {})
        if not isinstance(players, dict):
            continue

        for player in players.values():
            if (
                isinstance(player, dict)
                and player.get("playerName")
            ):
                return True

    return False


max_games = st.slider(
    "Maximum games to check",
    min_value=1,
    max_value=5,
    value=3,
    key="nba_auto_max_games"
)

st.caption(
    "Estimated API calls on a fresh scan: "
    f"up to {max_games + 1}. "
    "Cached results are reused for 30 minutes."
)

if st.button(
    "🔍 SCAN UPCOMING NBA MARKETS",
    use_container_width=True,
    key="nba_auto_scan_button"
):
    try:
        with st.spinner("Scanning upcoming NBA games..."):
            games = scan_nba_games()

            if not games:
                st.warning(
                    "No upcoming NBA fixtures found "
                    "in the next three days."
                )
            else:
                results = []
                market_rows = []

                for game in games[:max_games]:
                    fixture_id = game["fixtureId"]

                    matchup = (
                        str(game.get("participant1Name", "?"))
                        + " vs "
                        + str(game.get("participant2Name", "?"))
                    )

                    try:
                        odds = scan_game_odds(fixture_id)
                        books = odds.get("bookmakerOdds", {})

                        for book in SCAN_BOOKS:
                            data = books.get(book, {})
                            markets = data.get("markets", {})

                            if not isinstance(markets, dict):
                                markets = {}

                            player_markets = 0
                            game_markets = 0
                            active_players = 0

                            for market_id, market in markets.items():
                                if not isinstance(market, dict):
                                    continue

                                is_player = classify_market(market)

                                if is_player:
                                    player_markets += 1
                                else:
                                    game_markets += 1

                                outcomes = market.get(
                                    "outcomes", {}
                                )

                                if not isinstance(outcomes, dict):
                                    continue

                                for outcome_id, outcome in outcomes.items():
                                    if not isinstance(outcome, dict):
                                        continue

                                    players = outcome.get(
                                        "players", {}
                                    )

                                    if not isinstance(players, dict):
                                        continue

                                    for player_id, player in players.items():
                                        if not isinstance(player, dict):
                                            continue

                                        name = player.get("playerName")

                                        if not name:
                                            continue

                                        active = (
                                            market.get("marketActive") is True
                                            and player.get("active") is True
                                        )

                                        if active:
                                            active_players += 1

                                        market_rows.append({
                                            "Game": matchup,
                                            "Platform": book,
                                            "Player": name,
                                            "Market ID": str(market_id),
                                            "Outcome ID": str(outcome_id),
                                            "Player ID": str(player_id),
                                            "Active": active,
                                            "Decimal Odds": player.get("price"),
                                            "American Odds": player.get(
                                                "priceAmerican"
                                            ),
                                            "Line": "Unverified",
                                            "Direction": "Unverified",
                                            "Line Type": "Unverified"
                                        })

                            results.append({
                                "Game": matchup,
                                "Platform": book,
                                "Player Markets": player_markets,
                                "Other Markets": game_markets,
                                "Active Player Outcomes": active_players,
                                "Fixture ID": fixture_id,
                                "Status": "Retrieved"
                            })

                    except (ValueError, requests.RequestException):
                        for book in SCAN_BOOKS:
                            results.append({
                                "Game": matchup,
                                "Platform": book,
                                "Player Markets": None,
                                "Other Markets": None,
                                "Active Player Outcomes": None,
                                "Fixture ID": fixture_id,
                                "Status": "API request failed"
                            })

                st.session_state["nba_auto_results"] = results
                st.session_state["nba_auto_players"] = market_rows

    except (ValueError, requests.RequestException) as error:
        st.error(str(error))


if "nba_auto_results" in st.session_state:
    st.subheader("📊 Market Availability by Game")

    st.dataframe(
        pd.DataFrame(st.session_state["nba_auto_results"]),
        use_container_width=True,
        hide_index=True
    )

    players = pd.DataFrame(
        st.session_state.get("nba_auto_players", [])
    )

    st.subheader("🎯 Retrieved Player Outcomes")

    if players.empty:
        st.info(
            "No named player outcomes were returned "
            "from the checked games."
        )
    else:
        platform = st.selectbox(
            "Platform",
            ["All"] + SCAN_BOOKS,
            key="nba_auto_platform"
        )

        active_only = st.checkbox(
            "Active outcomes only",
            value=True,
            key="nba_auto_active_only"
        )

        if platform != "All":
            players = players[
                players["Platform"] == platform
            ]

        if active_only:
            players = players[
                players["Active"] == True
            ]

        st.metric("Displayed Outcomes", len(players))

        st.dataframe(
            players,
            use_container_width=True,
            hide_index=True
        )

        st.caption(
            "A named player outcome is not necessarily "
            "a verified player-prop line. Thresholds, "
            "MORE/LESS directions, and Goblin/Demon "
            "types still require validation."
        )

# ============================================
# NBA VERIFIED PROP DECODER V2
# ============================================

import re

st.divider()
st.header("🏀 Verified NBA Prop Decoder V2")

@st.cache_data(ttl=1800, show_spinner=False)
def decoder_market_catalog():
    data = scan_request(
        "/markets",
        {"language": "en"}
    )
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("markets", "data"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def decoder_number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
        if 0 <= number <= 1000:
            return number
    except (TypeError, ValueError):
        pass
    return None


def decoder_direction(*values):
    for value in values:
        if not isinstance(value, str):
            continue
        words = value.lower().strip()
        if re.search(r"\b(over|more|higher)\b", words):
            return "MORE"
        if re.search(r"\b(under|less|lower)\b", words):
            return "LESS"
    return None


def decoder_line(*records):
    # Use only explicit line/handicap fields.
    # Do not confuse decimal odds with a stat line.
    keys = (
        "line", "handicap", "points",
        "total", "threshold", "specialValue"
    )
    for record in records:
        if not isinstance(record, dict):
            continue
        for key in keys:
            if key in record:
                value = decoder_number(record[key])
                if value is not None:
                    return value
    return None


def decoder_tier(*values):
    text = " ".join(
        str(value).lower()
        for value in values
        if value is not None
    )
    if re.search(r"\bgoblin\b", text):
        return "Goblin"
    if re.search(r"\bdemon\b", text):
        return "Demon"
    if re.search(r"\bstandard\b|\bregular\b", text):
        return "Standard"
    return "Unknown"


def decoder_catalog_map(catalog):
    result = {}
    for item in catalog:
        if not isinstance(item, dict):
            continue
        market_id = item.get("marketId")
        if market_id is not None:
            result[str(market_id)] = item
    return result


if st.button(
    "🔍 DECODE NBA PROP LINES",
    use_container_width=True,
    key="decoder_v2_button"
):
    try:
        with st.spinner("Decoding NBA markets..."):
            games = scan_nba_games()[:max_games]
            catalog = decoder_catalog_map(
                decoder_market_catalog()
            )

            verified = []
            unresolved = []
            seen = set()

            for game in games:
                fixture_id = game.get("fixtureId")
                if not fixture_id:
                    continue

                matchup = (
                    str(game.get("participant1Name", "?"))
                    + " vs "
                    + str(game.get("participant2Name", "?"))
                )

                try:
                    odds = scan_game_odds(fixture_id)
                except (ValueError, requests.RequestException):
                    unresolved.append({
                        "Game": matchup,
                        "Reason": "Odds request failed"
                    })
                    continue

                books = odds.get("bookmakerOdds", {})
                if not isinstance(books, dict):
                    continue

                for book in SCAN_BOOKS:
                    book_data = books.get(book, {})
                    if not isinstance(book_data, dict):
                        continue

                    markets = book_data.get("markets", {})
                    if not isinstance(markets, dict):
                        continue

                    for market_id, market in markets.items():
                        if not isinstance(market, dict):
                            continue

                        definition = catalog.get(
                            str(market_id), {}
                        )

                        # Require catalog-confirmed player props.
                        if definition.get("playerProp") is not True:
                            continue

                        market_name = definition.get(
                            "marketName", "Unknown"
                        )

                        if market.get("marketActive") is not True:
                            continue

                        outcomes = market.get("outcomes", {})
                        if not isinstance(outcomes, dict):
                            continue

                        for outcome_id, outcome in outcomes.items():
                            if not isinstance(outcome, dict):
                                continue

                            players = outcome.get("players", {})
                            if not isinstance(players, dict):
                                continue

                            for player_id, player in players.items():
                                if not isinstance(player, dict):
                                    continue

                                if player.get("active") is not True:
                                    continue

                                player_name = player.get("playerName")
                                if not player_name:
                                    continue

                                direction = decoder_direction(
                                    player.get("outcomeName"),
                                    player.get("selectionName"),
                                    player.get("name"),
                                    outcome.get("outcomeName"),
                                    outcome.get("name"),
                                    definition.get("outcomeName")
                                )

                                line = decoder_line(
                                    player, outcome, market
                                )

                                tier = decoder_tier(
                                    player.get("lineType"),
                                    player.get("tier"),
                                    player.get("label"),
                                    outcome.get("lineType"),
                                    market.get("lineType")
                                )

                                row = {
                                    "Game": matchup,
                                    "Platform": book,
                                    "Player": player_name,
                                    "Stat": market_name,
                                    "Line": line,
                                    "Direction": direction,
                                    "Tier": tier,
                                    "American Odds": player.get(
                                        "priceAmerican"
                                    ),
                                    "Market ID": str(market_id),
                                    "Outcome ID": str(outcome_id),
                                    "Player ID": str(player_id)
                                }

                                # No guesses: all required fields
                                # must be explicitly verified.
                                missing = []
                                if line is None:
                                    missing.append("line")
                                if direction is None:
                                    missing.append("direction")
                                if tier == "Unknown":
                                    missing.append("line type")

                                if missing:
                                    row["Reason"] = (
                                        "Missing " + ", ".join(missing)
                                    )
                                    unresolved.append(row)
                                    continue

                                unique_key = (
                                    fixture_id, book, player_id,
                                    market_id, line, direction, tier
                                )

                                if unique_key in seen:
                                    continue

                                seen.add(unique_key)
                                verified.append(row)

            st.session_state["decoder_v2_verified"] = verified
            st.session_state["decoder_v2_unresolved"] = unresolved

    except (ValueError, requests.RequestException) as error:
        st.error(
            "Decoder could not complete: " + str(error)
        )


if "decoder_v2_verified" in st.session_state:
    verified = st.session_state["decoder_v2_verified"]
    unresolved = st.session_state["decoder_v2_unresolved"]

    c1, c2 = st.columns(2)
    c1.metric("Verified Lines", len(verified))
    c2.metric("Needs Decoding", len(unresolved))

    st.subheader("Verified Eligible Lines")
    if verified:
        st.dataframe(
            pd.DataFrame(verified),
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info(
            "No fully verified lines yet. "
            "The decoder will not invent missing data."
        )

    st.subheader("Unresolved Lines")
    if unresolved:
        st.dataframe(
            pd.DataFrame(unresolved),
            use_container_width=True,
            hide_index=True
        )

    st.caption(
        "Verified means the available API fields passed "
        "the decoder checks, not that a pick is profitable "
        "or that the sportsbook line is still current."
    )

# ============================================
# SPORTS LAB - ONE PLAYER DECODER DIAGNOSTIC
# ============================================

st.divider()
st.header("🔬 PrizePicks Decoder Diagnostic")

st.caption(
    "Inspects one PrizePicks player outcome. "
    "API keys and betslip links are not displayed."
)

if st.button(
    "🔬 INSPECT ONE PLAYER",
    use_container_width=True,
    key="inspect_one_player_v1"
):
    try:
        games = scan_nba_games()

        if not games:
            st.warning("No upcoming NBA games found.")
        else:
            found = False

            # Check no more than 3 games to conserve API calls.
            for game in games[:3]:
                odds = scan_game_odds(game["fixtureId"])

                books = odds.get("bookmakerOdds", {})
                prizepicks = books.get("prizepicks", {})
                markets = prizepicks.get("markets", {})

                if not isinstance(markets, dict):
                    continue

                for market_id, market in markets.items():
                    if not isinstance(market, dict):
                        continue

                    outcomes = market.get("outcomes", {})

                    if not isinstance(outcomes, dict):
                        continue

                    for outcome_id, outcome in outcomes.items():
                        if not isinstance(outcome, dict):
                            continue

                        players = outcome.get("players", {})

                        if not isinstance(players, dict):
                            continue

                        for player_id, player in players.items():
                            if not isinstance(player, dict):
                                continue

                            if not player.get("playerName"):
                                continue

                            # Hide sensitive or unnecessary fields.
                            safe_player = {
                                k: v
                                for k, v in player.items()
                                if k not in (
                                    "betslip",
                                    "apiKey",
                                    "url"
                                )
                            }

                            safe_market = {
                                k: v
                                for k, v in market.items()
                                if k != "outcomes"
                            }

                            safe_outcome = {
                                k: v
                                for k, v in outcome.items()
                                if k != "players"
                            }

                            st.success("Player outcome found!")

                            st.write("Game")
                            st.write(
                                game.get("participant1Name"),
                                "vs",
                                game.get("participant2Name")
                            )

                            st.write("Market ID:", market_id)
                            st.json(safe_market)

                            st.write("Outcome ID:", outcome_id)
                            st.json(safe_outcome)

                            st.write("Player ID:", player_id)
                            st.json(safe_player)

                            found = True
                            break

                        if found:
                            break
                    if found:
                        break
                if found:
                    break

            if not found:
                st.warning(
                    "No PrizePicks player outcome was "
                    "returned for the first 3 games."
                )

    except (ValueError, requests.RequestException) as error:
        st.error(
            "Diagnostic failed: " + str(error)
        )

# ============================================
# SPORTS LAB - MARKET DEFINITION INSPECTOR
# ============================================

st.divider()
st.header("🔬 Market Definition Inspector")

if st.button(
    "INSPECT MARKET 111682",
    key="inspect_market_111682"
):
    try:
        catalog = decoder_market_catalog()

        matches = [
            item for item in catalog
            if str(item.get("marketId")) == "111682"
        ]

        if matches:
            st.json(matches[0])
        else:
            st.warning(
                "Market 111682 was not found in the catalog."
            )

    except (ValueError, requests.RequestException):
        st.error("Could not retrieve market definitions.")

# ============================================
# NBA PROP DECODER V3 - CATALOG CORRECTED
# ============================================

st.divider()
st.header("🏀 NBA Prop Decoder V3")

def v3_decode_outcome(catalog_market, outcome_id):
    for entry in catalog_market.get("outcomes", []):
        if str(entry.get("outcomeId")) == str(outcome_id):
            name = str(entry.get("outcomeName", "")).lower()
            if name == "over":
                return "MORE"
            if name == "under":
                return "LESS"
    return None


if st.button(
    "DECODE REAL NBA LINES V3",
    use_container_width=True,
    key="nba_decoder_v3"
):
    try:
        with st.spinner("Decoding market definitions..."):
            catalog = decoder_catalog_map(
                decoder_market_catalog()
            )

            decoded = []
            seen = set()
            games = scan_nba_games()[:max_games]

            for game in games:
                fixture_id = game.get("fixtureId")
                if not fixture_id:
                    continue

                matchup = (
                    str(game.get("participant1Name", "?"))
                    + " vs "
                    + str(game.get("participant2Name", "?"))
                )

                odds = scan_game_odds(fixture_id)
                books = odds.get("bookmakerOdds", {})

                for book in SCAN_BOOKS:
                    book_data = books.get(book, {})
                    if not isinstance(book_data, dict):
                        continue

                    markets = book_data.get("markets", {})
                    if not isinstance(markets, dict):
                        continue

                    for market_id, market in markets.items():
                        definition = catalog.get(str(market_id), {})

                        if definition.get("playerProp") is not True:
                            continue

                        if market.get("marketActive") is not True:
                            continue

                        line = decoder_number(
                            definition.get("handicap")
                        )

                        if line is None:
                            continue

                        outcomes = market.get("outcomes", {})
                        if not isinstance(outcomes, dict):
                            continue

                        for outcome_id, outcome in outcomes.items():
                            direction = v3_decode_outcome(
                                definition, outcome_id
                            )

                            if direction is None:
                                continue

                            players = outcome.get("players", {})
                            if not isinstance(players, dict):
                                continue

                            for player_id, player in players.items():
                                if not isinstance(player, dict):
                                    continue

                                if player.get("active") is not True:
                                    continue

                                name = player.get("playerName")
                                if not name:
                                    continue

                                unique = (
                                    fixture_id,
                                    book,
                                    str(player_id),
                                    str(market_id),
                                    str(outcome_id)
                                )

                                if unique in seen:
                                    continue
                                seen.add(unique)

                                decoded.append({
                                    "Game": matchup,
                                    "Platform": book,
                                    "Player": name,
                                    "Stat": definition.get(
                                        "marketName"
                                    ),
                                    "Line": line,
                                    "Direction": direction,
                                    "Line Type": "Unverified",
                                    "Odds": player.get(
                                        "priceAmerican"
                                    ),
                                    "Market ID": market_id,
                                    "Outcome ID": outcome_id,
                                    "Active": True
                                })

            st.session_state["nba_v3_lines"] = decoded

    except (ValueError, requests.RequestException) as error:
        st.error("Decoder error: " + str(error))


if "nba_v3_lines" in st.session_state:
    rows = st.session_state["nba_v3_lines"]

    st.metric("Decoded Active Outcomes", len(rows))

    if rows:
        df = pd.DataFrame(rows)

        platform = st.selectbox(
            "Filter platform",
            ["All"] + SCAN_BOOKS,
            key="nba_v3_platform"
        )

        if platform != "All":
            df = df[df["Platform"] == platform]

        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True
        )

    else:
        st.warning("No matching active player props decoded.")

    st.caption(
        "Line and MORE/LESS are decoded from the market "
        "catalog. Standard/Goblin/Demon classification "
        "remains unverified. These are market records, "
        "not model recommendations."
    )

       
