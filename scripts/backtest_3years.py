"""Free, chronological 2022-24 NBA season-start backtest; no PrizePicks or odds calls.

Uses public sportsdataverse GitHub Release player box-score CSVs (2022-23 through 2024-25). The proxy
line is each player's previous 15 appearances, never the target game.
V2 replay functions are imported from projection_v2; unavailable historical
Vegas/pace/position context is intentionally neutral rather than fabricated.
"""
from __future__ import annotations

import argparse
import math
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


# Public, static GitHub Release CSVs: 2023 = 2022-23, etc.
# https://github.com/sportsdataverse/sportsdataverse-data/releases/tag/nba_stats_player_boxscores
CSV_URL = ("https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
           "nba_stats_player_boxscores/player_boxscores_{year}.csv")


def normalize_box_scores(raw: pd.DataFrame, season: str) -> pd.DataFrame:
    """Normalize sportsdataverse player box scores to V2 replay schema."""
    df = raw.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_").replace(".", "_")
                  for c in df.columns]
    # Explicit published sportsdataverse fields plus common alternate names.
    aliases = {
        "PLAYER_ID": ("player_id", "person_id", "athlete_id", "playerid", "athleteid"),
        "GAME_ID": ("game_id", "gameid", "event_id", "eventid"),
        "GAME_DATE": ("game_date", "game_date_est", "gamedate", "date", "game_datetime"),
        "PLAYER_NAME": ("player_name", "name_i", "player_slug", "display_name"),
        "TEAM_ABBREVIATION": ("team_tricode", "team_abbreviation"),
        "SEASON": ("season",),
        "MIN": ("min", "minutes", "minutes_played", "mins", "min_played"),
        "PTS": ("pts", "points", "points_scored"),
        "REB": ("reb", "rebounds_total", "rebounds", "total_rebounds"),
        "AST": ("ast", "assists"),
        "STL": ("stl", "steals"),
        "BLK": ("blk", "blocks"),
        "TOV": ("tov", "turnovers"),
        "FG3M": ("fg3m", "three_pointers_made"),
    }
    rename = {}
    for canonical, options in aliases.items():
        source = next((col for col in (canonical.lower(), *options) if col in df.columns), None)
        if source is not None:
            rename[source] = canonical
    df = df.rename(columns=rename)
    # The source can encode a season as its ending year (2023) or
    # as an NBA label (2022-23). Validate against the requested file.
    if "SEASON" in df.columns:
        raw_seasons = df["SEASON"].dropna().astype(str).str.strip()
        ending_year = int(season[:4]) + 1
        allowed = {season, str(ending_year), str(int(season[:4]))}
        unexpected = sorted(set(raw_seasons.unique()) - allowed)
        if unexpected:
            raise ValueError(
                f"{season}: unexpected season labels {unexpected[:10]}; "
                "refusing to silently mix seasons"
            )
    df["SEASON"] = str(season)
    required = ("PLAYER_ID", "GAME_ID", "MIN", *STATS)
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(
            f"CSV for {season} missing required fields {missing}; "
            f"available columns: {sorted(df.columns)}"
        )
    if "GAME_DATE" not in df.columns:
        # Game IDs are identifiers, NOT actual dates. Assign an ordered
        # synthetic timestamp per distinct numeric game ID for replay only.
        # Never interpret a game ID as a real calendar date.
        ids = pd.to_numeric(df["GAME_ID"], errors="coerce")
        if ids.isna().any():
            raise ValueError(f"{season}: no game date and nonnumeric game IDs; cannot safely order")
        unique_ids = sorted(ids.unique())
        if len(unique_ids) < 100:
            raise ValueError(f"{season}: too few game IDs to establish chronological proxy")
        ranks = {game_id: i for i, game_id in enumerate(unique_ids)}
        base = pd.Timestamp(f"{season[:4]}-01-01", tz="UTC")
        df["GAME_DATE"] = ids.map(ranks).map(
            lambda rank: base + pd.Timedelta(days=int(rank))
        )
        print(f"WARNING {season}: no actual game dates; using numeric GAME_ID ordering "
              "with synthetic dates (assumes IDs increase chronologically)", flush=True)
    else:
        df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"], utc=True, errors="coerce")
    # NBA box scores may encode minutes as MM:SS.
    def minutes_number(value):
        if isinstance(value, str) and ":" in value:
            try:
                mm, ss = value.split(":", 1)
                return float(mm) + float(ss) / 60
            except (ValueError, TypeError):
                return float("nan")
        return pd.to_numeric(value, errors="coerce")
    df["MIN"] = df["MIN"].map(minutes_number)
    for stat in STATS:
        df[stat] = pd.to_numeric(df[stat], errors="coerce")
    df["PLAYER_ID"] = df["PLAYER_ID"].astype(str)
    df["GAME_ID"] = df["GAME_ID"].astype(str)
    df = df.dropna(subset=["GAME_DATE", "MIN", *STATS])
    df = df[df["MIN"].between(.01, 70)]
    df = df.drop_duplicates(["PLAYER_ID", "GAME_ID"])
    if len(df) < 10000:
        raise RuntimeError(
            f"{season} contains only {len(df)} valid player-game rows; "
            "refusing to backtest season-level or incomplete data"
        )
    return df


def load_season(season: str, refresh: bool = False) -> pd.DataFrame:
    path = CACHE / f"player_boxscores_{season}.csv"
    if path.exists() and not refresh:
        raw = pd.read_csv(path, low_memory=False)
    else:
        year = int(season[:4]) + 1
        url = CSV_URL.format(year=year)
        print(f"Downloading public GitHub player box-score CSV: {season}", flush=True)
        try:
            raw = pd.read_csv(url, low_memory=False)
        except Exception as exc:
            raise RuntimeError(f"Failed to download {url}: {exc}") from exc
        if raw.empty:
            raise RuntimeError(f"Empty GitHub CSV for {season}")
        CACHE.mkdir(parents=True, exist_ok=True)
        raw.to_csv(path, index=False)
    return normalize_box_scores(raw, season)


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
    parser.add_argument("--refresh", action="store_true", help="Refetch public GitHub CSVs instead of using local cache")
    args = parser.parse_args()
    frames = [load_season(s, refresh=args.refresh) for s in SEASONS]
    all_games = pd.concat(frames, ignore_index=True)
    print(f"Loaded {len(all_games):,} player-game rows", flush=True)
    report(run(all_games))


if __name__ == "__main__":
    main()
