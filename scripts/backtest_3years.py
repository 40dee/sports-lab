"""Free, chronological 2022-24 NBA season-start backtest; no PrizePicks or odds calls.

Uses public sportsdataverse GitHub Release player box-score CSVs (2022-23 through 2024-25). The proxy
line is each player's previous 15 appearances, never the target game.
V2 replay functions are imported from projection_v2; unavailable historical
Vegas/pace/position context is intentionally neutral rather than fabricated.
"""
from __future__ import annotations

import argparse
import sys
import hashlib
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from projection_v2 import (
    ENGINE_VERSION, estimate_robust_minutes, shrunk_per_minute_rate,
    calibrated_hit_probability,
)

VARIANTS = (("baseline", 1.0, False), ("pts_only", .95, False),
            ("cap_only", 1.0, True), ("combined", .95, True))

SEASONS = ("2022-23", "2023-24", "2024-25")
STATS = ("PTS", "REB", "AST")
COLUMNS = ("PLAYER_ID", "GAME_ID", "GAME_DATE", "MIN", *STATS)
CACHE = Path(".cache/nba_backtest")


# Public, static GitHub Release CSVs: 2023 = 2022-23, etc.
# https://github.com/sportsdataverse/sportsdataverse-data/releases/tag/nba_stats_player_boxscores
CSV_URL = ("https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
           "nba_stats_player_boxscores/player_boxscores_{year}.csv")


def normalize_box_scores(raw: pd.DataFrame, season: str) -> pd.DataFrame:
    """Map common published box-score schemas; reject incomplete datasets."""
    frame = raw.copy()
    frame.columns = [str(c).strip().lower().replace(" ", "_").replace(".", "_")
                     for c in frame.columns]
    # Explicit source-to-model mapping; prefer canonical names when both exist.
    rename = {
        "game_date": "GAME_DATE", "game_id": "GAME_ID",
        "person_id": "PLAYER_ID", "minutes": "MIN", "points": "PTS",
        "rebounds_total": "REB", "assists": "AST", "season": "SEASON",
    }
    aliases = {
        "PLAYER_ID": ("player_id", "athlete_id", "playerid", "athleteid"),
        "GAME_ID": ("gameid", "event_id", "eventid"),
        "GAME_DATE": ("gamedate", "date", "game_datetime", "game_date_est",
                      "game_date_time", "game_date_time_utc", "game_date_time_est"),
        "MIN": ("min", "minutes_played", "mins", "min_played"),
        "PTS": ("pts", "points_scored"),
        "REB": ("reb", "rebounds", "total_rebounds"),
        "AST": ("ast",),
    }
    for target, options in aliases.items():
        if target not in rename.values() or not any(
            source in frame.columns and dest == target for source, dest in rename.items()
        ):
            match = next((col for col in options if col in frame.columns), None)
            if match is not None:
                rename[match] = target
    frame = frame.rename(columns=rename)
    frame = frame.loc[:, ~frame.columns.duplicated()]
    synthetic = "GAME_DATE" not in frame.columns
    if synthetic and "GAME_ID" in frame.columns:
        ids = pd.to_numeric(frame["GAME_ID"], errors="coerce")
        if ids.isna().any():
            raise ValueError(f"{season}: nonnumeric or missing GAME_ID; cannot infer order")
        # Rank distinct IDs, keeping all players in a game on the same synthetic day.
        ranks = ids.rank(method="dense").astype("int64") - 1
        # Spread ranks across a season-sized interval with distinct timestamps.
        # The replay uses exact timestamp cutoffs for synthetic rows below.
        frame["GAME_DATE"] = pd.Timestamp(f"{season[:4]}-10-01", tz="UTC") + pd.to_timedelta(ranks, unit="h")
        print(f"WARNING {season}: synthetic GAME_ID order; chronology unverified", flush=True)
    missing = set(COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"CSV for {season} missing required {sorted(missing)}; "
                         f"available columns: {sorted(frame.columns)}")
    out = frame[list(COLUMNS)].copy()
    out["SEASON"] = season
    out["SYNTHETIC_DATE"] = synthetic
    out["GAME_DATE"] = pd.to_datetime(out["GAME_DATE"], utc=True, errors="coerce")
    # NBA box scores may encode minutes as MM:SS.
    def minutes_number(value):
        if isinstance(value, str) and ":" in value:
            try:
                mm, ss = value.split(":", 1)
                return float(mm) + float(ss) / 60
            except (ValueError, TypeError):
                return float("nan")
        return pd.to_numeric(value, errors="coerce")
    out["MIN"] = out["MIN"].map(minutes_number)
    for stat in STATS:
        out[stat] = pd.to_numeric(out[stat], errors="coerce")
    out["PLAYER_ID"] = out["PLAYER_ID"].astype(str)
    out["GAME_ID"] = out["GAME_ID"].astype(str)
    out = out.dropna(subset=["GAME_DATE", "MIN", *STATS])
    out = out[out["MIN"].between(.01, 70)]
    out = out.drop_duplicates(["PLAYER_ID", "GAME_ID"])
    if len(out) < 10000:
        raise RuntimeError(
            f"{season} contains only {len(out)} valid player-game rows; "
            "refusing to backtest season-level or incomplete data"
        )
    return out


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
            cutoff = target["GAME_DATE"] if target["SYNTHETIC_DATE"] else target["GAME_DATE"].normalize()
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
                    base_projection = minutes * rate  # Neutral historical context.
                    for variant, multiplier, cap in VARIANTS:
                        projection = base_projection * (multiplier if stat == 'PTS' else 1.0)
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
                        if cap and probability > .65:
                            probability = .60  # Exact production penalty, not a hard ceiling.
                        win = int(actual > line if direction == "MORE" else actual < line)
                        records.append({
                            "variant": variant, "game_id": target["GAME_ID"],
                            "season": target["SEASON"], "player": player,
                            "date": target["GAME_DATE"].isoformat(),
                            "synthetic_date": bool(target["SYNTHETIC_DATE"]),
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
    if results.synthetic_date.any():
        print("WARNING: synthetic GAME_ID ordering; true chronology and baseline equivalence unverified.")
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
    for season, frame in zip(SEASONS, frames):
        print(f'{season} rows={len(frame)} sha256={hashlib.sha256(frame.to_csv(index=False).encode()).hexdigest()}')
    results = run(all_games)
    keys = ['season', 'player', 'game_id', 'stat']
    common = results.groupby(keys)['variant'].nunique().reset_index(name='variants')
    paired = results.merge(common[common.variants == len(VARIANTS)][keys], on=keys)
    for variant, _, _ in VARIANTS:
        print(f'\\n=== {variant} (all eligible) ===')
        report(results[results.variant == variant])
        print(f'\\n=== {variant} (matched cohort) ===')
        report(paired[paired.variant == variant])
    results.to_csv('backtest_results.csv', index=False)


if __name__ == "__main__":
    main()
