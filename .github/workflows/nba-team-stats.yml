name: Update NBA team stats

on:
  workflow_dispatch:
  schedule:
    - cron: "17 * * * *"

permissions:
  contents: write

concurrency:
  group: nba-team-stats
  cancel-in-progress: false

jobs:
  update:
    runs-on: ubuntu-latest
    timeout-minutes: 5

    steps:
      - name: Check out repository
        uses: actions/checkout@v4

      - name: Fetch and validate NBA statistics
        shell: bash
        run: |
          python3 - <<'PY'
          import json
          import pathlib
          import urllib.parse
          import urllib.request
          from datetime import datetime, timezone

          now = datetime.now(timezone.utc)
          year = now.year if now.month >= 10 else now.year - 1
          season = f"{year}-{str(year + 1)[-2:]}"

          params = {
              "MeasureType": "Advanced",
              "PerMode": "PerGame",
              "LeagueID": "00",
              "Season": season,
              "SeasonType": "Regular Season",
              "LastNGames": 0,
              "Month": 0,
              "OpponentTeamID": 0,
              "PaceAdjust": "N",
              "PlusMinus": "N",
              "Rank": "N",
              "Period": 0,
          }

          url = (
              "https://stats.nba.com/stats/leaguedashteamstats?"
              + urllib.parse.urlencode(params)
          )
          request = urllib.request.Request(
              url,
              headers={
                  "User-Agent": "Mozilla/5.0",
                  "Referer": "https://www.nba.com/",
              },
          )

          with urllib.request.urlopen(request, timeout=45) as response:
              payload = json.load(response)

          if payload.get("parameters", {}).get("Season") != season:
              raise ValueError("Unexpected season.")

          required = {
              "TEAM_ID", "TEAM_NAME", "GP",
              "OFF_RATING", "DEF_RATING", "PACE",
              "OFF_RATING_RANK", "DEF_RATING_RANK", "PACE_RANK",
          }
          table = next(
              (
                  table for table in payload.get("resultSets", [])
                  if required.issubset(table.get("headers", []))
              ),
              None,
          )
          if table is None:
              raise ValueError("Required statistics are missing.")

          headers = table["headers"]
          values = table["rowSet"]
          if any(len(row) != len(headers) for row in values):
              raise ValueError("Incomplete statistics row.")

          rows = [dict(zip(headers, row)) for row in values]
          if len(rows) != 30 or len({row["TEAM_ID"] for row in rows}) != 30:
              raise ValueError("Expected 30 unique teams.")

          for row in rows:
              if any(row[field] is None for field in required):
                  raise ValueError("Missing team statistic.")
              if float(row["GP"]) <= 0:
                  raise ValueError("Team has no completed games.")

          output = {
              "season": season,
              "source": "NBA Stats",
              "fetched_at": datetime.now(timezone.utc).isoformat(),
              "teams": rows,
          }
          folder = pathlib.Path("data")
          folder.mkdir(exist_ok=True)
          target = folder / "nba_team_advanced.json"
          target.write_text(json.dumps(output, indent=2), encoding="utf-8")
          print(f"Saved validated statistics for {len(rows)} teams.")
          PY

      - name: Save updated statistics
        shell: bash
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add data/nba_team_advanced.json
          if ! git diff --cached --quiet; then
            git commit -m "Update NBA team efficiency statistics"
            git push
          fi
