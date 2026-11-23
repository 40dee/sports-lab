"""Free, chronological 2022-24 NBA season-start backtest; no PrizePicks or odds calls.

Uses nba_api LeagueGameLog (2022-23, 2023-24, 2024-25). The proxy
line is each player's previous 15 appearances, never the target game.
V2 replay functions are imported from projection_v2; unavailable historical
Vegas/pace/position context is intentionally neutral rather than fabricated.
"""
from __future__ import annotations

import argparse
import math
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from projection_v2 import (
    ENGINE_VERSION, estimate_robust_minutes, shrunk_per_minute_rate,
    calibrated_hit_probability,
)

SEASONS = ("2022-23", "2023-24", "2024-25")
STATS = ("PTS", "REB", "AST")
COLUMNS = ("PLAYER_ID", "GAME_ID", "GAME_DATE", "MIN", *STATS)
CACHE = Path(".cache/nba_backtest")


def load_season(season: str, refresh: bool = False) -> pd.DataFrame:
    path = CACHE / f"player_gamelog_{season}.csv"
    if path.exists() and not refresh:
        frame = pd.read_csv(path, dtype={"GAME_ID": str, "PLAYER_ID": str})
    else:
        from nba_api.stats.endpoints import leaguegamelog
        print(f"Downloading free NBA player logs: {season}", flush=True)
        response = leaguegamelog.LeagueGameLog(
            season=season, season_type_all_star="Regular Season",
            player_or_team_abbreviation="P", timeout=90,
        )
        frame = response.get_data_frames()[0]
        if frame.empty:
            raise RuntimeError(f"No player games returned for {season}")
        CACHE.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False)
        time.sleep(2)
    missing = set(COLUMNS) - set(frame.columns)
    if missing:
        raise RuntimeError(f"Missing columns for {season}: {sorted(missing)}")
    frame = frame[list(COLUMNS)].copy()
    frame["SEASON"] = season
    frame["GAME_DATE"] = pd.to_datetime(frame["GAME_DATE"], utc=True, errors="coerce")
    for col in ("MIN", *STATS):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["PLAYER_ID"] = frame["PLAYER_ID"].astype(str)
    frame["GAME_ID"] = frame["GAME_ID"].astype(str)
    frame = frame.dropna(subset=["GAME_DATE", "MIN", *STATS])
    frame = frame[frame["MIN"].between(.01, 70)]
    return frame.drop_duplicates(["PLAYER_ID", "GAME_ID"])


def run(frame: pd.DataFrame) -> pd.DataFrame:
    # Only completed games earlier than the target calendar day can inform a pick.
    frame = frame.sort_values(["GAME_DATE", "GAME_ID"]).reset_index(drop=True)
    records = []
    for player, games in frame.groupby("PLAYER_ID", sort=False):
        games = games.sort_values(["GAME_DATE", "GAME_ID"]).reset_index(drop=True)
        for idx in range(15, len(games)):
            target = games.iloc[idx]
            cutoff = target["GAME_DATE"].normalize()
            history = games.iloc[:idx]
            history = history[history["GAME_DATE"] < cutoff].sort_values("GAME_DATE", ascending=False)
            if len(history) < 15:
                continue
            recent = history.head(15)
            current = history[history["SEASON"].eq(target["SEASON"])]
            if len(current) < 3:
                continue
            # The replay V2 robust minutes model uses recency and outlier clipping.
            minutes, _ = estimate_robust_minutes(current, None, None)
            for stat in STATS:
                line = float(recent[stat].mean())
                if line <= 0:
                    continue
                try:
                    rate, _ = shrunk_per_minute_rate(
                        history, current, stat, target["SEASON"], cutoff
                    )
                    projection = minutes * rate  # Neutral historical context.
                    if not math.isfinite(projection) or abs(projection-line) < 1e-9:
                        continue
                    direction = "MORE" if projection > line else "LESS"
                    actual = float(target[stat])
                    if abs(actual-line) < 1e-9:
                        continue  # Push: not a binary event.
                    probability, method = calibrated_hit_probability(
                        recent[stat].tolist(), projection, line, direction, [stat]
                    )
                    if probability is None or not math.isfinite(probability):
                        continue
                    win = int(actual > line if direction == "MORE" else actual < line)
                    records.append({
                        "season": target["SEASON"], "player": player,
                        "date": target["GAME_DATE"].date().isoformat(),
                        "stat": stat, "line": line, "projection": projection,
                        "actual": actual, "direction": direction,
                        "probability": probability, "win": win,
                        "probability_method": method,
                    })
                except (ValueError, KeyError, TypeError, OverflowError, ZeroDivisionError):
                    continue
    return pd.DataFrame(records)


def report(results: pd.DataFrame) -> None:
    if results.empty:
        raise RuntimeError("No gradeable picks: check dataset and sample requirements")
    print(f"\nV2 ENGINE: {ENGINE_VERSION}")
    print("Proxy: preceding 15 games; no historical PrizePicks lines")
    print("Historical game context: neutral (no reconstructed Vegas/pace/matchup)")
    print("Note: V2 replay minutes differ from the new live app.py minutes shrinkage.")
    print(f"Samples: {len(results):,}")
    print(f"Accuracy: {results.win.mean():.4%}")
    print(f"Brier score: {np.mean((results.probability-results.win)**2):.6f}")
    print("\nBY STAT")
    for stat, group in results.groupby("stat"):
        print(f"{stat:4s} n={len(group):7,d} accuracy={group.win.mean():.4%} brier={np.mean((group.probability-group.win)**2):.6f}")
    print("\nBY SEASON")
    for season, group in results.groupby("season"):
        print(f"{season} n={len(group):7,d} accuracy={group.win.mean():.4%} brier={np.mean((group.probability-group.win)**2):.6f}")
    print("\nCALIBRATION TABLE (forecast confidence vs actual hit rate)")
    bins = [(0, .50), (.50, .55), (.55, .60), (.60, .65), (.65, .70),
            (.70, .75), (.75, .80), (.80, .90), (.90, 1.000001)]
    print(f"{'Bin':>13} {'N':>9} {'Mean P':>10} {'Hit rate':>10} {'Brier':>10}")
    for lo, hi in bins:
        group = results[(results.probability >= lo) & (results.probability < hi)]
        if len(group):
            brier = np.mean((group.probability-group.win)**2)
            print(f"{lo:.0%}-{min(hi,1):.0%} {len(group):9,d} {group.probability.mean():10.3%} {group.win.mean():10.3%} {brier:10.5f}")
    print("\nCAUTION: Proxy-line accuracy is not evidence of profitable real-market picks.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="Refetch free logs instead of using CSV cache")
    args = parser.parse_args()
    frames = [load_season(s, refresh=args.refresh) for s in SEASONS]
    all_games = pd.concat(frames, ignore_index=True)
    print(f"Loaded {len(all_games):,} player-game rows", flush=True)
    report(run(all_games))


if __name__ == "__main__":
    main()
