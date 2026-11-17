"""Learn pregame feature reliability from settled picks, without rewriting forecasts.

Scores are smoothed historical associations, not calibrated hit probabilities.
All validation fits earlier Pacific dates and evaluates a later date.
"""
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

FEATURES = ('market_direction', 'tier', 'history', 'minutes', 'gap', 'adjustment', 'pace', 'rate', 'position_adjustment')
VERSION = 'green-red-reliability-v1'


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError, OverflowError):
        return None


def band(value, boundaries, labels):
    if value is None:
        return 'unknown'
    for boundary, label in zip(boundaries, labels):
        if value < boundary:
            return label
    return labels[-1]


def features(card, result):
    projection = number(result.get('projection'))
    baseline = number(result.get('baseline'))
    relative = None if projection is None or baseline is None or baseline <= 0 else projection / baseline - 1
    minutes = number(result.get('minutes'))
    rate = baseline / minutes if baseline is not None and minutes is not None and minutes > 0 else None
    components = result.get('components') or []
    pace = [number(c.get('Pace factor')) for c in components]
    pace = [p for p in pace if p is not None]
    positions = [number(c.get('Position factor')) for c in components]
    positions = [p for p in positions if p is not None]
    return {
        'market_direction': str(result.get('stat_group') or card.get('Stat Key')) + ':' + str(result.get('direction')),
        'tier': str(card.get('Line Type') or 'unverified').lower(),
        'history': band(number(result.get('ranking_games')), (5, 10, 15), ('under5', '5-9', '10-14', '15+')),
        'minutes': band(number(result.get('minutes')), (15, 25, 32), ('under15', '15-24', '25-31', '32+')),
        'gap': band(number(result.get('standardized_gap')), (.5, 1, 1.5), ('under0.5', '0.5-0.99', '1-1.49', '1.5+')),
        'adjustment': band(relative, (-.05, .05), ('below-5pct', 'within5pct', 'above5pct')),
        'rate': str(result.get('stat_group') or card.get('Stat Key')) + ':' + band(rate, (.05, .2, .5, 1, 2), ('under0.05', '0.05-0.19', '0.2-0.49', '0.5-0.99', '1-1.99', '2+')),
        'position_adjustment': band(sum(positions) / len(positions) if positions else None, (.95, 1.05), ('below0.95', 'neutral', 'above1.05')),
        'pace': band(sum(pace) / len(pace) if pace else None, (.98, 1.02), ('slower', 'neutral', 'faster')),
    }


def examples(records, grades):
    """One player-game/market of weight, shared across its alternate thresholds."""
    rows = []
    seen = set()
    excluded = Counter()
    ordered = sorted(records, key=lambda item: str(item[1].get('created_at', '')))
    for key, frozen in ordered:
        card = frozen.get('card') or {}
        result = frozen.get('result') or {}
        grade = grades.get(key) or {}
        if str(card.get('Platform', '')).lower() != 'prizepicks':
            excluded['other_platform'] += 1
            continue
        if grade.get('status') != 'FINAL' or grade.get('outcome') not in ('WIN', 'LOSS'):
            excluded['not_decided_final'] += 1
            continue
        start, created = timestamp(card.get('Start (UTC)')), timestamp(frozen.get('created_at'))
        if start is None or created is None or created >= start:
            excluded['not_verified_pregame'] += 1
            continue
        direction = result.get('direction')
        line, actual, projection = number(card.get('Line')), number(grade.get('actual')), number(result.get('projection'))
        if direction not in ('MORE', 'LESS') or None in (line, actual, projection) or actual == line:
            excluded['invalid_direction_or_numbers'] += 1
            continue
        correct = actual > line if direction == 'MORE' else actual < line
        if ('WIN' if correct else 'LOSS') != grade['outcome']:
            excluded['inconsistent_saved_grade'] += 1
            continue
        event = str(frozen.get('event_id') or '')
        player = str(result.get('player_id') or '')
        market = str(result.get('stat_group') or '')
        if not event or not player or not market:
            excluded['missing_identity'] += 1
            continue
        identity = (event, player, market, line)
        if identity in seen:
            excluded['duplicate_threshold'] += 1
            continue
        seen.add(identity)
        side_values = card.get('Available Sides')
        offered = (direction in side_values if isinstance(side_values, (list, tuple))
                   else str(card.get('Line Type', '')).lower() in ('standard', 'regular'))
        rows.append(dict(id=key, day=start.astimezone(ZoneInfo('America/Los_Angeles')).date().isoformat(),
            event=event, player=player, market=market, correct=int(correct), features=features(card, result),
            selectable=offered, gap=number(result.get('standardized_gap')) or 0,
            history=number(result.get('ranking_games')) or 0))
    counts = Counter((r['event'], r['player'], r['market']) for r in rows)
    for row in rows:
        row['weight'] = 1 / counts[(row['event'], row['player'], row['market'])]
    return rows, dict(excluded)


