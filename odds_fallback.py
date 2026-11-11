"""Quota-only provider failover with independent, atomically paced free credits."""
import calendar
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from zoneinfo import ZoneInfo
import requests
from the_odds_api import BASE, quota, stamp

MARKETS = {
    'player_points': ('Points','points'),
    'player_rebounds': ('Rebounds','rebounds'),
    'player_assists': ('Assists','assists'),
    'player_threes': ('3 Point FG','three_point_fg'),
    'player_points_rebounds_assists': ('Points + Assists + Rebounds','points_assists_rebounds'),
    'player_fantasy_points': ('Fantasy Score','fantasy_score'),
}
COST = len(MARKETS)
FANTASY_MARKETS = ('player_fantasy_points', 'player_fantasy_points_alternate')
BOARD_MARKETS = tuple(key for base in MARKETS for key in (base, base + '_alternate'))


def line_type(book, market_key, outcome):
    """Use The Odds API's documented PrizePicks encoding, never line size."""
    if not market_key.endswith('_alternate'):
        return 'Standard'
    if book.get('key') != 'prizepicks':
        return None
    price = outcome.get('price')
    if type(price) not in (int, float) or not math.isfinite(price):
        return None
    if price == 100:
        return 'Demon'
    base = market_key.removesuffix('_alternate')
    defaults = {o.get('price') for m in book.get('markets') or [] if m.get('key') == base
                for o in m.get('outcomes') or [] if type(o.get('price')) in (int, float)
                and math.isfinite(o['price']) and o['price'] < 0}
    return 'Goblin' if defaults == {price} else None



def primary_exhausted(account, budget):
    """Do not confuse daily pacing, transient failures or missing auth with exhaustion."""
    return bool(account and budget and int(budget.get('remaining', -1)) == 0)


