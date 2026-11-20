"""Display-only cohorts. Select before outcome filtering; never rewrite grades."""
from datetime import datetime,timezone
import math
import re,unicodedata

def normalized(value):
    value=unicodedata.normalize("NFKD",str(value or "")).encode("ascii","ignore").decode().lower()
    return re.sub("[^a-z0-9]","",value)

def timestamp(value):
    try:
        stamp=datetime.fromisoformat(str(value).replace("Z","+00:00"))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo else None
    except (ValueError,TypeError):
        return None

def standard_playable_rows(rows):
    candidates=[]
    for row in rows:
        if str(row.get("Platform","")).lower()!="prizepicks":continue
        if str(row.get("Line type","")).lower() not in ("standard","regular"):continue
        if row.get("Selectable") is not True:continue
        created,start=timestamp(row.get("Created (UTC)")),timestamp(row.get("Start (UTC)"))
        if not created or not start or created>=start:continue
        if row.get("Outcome") in ("WIN","LOSS") and row.get("Grade status")!="FINAL":continue
        if not row.get("Event") or not row.get("Stat columns") or not row.get("Player"):continue
        candidates.append(row)
    candidates.sort(key=lambda r:(timestamp(r["Created (UTC)"]),str(r["ID"])))
    kept={}
    for row in candidates:
        signature=(str(row["Event"]),normalized(row["Player"]),tuple(sorted(row["Stat columns"])),
                   str(row.get("Period") or "fullgame").lower(),"prizepicks")
        kept.setdefault(signature,row)
    return list(kept.values())


def results_breakdowns(rows):
    """Read-only settled diagnostics from the already-selected Results cohort."""
    directions = {}
    confidence = {}
    labels = ('Below 50%', '50–54.9%', '55–59.9%', '60–64.9%',
              '65–69.9%', '70%+', 'Unavailable')
    for row in rows:
        created, start = timestamp(row.get('Created (UTC)')), timestamp(row.get('Start (UTC)'))
        direction = row.get('Pick')
        outcome = row.get('Outcome')
        if (row.get('Grade status') != 'FINAL' or outcome not in ('WIN', 'LOSS')
                or direction not in ('MORE', 'LESS') or not created or not start or created >= start):
            continue
        raw = row.get('Estimated hit probability')
        try:
            probability = float(raw) if not isinstance(raw, bool) else None
        except (TypeError, ValueError):
            probability = None
        if probability is not None and (not math.isfinite(probability) or not 0 <= probability <= 1):
            probability = None
        if probability is None:
            bucket = 'Unavailable'
        else:
            bucket = labels[sum(probability >= bound for bound in (.5, .55, .6, .65, .7))]
        for groups, key in ((directions, direction), (confidence, bucket)):
            counts = groups.setdefault(key, {'wins': 0, 'losses': 0, 'estimates': []})
            counts['wins' if outcome == 'WIN' else 'losses'] += 1
            if probability is not None:
                counts['estimates'].append(probability)

    def table(groups, order, field):
        output = []
        for key in order:
            if key not in groups:
                continue
            counts = groups[key]
            total = counts['wins'] + counts['losses']
            estimates = counts['estimates']
            output.append({field: key, 'Settled picks': total, 'Wins': counts['wins'],
                           'Losses': counts['losses'], 'Hit rate': counts['wins'] / total,
                           'Estimates available': len(estimates),
                           'Average estimate': sum(estimates) / len(estimates) if estimates else None})
        return output
    return {'direction': table(directions, ('MORE', 'LESS'), 'Direction'),
            'confidence': table(confidence, labels, 'Saved confidence')}



def sharp_market_breakdowns(rows):
    """Read-only exact-line records; sharp cohorts settle on the offered sharp side."""
    from market_compare import BOOK_WEIGHTS

    labels = ('🔥 Sharp +EV (54%+ · sharp side)',
              '✓ Sharp aligned (53.5%+ · model side)',
              '🎯 Pinnacle-backed (53.5%+ · sharp side)',
              '⚠️ Sharp opposition (<49% · model side)')
    counts = [[0, 0] for _ in labels]

    def number(value):
        if isinstance(value, bool):
            return None
        try:
            value = float(value)
            return value if math.isfinite(value) else None
        except (TypeError, ValueError, OverflowError):
            return None

    def probabilities(market):
        if not isinstance(market, dict):
            return None
        more, less = number(market.get('more')), number(market.get('less'))
        if (more is None or less is None or not 0 <= more <= 1 or not 0 <= less <= 1
                or abs(more + less - 1) > 1e-6):
            return None
        return more, less

    for row in rows:
        created, start = timestamp(row.get('Created (UTC)')), timestamp(row.get('Start (UTC)'))
        actual, line = number(row.get('Actual')), number(row.get('Line'))
        if (row.get('Grade status') != 'FINAL' or row.get('Outcome') not in ('WIN', 'LOSS')
                or not created or not start or created >= start
                or actual is None or line is None or actual == line):
            continue
        market = row.get('Market')
        pair = probabilities(market)
        if not pair or market.get('match_method') != 'teams-time-player-stat-period-line':
            continue
        pinnacle = market.get('book') == 'pinnacle'
        if 'books' in market:
            books = market['books']
            if not isinstance(books, list) or not books:
                continue
            pairs = [probabilities(book) for book in books]
            if any(pair is None for pair in pairs):
                continue
            weights = [BOOK_WEIGHTS.get(book.get('book'), 1.0) for book in books]
            more = sum(pair[0] * weight for pair, weight in zip(pairs, weights)) / sum(weights)
            pair = more, 1 - more
            pinnacle = pinnacle or any(book.get('book') == 'pinnacle' for book in books)
        probs = dict(zip(('MORE', 'LESS'), pair))
        offered = row.get('Available sides')
        if not (isinstance(offered, (list, tuple)) and all(side in ('MORE', 'LESS') for side in offered)):
            offered = (('MORE', 'LESS') if str(row.get('Platform', '')).lower() == 'prizepicks'
                       and str(row.get('Line type', '')).lower() in ('standard', 'regular') else ())
        best_side = max(offered, key=probs.get) if offered else None
        best_prob = probs[best_side] if best_side else None
        model_side = row.get('Pick')
        sharp_win = (actual > line) == (best_side == 'MORE')
        model_win = row['Outcome'] == 'WIN'
        memberships = (best_prob is not None and best_prob >= .54,
                       model_side in offered and probs[model_side] >= .535,
                       pinnacle and best_prob is not None and best_prob >= .535,
                       model_side in probs and probs[model_side] < .49)
        for index, member in enumerate(memberships):
            if member:
                counts[index][0] += 1
                counts[index][1] += int(sharp_win if index in (0, 2) else model_win)
    return [{'Category': label, 'Settled': total, 'Wins': wins,
             'Losses': total - wins, 'Hit rate': wins / total}
            for label, (total, wins) in zip(labels, counts) if total]
