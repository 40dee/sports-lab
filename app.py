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
st.info("First load the discovery batch. Then scan lines and expand a player card to load current and previous season history. Saved ESPN responses use a local SQLite cache; Streamlit can erase local files on redeploy. Export the cache below for backup until external database storage is connected.")

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
        f"Source: ESPN · Seven-calendar-day discovery batch · Regular season · "
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
        "identify players for card research. Open a card to load its deeper history."
    )


with st.expander("What is connected and what remains"):
    st.write(
        "Connected: NBA odds scanner, market decoding, player cards, "
        "filters, grouped thresholds and CSV download."
    )
    st.write(
        "Historical connection: available through LOAD NBA DISCOVERY BATCH. "
        "A successful response is required before using these stats."
    )
    st.write(
        "Pending: photos, "
        "defensive rankings, injuries, P1/P2 projections, "
        "NFL, DraftKings lineup builders, saved picks and ROI audits."
    )


if HISTORY_DB.exists():
    st.download_button('Back up historical response cache', data=HISTORY_DB.read_bytes(), file_name='sports_lab_history.sqlite3', mime='application/vnd.sqlite3')