def fit(rows):
    total = sum(r['weight'] for r in rows)
    wins = sum(r['weight'] * r['correct'] for r in rows)
    prior = (wins + 1) / (total + 2)
    cells = {feature: {} for feature in FEATURES}
    for row in rows:
        for feature in FEATURES:
            cell = cells[feature].setdefault(row['features'][feature], {'weight': 0., 'wins': 0.})
            cell['weight'] += row['weight']
            cell['wins'] += row['weight'] * row['correct']
    return dict(version=VERSION, trained_through=max((r['day'] for r in rows), default=None),
                effective_examples=total, prior=prior, cells=cells, status='shadow')


def score(model, values):
    # Shrink every pattern toward the overall record; small cells stay uncertain.
    prior = model.get('prior', .5)
    weighted = []
    for feature in FEATURES:
        cell = (model.get('cells', {}).get(feature) or {}).get(values.get(feature))
        estimate = prior if not cell else (cell['wins'] + 20 * prior) / (cell['weight'] + 20)
        weight = 2 if feature == 'market_direction' else 1
        weighted.extend([estimate] * weight)
    return sum(weighted) / len(weighted)


def eligible(rows):
    return [r for r in rows if r['selectable'] and r['history'] >= 5 and r['gap'] >= .25
            and r['features']['tier'] in ('standard', 'regular')]


def top_players(rows, rank, limit=6):
    chosen = []
    players = set()
    for row in sorted(rows, key=lambda r: (-rank(r), r['id'])):
        identity = (row['event'], row['player'])
        if identity in players:
            continue
        players.add(identity)
        chosen.append(row)
        if len(chosen) == limit:
            break
    return chosen


def evaluate(rows):
    days = sorted({r['day'] for r in rows})
    folds = []
    for day in days[2:]:
        train = eligible([r for r in rows if r['day'] < day])
        test = eligible([r for r in rows if r['day'] == day])
        model = fit(train)
        weight = sum(r['weight'] for r in test)
        if not weight:
            continue
        baseline_brier = sum(r['weight'] * (model['prior'] - r['correct']) ** 2 for r in test) / weight
        candidate_brier = sum(r['weight'] * (score(model, r['features']) - r['correct']) ** 2 for r in test) / weight
        pool = eligible(test)
        legacy = top_players(pool, lambda r: min(1.5, r['gap']))
        candidate = top_players(pool, lambda r: score(model, r['features']))
        folds.append(dict(day=day, trained_through=model['trained_through'],
            effective_examples=weight, candidate_brier=candidate_brier, baseline_brier=baseline_brier,
            top_count=len(candidate), candidate_top_accuracy=sum(r['correct'] for r in candidate) / len(candidate) if candidate else None,
            gap_top_accuracy=sum(r['correct'] for r in legacy) / len(legacy) if legacy else None))
    return folds


def learn(records, grades):
    rows, excluded = examples(records, grades)
    profile = fit(rows)
    ranking_rows = eligible(rows)
    model = fit(ranking_rows)
    folds = evaluate(rows)
    patterns = []
    for feature, values in profile['cells'].items():
        for value, cell in values.items():
            patterns.append(dict(feature=feature, pattern=value, effective_examples=round(cell['weight'], 3),
                weighted_greens=round(cell['wins'], 3), weighted_reds=round(cell['weight']-cell['wins'], 3),
                historical_accuracy=cell['wins']/cell['weight'],
                shrunk_accuracy=(cell['wins']+20*model['prior'])/(cell['weight']+20)))
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:16]
    report = dict(version=VERSION, status='evaluated' if folds else 'collecting_dates',
        examples=len(rows), effective_examples=profile['effective_examples'], ranking_examples=len(ranking_rows), ranking_effective_examples=model['effective_examples'], graded_dates=len({r['day'] for r in rows}),
        last_graded_date=profile['trained_through'], input_digest=digest, excluded=excluded,
        patterns=patterns, folds=folds, activation='shadow_only',
        scope='All settled PrizePicks markets, including Fantasy. Never changes frozen MORE/LESS predictions.',
        method='Pregame features only. Earlier Pacific dates train; later dates test. Alternate thresholds share player-game/market weight. Ranking trains and tests only selectable Standard rows meeting the history/gap checks. All-projection patterns also include passes. Top-six diagnostic uses distinct player-games, not full combo constraints or payouts.')
    return model, report
