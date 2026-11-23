"""Display-only interpolation of already saved two-way sportsbook quotes."""
import json, math
from datetime import datetime
from statistics import NormalDist, pstdev
from sportsbook_capture import BOOKS, capture_quotes
from market_compare import BOOK_WEIGHTS


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
    pp_time=stamp(card.get('Tier Fetched At') if card.get('Tier Source')=='prizepicksapi.com' else card.get('Odds checked (UTC)'))
    if card.get('Tier Source')=='prizepicksapi.com':
        observed=stamp(card.get('Tier Observed At'))
        if observed and pp_time:pp_time=min(observed,pp_time)
    if not pp_time:return []
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
            if not captured or abs((captured-pp_time).total_seconds())>1800:continue
            if observed and captured and start and 0<=(captured-observed).total_seconds()<=1800 and captured<start:
                books.append(book)
        if books:
            # Do not combine materially separated snapshots into one consensus.
            if (max(stamp(b['captured_at']) for b in books)-min(stamp(b['captured_at']) for b in books)).total_seconds()>1800:continue
            pairs.append((line,books))
    return pairs



def poisson_more_at_line(mean, line):
    """P(X > line) for nonnegative integer X; integer lines exclude pushes."""
    if mean < 0 or not math.isfinite(mean):
        return None
    threshold=math.floor(line)
    if threshold < 0:
        return 1.0
    if mean == 0:
        return 0.0
    # Count props are low-volume; avoid unstable underflow for extreme means.
    if mean > 500:
        return None
    term=math.exp(-mean)
    cdf=term
    for k in range(1,threshold+1):
        term*=mean/k
        cdf+=term
    return min(1.0,max(0.0,1.0-cdf))


def poisson_interpolated_more(book_probability, book_line, pp_line):
    """Infer Poisson mean from the book's no-vig over probability, then move line.

    Skip integer book/PP lines: two-way no-vig probabilities can be conditional
    on a push, so treating them as unconditional probabilities is unsafe.
    """
    if (book_line < 0 or pp_line < 0
            or abs(book_line-round(book_line)) < 1e-6
            or abs(pp_line-round(pp_line)) < 1e-6):
        return None
    if not 0 < book_probability < 1:
        return None
    lo,hi=0.0,1.0
    while hi < 500:
        prob=poisson_more_at_line(hi,book_line)
        if prob is None:
            return None
        if prob >= book_probability:
            break
        hi*=2
    else:
        return None
    for _ in range(55):
        mid=(lo+hi)/2
        if poisson_more_at_line(mid,book_line) < book_probability:
            lo=mid
        else:
            hi=mid
    return poisson_more_at_line((lo+hi)/2,pp_line)


def detect(card, result, rows, compare, offered, columns, fantasy=False):
    if str(card.get('Platform','')).lower()!='prizepicks' or not offered:return None
    try:pp=float(card['Line'])
    except (ValueError,TypeError,KeyError):return None
    if not math.isfinite(pp):return None
    integer_line=abs(pp-round(pp))<1e-6
    pairs=candidate_pairs(card,rows,compare)
    if any(line==pp for line,_ in pairs):return None
    # Half-point neighbors of an integer PP line overlap its push band.
    # Require a full point of separation before estimating a moved-line edge.
    options=[(line,books) for line,books in pairs if line!=pp
             and (not integer_line or abs(line-pp)>=1.0)
             and len({b['book'] for b in books})>=2]
    if not options:return None
    line,books=min(options,key=lambda v:(-len(v[1]),abs(v[0]-pp),v[0]))
    floors={'PTS':9.5,'REB':4.5,'AST':4.0,'FG3M':3.,'STL':3.,'BLK':3.,'TOV':3.}
    floor=24. if fantasy else math.sqrt(sum(floors.get(c,12.)**2 for c in columns)) if columns else 13.
    values=[float(v) for v in result.get('recent_values') or [] if isinstance(v,(int,float)) and math.isfinite(v)]
    spread=max(floor,pstdev(values) if len(values)>=5 else 0)
    weights=[BOOK_WEIGHTS.get(b['book'],1.0) for b in books]
    p=sum(b['more']*w for b,w in zip(books,weights))/sum(weights)
    if not 0<p<1:return None
    # Single low-count stats use a discrete Poisson survival probability.
    # Mixed/combined props and higher-volume stats retain normal interpolation.
    stat=str(columns[0]).upper() if not fantasy and len(columns)==1 else ''
    discrete=stat in {'FG3M','STL','BLK','TOV'}
    if discrete:
        more=poisson_interpolated_more(p,line,pp)
        if more is None:return None
        method='poisson-moved-line-v2'
    else:
        normal=NormalDist()
        more=normal.cdf(normal.inv_cdf(p)+(line-pp)/spread)
        method='heuristic-normal-interpolation-v2'
    probabilities={'MORE':more,'LESS':1-more}
    side=max(offered,key=lambda s:probabilities[s])
    probability=probabilities[side]
    if probability<.54:return None
    price=sum(b['over_price' if side=='MORE' else 'under_price'] for b in books)/len(books)
    # Never average American prices across the +/-100 discontinuity.
    prices=[b['over_price' if side=='MORE' else 'under_price'] for b in books]
    price=price if all(v<0 for v in prices) or all(v>0 for v in prices) else None
    return {'side':side,'probability':probability,'book_line':line,'pp_line':pp,'book_count':len(books),'price':price,'spread':spread,'books':books,'method':method}
