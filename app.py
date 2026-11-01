
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
