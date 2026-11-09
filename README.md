# sports-lab

### Monthly odds allowance and manual lines

Odds downloads first read the unmetered `/account` endpoint. Only sanitized usage
numbers are saved. Shared reservations reconcile upward with provider usage and
pace the remaining allowance through the end of the UTC calendar month. Cache
hits remain usable after exhaustion. `ODDSPAPI_DAILY_REQUEST_CAP` is an additional
ceiling; optional `ODDSPAPI_MONTHLY_REQUEST_CAP` can lower the provider limit.
The worker's matching repository variable must agree with any Streamlit setting.
Other applications using the same key can spend quota between account checks.

Settings → **Enter current PrizePicks lines — no odds requests** loads the public
ESPN schedule and rosters. Enter actual posted player lines, tiers and offered
MORE/LESS directions, including Fantasy Score when posted. Confirmation is required.
The app validates the entire table before importing, calculates only those entries,
and rebuilds combos with normal history, freshness, roster and injury checks.
Manual observation is recorded separately from provider timestamps. An import
cannot guarantee a combo, replace full-board coverage, or backfill a started game.
Existing frozen forecasts and grades are not rewritten.

The **Check monthly odds budget and league coverage** workflow reads account usage
and attempts at most one guarded PrizePicks league-coverage probe per account/month,
using a league ID already saved by the NBA scanner. It stores counts and a result
in diagnostics, without exposing account credentials. The batch endpoint is not
yet substituted for production scans: named-player, tier, Fantasy, and offered-side
coverage must be verified first. This probe may use one paid request when allowed.
