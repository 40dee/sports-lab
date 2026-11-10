"""Explicit, bounded The Odds API evaluation; never an automatic board scan."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import math
import requests

BASE = 'https://api.the-odds-api.com/v4/sports/basketball_nba'
MARKETS = ('player_points', 'player_fantasy_points',
           'player_points_alternate', 'player_fantasy_points_alternate')
MAX_CREDITS = len(MARKETS)


def stamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def quota(response):
    result = {}
    for header, label in (('x-requests-remaining', 'credits_remaining'),
                          ('x-requests-used', 'credits_used'),
                          ('x-requests-last', 'last_request_credits')):
        try:
            value = int(response.headers[header])
            if value >= 0:
                result[label] = value
        except (KeyError, TypeError, ValueError):
            pass
    return result


def sample_rows(event):
    """Keep each exact quote and only the directions actually returned."""
    grouped = {}
    for book in event.get('bookmakers') or []:
        if book.get('key') != 'prizepicks':
            continue
        for market in book.get('markets') or []:
            key = market.get('key')
            if key not in MARKETS:
                continue
            for outcome in market.get('outcomes') or []:
                side = {'Over': 'MORE', 'Under': 'LESS'}.get(outcome.get('name'))
                player = outcome.get('description')
                point = outcome.get('point')
                if not side or not isinstance(player, str) or not player.strip() or isinstance(point, bool):
                    continue
                try:
                    point = float(point)
                    if not math.isfinite(point) or not 0 < point <= 1000:
                        continue
                except (ValueError, TypeError):
                    continue
                identity = (player.strip(), key, point)
                row = grouped.setdefault(identity, {
                    'Player': player.strip(),
                    'Stat': 'Fantasy Score' if 'fantasy' in key else 'Points',
                    'Market': key, 'Line': point,
                    'Line type': 'Alternate — tier unverified' if key.endswith('_alternate') else 'Standard',
                    'Updated (UTC)': market.get('last_update'),
                    'Directions': [],
                })
                if side not in row['Directions']:
                    row['Directions'].append(side)
    return list(grouped.values())


def run_probe(store, api_key, now=None, get=None):
    """At most one probe per credential per UTC day, atomically reserved."""
    key = str(api_key or '').strip()
    if not key:
        raise ValueError('THE_ODDS_API_KEY is missing in Streamlit Secrets.')
    if not getattr(store, 'remote', False) or not hasattr(store, 'query'):
        raise ValueError('Permanent storage is required for the test credit guard.')
    current = now or datetime.now(timezone.utc)
    fingerprint = hashlib.sha256(key.encode()).hexdigest()[:20]
    record_key = 'the_odds_api_probe:' + current.strftime('%Y-%m-%d') + ':' + fingerprint
    existing = store.get('source', record_key)
    if existing:
        return existing
    claimed = store.query(
        'INSERT INTO sports_records VALUES (?,?,?,?) ON CONFLICT(namespace,key) DO NOTHING RETURNING key',
        ('provider_probe_lock', record_key, '{}', current.isoformat()), fetch=True)
    if not claimed:
        return {'state': 'already_reserved', 'checked_at': current.isoformat(),
                'note': 'This key already has a test reserved today. No additional request was made.'}
    report = {'state': 'checking', 'checked_at': current.isoformat(),
              'max_test_credits': MAX_CREDITS, 'http_requests': 0,
              'board_changed': False, 'scheduled_downloads_enabled': False, 'rows': []}
    get = get or requests.get

    def request(path, params):
        report['http_requests'] += 1
        try:
            response = get(BASE + path, params=dict(params, apiKey=key), timeout=20)
        except requests.RequestException:
            raise ValueError('The Odds API connection failed. No retry was made.') from None
        report.update(quota(response))
        if response.status_code != 200:
            if response.status_code in (401, 403):
                raise ValueError('The Odds API rejected this key or its access. Check the key and free-plan access.')
            if response.status_code == 429:
                raise ValueError('The Odds API quota or rate limit was reached. No retry was made.')
            raise ValueError('The Odds API returned HTTP ' + str(response.status_code) + '. No retry was made.')
        try:
            return response.json()
        except ValueError:
            raise ValueError('The Odds API returned invalid JSON. No retry was made.') from None

    try:
        events = request('/events', {'dateFormat': 'iso'})
        if not isinstance(events, list):
            raise ValueError('The Odds API schedule response had an unexpected format.')
        upcoming = [event for event in events if isinstance(event, dict)
                    and stamp(event.get('commence_time'))
                    and current + timedelta(minutes=5) < stamp(event['commence_time']) < current + timedelta(hours=48)
                    and event.get('id') and event.get('home_team') and event.get('away_team')]
        upcoming.sort(key=lambda event: stamp(event['commence_time']))
        report['key_accepted'] = True
        if not upcoming:
            report.update(state='no_upcoming_sample', note='Key accepted. No upcoming NBA game within 48 hours was returned; no prop request was made.')
        elif report.get('credits_remaining', MAX_CREDITS) < MAX_CREDITS:
            report.update(state='insufficient_quota', note='Key accepted, but fewer than four credits remain. No prop request was made.')
        else:
            event = upcoming[0]
            report['game'] = event['away_team'] + ' at ' + event['home_team']
            report['start_utc'] = event['commence_time']
            event_id = str(event['id'])
            if not event_id.isalnum():
                raise ValueError('The Odds API event ID had an unexpected format.')
            response = request('/events/' + event_id + '/odds', {
                'regions': 'us_dfs', 'bookmakers': 'prizepicks',
                'markets': ','.join(MARKETS), 'oddsFormat': 'american',
                'dateFormat': 'iso', 'includeMultipliers': 'true'})
            if not isinstance(response, dict) or response.get('id') != event['id']:
                raise ValueError('The Odds API returned a different game. Sample was not accepted.')
            rows = sample_rows(response)
            report['rows'] = rows
            report['market_counts'] = {market: sum(r['Market'] == market for r in rows) for market in MARKETS}
            report['state'] = 'sample_received' if rows else 'no_prizepicks_sample'
            report['note'] = ('Sample received for evaluation only. Compare it with PrizePicks before enabling imports. '
                              'Alternates remain unverified tiers; no offered direction is inferred.' if rows else
                              'Key accepted, but this game returned no PrizePicks Points or Fantasy sample. Full coverage is not confirmed.')
    except ValueError as error:
        report.update(state='unavailable', note=str(error))
    store.put('source', record_key, report)
    store.put('source', 'the_odds_api_probe_latest', report)
    return report


def render_probe(st, store, api_key):
    with st.expander('The Odds API — free connection test', expanded=True):
        st.write('New key configured' if api_key else 'THE_ODDS_API_KEY missing')
        st.caption('One NBA game · PrizePicks Points and Fantasy, regular and alternate markets · at most four credits. Once per key per UTC day. Browsing does not run this test. Automatic downloads remain off.')
        if st.button('Test new free API — up to 4 credits', key='the_odds_api_probe', disabled=not bool(api_key)):
            try:
                with st.spinner('Checking one game with The Odds API…'):
                    result = run_probe(store, api_key)
                st.session_state['_the_odds_api_probe'] = result
            except ValueError as error:
                st.warning(str(error))
        result = st.session_state.get('_the_odds_api_probe') or store.get('source', 'the_odds_api_probe_latest')
        if result:
            if result.get('state') == 'sample_received':
                st.success('New API sample received. Current board and original forecasts are unchanged.')
            else:
                st.info(result.get('note', 'Test reserved; no automatic retry.'))
            if result.get('game'):
                st.write(result['game'] + ' · Start (UTC): ' + result.get('start_utc', ''))
            st.json({k: v for k, v in result.items() if k != 'rows'})
            if result.get('rows'):
                st.dataframe(result['rows'], hide_index=True, use_container_width=True)
