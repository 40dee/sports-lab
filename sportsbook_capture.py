"""Parse sportsbook evidence already returned with the scheduled PrizePicks request."""
from datetime import datetime
import math

# One bookmaker billing group. Keep the requested market set and reservation unchanged.
BOOKS = ('draftkings', 'fanduel', 'betmgm', 'bovada', 'pinnacle')
REQUEST_BOOKS = ','.join(('prizepicks',) + BOOKS)


def capture_quotes(event, fetched_at, current, markets):
    rows = []
    for book in event.get('bookmakers') or []:
        if not isinstance(book, dict) or book.get('key') not in BOOKS:
            continue
        for market in book.get('markets') or []:
            if not isinstance(market, dict):
                continue
            key = market.get('key') or ''
            stat = markets.get(key.removesuffix('_alternate'))
            try:
                observed = datetime.fromisoformat(str(market.get('last_update')).replace('Z', '+00:00'))
                if observed.tzinfo is None or not 0 <= (current-observed).total_seconds() <= 1800:
                    continue
            except (ValueError, TypeError):
                continue
            if not stat:
                continue
            for outcome in market.get('outcomes') or []:
                if not isinstance(outcome, dict):
                    continue
                direction = {'Over': 'MORE', 'Under': 'LESS'}.get(outcome.get('name'))
                try:
                    if isinstance(outcome.get('point'), bool) or isinstance(outcome.get('price'), bool):
                        continue
                    line, price = float(outcome['point']), float(outcome['price'])
                    if not math.isfinite(line) or line <= 0 or not math.isfinite(price) or abs(price) < 100:
                        continue
                except (KeyError, ValueError, TypeError):
                    continue
                if not direction or not isinstance(outcome.get('description'), str):
                    continue
                rows.append({'Platform': book['key'], 'Player': outcome['description'],
                    'Game': event['home_team']+' vs '+event['away_team'],
                    'Start (UTC)': event['commence_time'], 'Fixture ID': 'theodds:'+str(event['id']),
                    'Period': 'result', 'Stat': stat[0], 'Stat Key': stat[1],
                    'Line': line, 'Direction': direction, 'Odds': price,
                    'Market ID': 'theodds:'+key, 'Odds checked (UTC)': observed.isoformat(),
                    'Provider fetched at': fetched_at})
    return rows


def attach_quotes(cards, event, fetched_at, current, markets):
    quotes = capture_quotes(event, fetched_at, current, markets)
    for card in cards:
        # Evidence stays attached to PrizePicks cards; it is never a separate playable line.
        matches = [q for q in quotes if q['Line'] == card['Line'] and q['Stat Key'] == card['Stat Key']]
        if matches:
            card['Sportsbook Quotes'] = matches
    return cards
