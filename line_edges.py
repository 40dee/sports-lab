"""Display-only interpolation of already saved two-way sportsbook quotes."""
import json, math
from datetime import datetime
from statistics import NormalDist, pstdev
from sportsbook_capture import BOOKS, capture_quotes


def stamp(value):
    try:
        value=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return value if value.tzinfo else None
    except (ValueError,TypeError):return None


def saved_quotes(store, day, hour=None, include_cards=False):
    from odds_fallback import MARKETS
    from zoneinfo import ZoneInfo
    if callable(getattr(store,'query',None)):
        records=[json.loads(row[0]) for row in store.query('SELECT payload FROM sports_records WHERE namespace=? AND key LIKE ?',('source','the_odds_api_game_v2:%'),True)]
    else:records=[v for k,v in store.items('source') if k.startswith('the_odds_api_game_v2:')]
    rows=[];captures=[];cards=[]
    for record in records:
        fetched=stamp(record.get('fetched_at'));event=record.get('event') or {}
        if not fetched:continue
        local=fetched.astimezone(ZoneInfo('America/Los_Angeles'))
        if local.date().isoformat()!=day or (hour is not None and local.hour!=hour):continue
        if not all(event.get(k) for k in ('id','home_team','away_team','commence_time')):continue
        rows.extend(capture_quotes(event,fetched.isoformat(),fetched,MARKETS));captures.append(fetched.isoformat())
        if include_cards:
            from odds_fallback import decode
            cards.extend(decode(event,fetched.isoformat(),fetched))
    return (rows,sorted(set(captures)),cards) if include_cards else (rows,sorted(set(captures)))


def candidate_pairs(card, rows, compare):
    """Reuse exact identity, period, scoring, snapshot and two-way validation."""
    lines=set()
    for row in rows:
        try:
            line=float(row['Line'])
            if math.isfinite(line):lines.add(line)
        except (ValueError,TypeError,KeyError):pass
    pairs=[]
    for line in sorted(lines):
        pair=compare(dict(card,Line=line),rows)
        if not pair:continue
        books=[]
        for book in pair.get('books') or []:
            observed,captured=stamp(book.get('observed_at')),stamp(book.get('captured_at'))
            start=stamp(card.get('Start (UTC)'))
            if observed and captured and start and 0<=(captured-observed).total_seconds()<=1800 and captured<start:
                books.append(book)
        if books:
            # Do not combine materially separated snapshots into one consensus.
            if (max(stamp(b['captured_at']) for b in books)-min(stamp(b['captured_at']) for b in books)).total_seconds()>1800:continue
            pairs.append((line,books))
    return pairs


def detect(card, result, rows, compare, offered, columns, fantasy=False):
    if str(card.get('Platform','')).lower()!='prizepicks' or not offered:return None
    try:pp=float(card['Line'])
    except (ValueError,TypeError,KeyError):return None
    pairs=candidate_pairs(card,rows,compare)
    if any(line==pp for line,_ in pairs):return None
    options=[(line,books) for line,books in pairs if line!=pp and len({b['book'] for b in books})>=2]
    if not options:return None
    line,books=min(options,key=lambda v:(-len(v[1]),abs(v[0]-pp),v[0]))
    floors={'PTS':12.,'REB':6.,'AST':5.,'FG3M':3.,'STL':3.,'BLK':3.,'TOV':3.}
    floor=22. if fantasy else math.sqrt(sum(floors.get(c,12.)**2 for c in columns)) if columns else 12.
    values=[float(v) for v in result.get('recent_values') or [] if isinstance(v,(int,float)) and math.isfinite(v)]
    spread=max(floor,pstdev(values) if len(values)>=5 else 0)
    normal=NormalDist();p=sum(b['more'] for b in books)/len(books)
    if not 0<p<1:return None
    more=normal.cdf(normal.inv_cdf(p)+(line-pp)/spread)
    probabilities={'MORE':min(.65,more),'LESS':min(.65,1-more)}
    side=max(offered,key=lambda s:probabilities[s])
    probability=probabilities[side]
    if probability<.54:return None
    price=sum(b['over_price' if side=='MORE' else 'under_price'] for b in books)/len(books)
    # Never average American prices across the +/-100 discontinuity.
    prices=[b['over_price' if side=='MORE' else 'under_price'] for b in books]
    price=price if all(v<0 for v in prices) or all(v>0 for v in prices) else None
    return {'side':side,'probability':probability,'book_line':line,'pp_line':pp,'book_count':len(books),'price':price,'spread':spread,'books':books,'method':'heuristic-normal-interpolation-v1'}