def reserve(store, key, current, remaining_hint, cost=COST, daily_allowance=None):
    fingerprint=hashlib.sha256(key.encode()).hexdigest()[:20]
    month=current.strftime('%Y-%m')+':the_odds_api:'+fingerprint
    day=current.astimezone(ZoneInfo('America/Los_Angeles')).strftime('%Y-%m-%d')+':the_odds_api:'+fingerprint
    # The free subscription is limited to 500. Provider-reported use may only
    # raise local reservations; timeout/empty responses never refund a reservation.
    used=max(0,500-max(0,min(500,int(remaining_hint))))
    store.query('INSERT INTO sports_records VALUES (?,?,?,?) ON CONFLICT(namespace,key) DO UPDATE SET '
        'payload=CASE WHEN CAST(sports_records.payload AS INTEGER)<CAST(excluded.payload AS INTEGER) '
        'THEN excluded.payload ELSE sports_records.payload END RETURNING payload',
        ('request_budget',month,str(used),current.isoformat()),fetch=True)
    monthly=int(store.get('request_budget',month,used) or used)
    today=int(store.get('request_budget',day,0) or 0)
    days=calendar.monthrange(current.year,current.month)[1]-current.day+1
    daily_cap=min(24,max(0,(500-monthly+today)//max(1,days))) if daily_allowance is None else min(180,max(0,int(daily_allowance)))
    if monthly+cost>500 or today+cost>daily_cap:
        raise ValueError('Free fallback credit allowance reached; saved plays retained.')
    rows=store.query('INSERT INTO sports_records VALUES (?,?,?,?) ON CONFLICT(namespace,key) DO UPDATE SET '
        'payload=CAST(CAST(sports_records.payload AS INTEGER)+? AS TEXT),updated=excluded.updated '
        'WHERE CAST(sports_records.payload AS INTEGER)+?<=? RETURNING payload',
        ('request_budget',day,str(cost),current.isoformat(),cost,cost,daily_cap),fetch=True)
    if not rows:raise ValueError('Free fallback daily credit allowance reached; saved plays retained.')
    rows=store.query('UPDATE sports_records SET payload=CAST(CAST(payload AS INTEGER)+? AS TEXT),updated=? '
        'WHERE namespace=? AND key=? AND CAST(payload AS INTEGER)+?<=500 RETURNING payload',
        (cost,current.isoformat(),'request_budget',month,cost),fetch=True)
    if not rows:raise ValueError('Free fallback monthly credit allowance reached; saved plays retained.')
    return {'daily_cap':daily_cap,'reserved_today':today+cost,'reserved_month':int(rows[0][0])}


def decode(event, fetched, current):
    start=stamp(event.get('commence_time'))
    day=current.astimezone(ZoneInfo('America/Los_Angeles')).date()
    if not start or start<=current+timedelta(minutes=5) or start.astimezone(ZoneInfo('America/Los_Angeles')).date()!=day:
        return []
    grouped={}
    for book in event.get('bookmakers') or []:
        if not isinstance(book,dict):continue
        platform=book.get('key')
        if platform not in ('prizepicks','draftkings'):continue
        for market in book.get('markets') or []:
            if not isinstance(market,dict):continue
            market_key=market.get('key') or ''
            stat=MARKETS.get(market_key.removesuffix('_alternate'))
            observed=stamp(market.get('last_update'))
            if not stat or not observed or not 0<=(current-observed).total_seconds()<=1800:continue
            for outcome in market.get('outcomes') or []:
                if not isinstance(outcome,dict):continue
                side={'Over':'MORE','Under':'LESS'}.get(outcome.get('name'))
                player=outcome.get('description');line=outcome.get('point')
                try:
                    if isinstance(line,bool):continue
                    line=float(line)
                    if not math.isfinite(line) or not 0<line<=1000:continue
                except (ValueError,TypeError):continue
                if not side or not isinstance(player,str) or not player.strip():continue
                tier=line_type(book,market_key,outcome)
                if tier is None:continue
                identity=(platform,market['key'],player,line,tier)
                card=grouped.setdefault(identity,{
                    'Game':event['home_team']+' vs '+event['away_team'],
                    'Fixture ID':'theodds:'+str(event['id']),'Start (UTC)':event['commence_time'],
                    'Platform':platform,'Player':player,'Stat':stat[0],'Stat Key':stat[1],
                    'Period':'result','Line':line,'Raw Handicap':line,'Direction':side,
                    'Available Sides':[],'Line Type':tier,'Line Status':'Provider regular market' if tier=='Standard' else 'Provider encoded '+tier,
                    'Tier Source':'The Odds API documented encoding','Payout Multiplier':None,
                    'Odds':None,'Market ID':'theodds:'+market['key'],
                    'Outcome ID':'theodds:'+market['key']+':'+player,'Player ID':'','Decoded':True,
                    'Line Source':'The Odds API','Odds checked (UTC)':observed.isoformat(),
                    'Provider fetched at':fetched,'Provider Event ID':str(event['id'])})
                if side not in card['Available Sides']:card['Available Sides'].append(side)
    return list(grouped.values())


COVERAGE_VERSION = 3


def market_coverage(event, decoded, markets):
    """Only a successful, structurally valid PrizePicks response proves absence.

    Not supplied means absent from THIS provider snapshot, not proof that the
    underlying PrizePicks app never offered that market.
    """
    books=event.get('bookmakers')
    books=[b for b in books if isinstance(b,dict) and b.get('key')=='prizepicks'] if isinstance(books,list) else []
    valid=len(books)==1 and isinstance(books[0].get('markets'),list)
    raw=books[0]['markets'] if valid else []
    valid=valid and all(isinstance(m,dict) and isinstance(m.get('key'),str) for m in raw)
    counts={m:sum(c.get('Platform')=='prizepicks' and c.get('Market ID')=='theodds:'+m for c in decoded) for m in markets}
    states={}
    for key in markets:
        matches=[m for m in raw if isinstance(m,dict) and m.get('key')==key]
        if not valid:state='unverified_response'
        elif not matches:state='not_supplied_at_capture'
        elif len(matches)!=1 or not isinstance(matches[0].get('outcomes'),list):state='unusable_returned_market'
        elif not matches[0]['outcomes']:state='no_outcomes_at_capture'
        elif any(not isinstance(o,dict) for o in matches[0]['outcomes']):state='unusable_returned_market'
        elif counts[key]>0:state='available'
        else:state='unusable_returned_market'
        states[key]=state
    return counts,states


def finalize_coverage(report):
    coverage=report.get('game_coverage') or []
    total=report.get('games_available',0)
    resolved={'available','not_supplied_at_capture','no_outcomes_at_capture'}
    requested=report.get('requested_markets') or []
    report['coverage_version']=COVERAGE_VERSION
    report['complete']=bool(total) and len(coverage)==total and len({g.get('event_id') for g in coverage})==total and all(
        set(g.get('market_status') or {})==set(requested) and all(v in resolved for v in g['market_status'].values()) for g in coverage)
    report['markets_skipped']=sum(v in ('not_supplied_at_capture','no_outcomes_at_capture') for g in coverage for v in (g.get('market_status') or {}).values())
    report['markets_unresolved']=sum(v not in resolved for g in coverage for v in (g.get('market_status') or {}).values())
    return report


def reclassify_saved_coverage(store, report, profile, fingerprint, markets):
    # Recheck raw SAVED responses: no paid refresh and no upgraded timestamps.
    import copy
    result=copy.deepcopy(report)
    for game in result.get('game_coverage') or []:
        cached=store.get('source','the_odds_api_game_v2:'+profile+fingerprint+':'+str(game.get('event_id'))) or {}
        body=cached.get('event');captured=stamp(cached.get('fetched_at'))
        if not captured or not isinstance(body,dict) or str(body.get('id'))!=str(game.get('event_id')):
            game['market_status']={m:'unverified_response' for m in markets}
            continue
        rows=decode(body,cached['fetched_at'],captured)
        game['start']=body.get('commence_time')
        game['market_counts'],game['market_status']=market_coverage(body,rows,markets)
    return finalize_coverage(result)


def prune_unavailable_lines(rows, report):
    """Remove superseded quotes from this feed; frozen forecasts are separate."""
    absent={}
    for game in report.get('game_coverage') or []:
        start=stamp(game.get('start'))
        if start is None:continue
        for market,state in (game.get('market_status') or {}).items():
            if state in ('not_supplied_at_capture','no_outcomes_at_capture'):
                absent[(str(game.get('event_id')),'theodds:'+market)]=start
    kept=[]
    for card in rows:
        event_id=str(card.get('Provider Event ID') or str(card.get('Fixture ID','')).removeprefix('theodds:'))
        key=(event_id,str(card.get('Market ID')))
        if (card.get('Platform')=='prizepicks' and key in absent
                and stamp(card.get('Start (UTC)'))==absent[key]):
            continue
        kept.append(card)
    return kept


def download(store, api_key, current=None, get=None, markets=None):
    markets = tuple(markets or FANTASY_MARKETS)
    if not markets or any(m.removesuffix('_alternate') not in MARKETS for m in markets) or len(set(markets)) != len(markets):
        raise ValueError('Unsupported or duplicate fallback markets.')
    profile = '' if markets == FANTASY_MARKETS else hashlib.sha256(','.join(markets).encode()).hexdigest()[:12] + ':'
    current=current or datetime.now(timezone.utc);get=get or requests.get
    report={'provider':'The Odds API','checked_at':current.isoformat(),'state':'paused',
            'configured':bool(api_key),'http_requests':0,'games_requested':0,'games_available':0,
            'lines':0,'fantasy_lines':0,'complete':False,'requested_markets':list(markets),'game_coverage':[]}
    if not api_key:
        report['note']='THE_ODDS_API_KEY is missing from this runtime. Add the same key to GitHub Actions Secrets for background use.'
        return [],report
    if not getattr(store,'remote',False):
        report['note']='Permanent storage required for the free-credit guard.';return [],report
    fingerprint=hashlib.sha256(api_key.encode()).hexdigest()[:20]
    day=current.astimezone(ZoneInfo('America/Los_Angeles')).date().isoformat()
    snapshot_key='the_odds_api_daily_v2:'+profile+fingerprint+':'+day
    snapshot=store.get('source',snapshot_key) or {}
    if snapshot.get('captured') or snapshot.get('complete'):
        # The original quote timestamps remain unchanged. Reusing a saved
        # board must never make stale lines look freshly verified.
        saved_report=reclassify_saved_coverage(store,snapshot['report'],profile,fingerprint,markets)
        saved_report.update(http_requests=0,games_requested=0,reused_snapshot=True)
        store.put('source',snapshot_key,dict(snapshot,complete=saved_report['complete'],report=saved_report))
        store.put('source','the_odds_api_fallback_latest',saved_report)
        coverage=saved_report.get('game_coverage') or []
        store.put('board_download',day,{'day':day,'provider':'The Odds API','state':'saved snapshot',
            'updated_at':datetime.now(timezone.utc).isoformat(),'games_total':saved_report.get('games_available'),
            'games_done':len(coverage),'games_with_lines':sum(any(g.get('market_counts',{}).values()) for g in coverage),
            'lines':len(snapshot.get('rows') or [])})
        return snapshot.get('rows') or [],saved_report
    claimed=store.query(
        'INSERT INTO sports_records VALUES (?,?,?,?) ON CONFLICT(namespace,key) DO NOTHING RETURNING key',
        ('daily_board_capture',snapshot_key,'{}',current.isoformat()),fetch=True)
    if not claimed:
        report.update(state='capture_already_started',reused_snapshot=True,
            note='Daily download already started. Reusing saved lines; no provider request made.')
        recovered=[]
        prefix='the_odds_api_game_v2:'+profile+fingerprint+':%'
        for (payload,) in store.query('SELECT payload FROM sports_records WHERE namespace=? AND key LIKE ?',
                ('source',prefix),fetch=True):
            cached=json.loads(payload);captured=stamp(cached.get('fetched_at'))
            if captured and captured.astimezone(ZoneInfo('America/Los_Angeles')).date().isoformat()==day:
                recovered.extend(decode(cached.get('event') or {},cached['fetched_at'],captured))
        report.update(lines=len(recovered),fantasy_lines=sum(c['Stat Key']=='fantasy_score' for c in recovered))
        store.put('source','the_odds_api_fallback_latest',report)
        return recovered,report
    def progress(state='downloading', current_game=None):
        store.put('board_download',str(day),{'day':str(day),'provider':'The Odds API',
            'state':state,'updated_at':datetime.now(timezone.utc).isoformat(),
            'games_total':report['games_available'],'games_done':len(report['game_coverage']),
            'games_with_lines':sum(any(g['market_counts'].values()) for g in report['game_coverage']),
            'lines':sum(sum(g['market_counts'].values()) for g in report['game_coverage']),
            'current_game':current_game})
    status=store.get('source','the_odds_api_quota:'+fingerprint) or {}
    probe=store.get('source','the_odds_api_probe_latest') or {}
    remaining=status.get('credits_remaining',probe.get('credits_remaining',500))
    if stamp(status.get('checked_at')) and stamp(status['checked_at']).strftime('%Y-%m')!=current.strftime('%Y-%m'):remaining=500
    def request(path,params):
        nonlocal remaining
        report['http_requests']+=1
        try:response=get(BASE+path,params=dict(params,apiKey=api_key),timeout=20)
        except requests.RequestException:raise ValueError('Fallback connection unavailable; no automatic retry.') from None
        info=quota(response);report.update(info)
        if 'credits_remaining' in info:
            remaining=info['credits_remaining'];store.put('source','the_odds_api_quota:'+fingerprint,dict(info,checked_at=current.isoformat()))
        if response.status_code!=200:raise ValueError('Fallback returned HTTP '+str(response.status_code)+'. Saved plays retained.')
        try:return response.json()
        except ValueError:raise ValueError('Fallback returned invalid JSON; saved plays retained.') from None
    rows=[]
    try:
        # Event metadata costs no credits. Only this Pacific date is accepted.
        schedule_key='the_odds_api_schedule:'+fingerprint+':'+day
        cached=store.get('source',schedule_key) or {}
        fetched=stamp(cached.get('checked_at'))
        if fetched and fetched.astimezone(ZoneInfo('America/Los_Angeles')).date()==current.astimezone(ZoneInfo('America/Los_Angeles')).date():events=cached.get('events') or []
        else:
            events=request('/events',{'dateFormat':'iso'})
            if not isinstance(events,list):raise ValueError('Fallback schedule was not a game list.')
            store.put('source',schedule_key,{'checked_at':current.isoformat(),'events':events})
        day=current.astimezone(ZoneInfo('America/Los_Angeles')).date()
        games=[e for e in events if isinstance(e,dict) and stamp(e.get('commence_time'))
            and stamp(e['commence_time'])>current+timedelta(minutes=5)
            and stamp(e['commence_time']).astimezone(ZoneInfo('America/Los_Angeles')).date()==day
            and str(e.get('id','')).isalnum() and e.get('home_team') and e.get('away_team')]
        games.sort(key=lambda e:e['commence_time']);report['games_available']=len(games)
        progress()
        covered=0
        for event in games:
            progress(current_game=event['home_team']+' vs '+event['away_team'])
            cache_key='the_odds_api_game_v2:'+profile+fingerprint+':'+event['id']
            cached=store.get('source',cache_key) or {};fetched=stamp(cached.get('fetched_at'))
            if fetched and fetched.astimezone(ZoneInfo('America/Los_Angeles')).date()==day:
                body=cached['event'];captured=cached['fetched_at']
            else:
                report['budget']=reserve(store,api_key,current,remaining,cost=len(markets),daily_allowance=min(180,len(games)*len(markets)))
                body=request('/events/'+event['id']+'/odds',{'bookmakers':'prizepicks,draftkings',
                    'regions':'us_dfs','markets':','.join(markets),'oddsFormat':'american','dateFormat':'iso'})
                report['games_requested']+=1
                if not isinstance(body,dict) or body.get('id')!=event['id'] or {body.get('home_team'),body.get('away_team')}!={event['home_team'],event['away_team']}:
                    raise ValueError('Fallback game identity mismatch; response rejected.')
                captured=current.isoformat();store.put('source',cache_key,{'fetched_at':captured,'event':body})
            decoded=decode(body,captured,stamp(captured) or current);rows.extend(decoded)
            fantasy_count=sum(c['Stat Key']=='fantasy_score' for c in decoded)
            counts,states=market_coverage(body,decoded,markets)
            report['game_coverage'].append({'event_id':event['id'],'start':event['commence_time'],'game':event['home_team']+' vs '+event['away_team'],'fantasy_lines':fantasy_count,
                'market_counts':counts,'market_status':states})
            covered+=bool(fantasy_count)
            progress()
        report.update(state='received' if rows else 'no_current_lines')
    except ValueError as error:
        report.update(state='partial' if rows else 'paused',note=str(error))
    finalize_coverage(report)
    progress('received' if report.get('complete') else 'partial')
    report['lines']=len(rows);report['fantasy_lines']=sum(c['Stat Key']=='fantasy_score' for c in rows)
    report['note']=report.get('note') or ('Saved Standard and provider-encoded Goblin/Demon markets. Full Fantasy coverage is not guaranteed. Unsupported alternate metadata is excluded. Payouts are unavailable; provider prices are not executable odds.')
    store.put('source','the_odds_api_fallback_latest',report)
    report['captured_at']=current.isoformat()
    report['cache_policy']='One capture per Pacific slate date; no automatic paid refresh, including incomplete boards.'
    store.put('source',snapshot_key,{'captured':True,'complete':report['complete'],'rows':rows,'report':dict(report)})
    return rows,report


