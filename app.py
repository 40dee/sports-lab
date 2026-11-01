
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

