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

