"""Quota-only provider failover with independent, atomically paced free credits."""
import calendar
from datetime import datetime, timedelta, timezone
import hashlib
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


def primary_exhausted(account, budget):
    """Do not confuse daily pacing, transient failures or missing auth with exhaustion."""
    return bool(account and budget and int(budget.get('remaining', -1)) == 0)


def reserve(store, key, current, remaining_hint):
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
    daily_cap=min(24,max(0,(500-monthly+today)//max(1,days)))
    if monthly+COST>500 or today+COST>daily_cap:
        raise ValueError('Free fallback credit allowance reached; saved plays retained.')
    rows=store.query('INSERT INTO sports_records VALUES (?,?,?,?) ON CONFLICT(namespace,key) DO UPDATE SET '
        'payload=CAST(CAST(sports_records.payload AS INTEGER)+? AS TEXT),updated=excluded.updated '
        'WHERE CAST(sports_records.payload AS INTEGER)+?<=? RETURNING payload',
        ('request_budget',day,str(COST),current.isoformat(),COST,COST,daily_cap),fetch=True)
    if not rows:raise ValueError('Free fallback daily credit allowance reached; saved plays retained.')
    rows=store.query('UPDATE sports_records SET payload=CAST(CAST(payload AS INTEGER)+? AS TEXT),updated=? '
        'WHERE namespace=? AND key=? AND CAST(payload AS INTEGER)+?<=500 RETURNING payload',
        (COST,current.isoformat(),'request_budget',month,COST),fetch=True)
    if not rows:raise ValueError('Free fallback monthly credit allowance reached; saved plays retained.')
    return {'daily_cap':daily_cap,'reserved_today':today+COST,'reserved_month':int(rows[0][0])}


def decode(event, fetched, current):
    start=stamp(event.get('commence_time'))
    day=current.astimezone(ZoneInfo('America/Los_Angeles')).date()
    if not start or start<=current+timedelta(minutes=5) or start.astimezone(ZoneInfo('America/Los_Angeles')).date()!=day:
        return []
    grouped={}
    for book in event.get('bookmakers') or []:
        platform=book.get('key')
        if platform not in ('prizepicks','draftkings'):continue
        for market in book.get('markets') or []:
            stat=MARKETS.get(market.get('key'))
            observed=stamp(market.get('last_update'))
            if not stat or not observed or not 0<=(current-observed).total_seconds()<=1800:continue
            for outcome in market.get('outcomes') or []:
                side={'Over':'MORE','Under':'LESS'}.get(outcome.get('name'))
                player=outcome.get('description');line=outcome.get('point')
                try:
                    if isinstance(line,bool):continue
                    line=float(line)
                    if not math.isfinite(line) or not 0<line<=1000:continue
                except (ValueError,TypeError):continue
                if not side or not isinstance(player,str) or not player.strip():continue
                identity=(platform,market['key'],player,line)
                card=grouped.setdefault(identity,{
                    'Game':event['home_team']+' vs '+event['away_team'],
                    'Fixture ID':'theodds:'+str(event['id']),'Start (UTC)':event['commence_time'],
                    'Platform':platform,'Player':player,'Stat':stat[0],'Stat Key':stat[1],
                    'Period':'result','Line':line,'Raw Handicap':line,'Direction':side,
                    'Available Sides':[],'Line Type':'Standard','Line Status':'Provider regular market',
                    'Odds':None,'Market ID':'theodds:'+market['key'],
                    'Outcome ID':'theodds:'+market['key']+':'+player,'Player ID':'','Decoded':True,
                    'Line Source':'The Odds API','Odds checked (UTC)':observed.isoformat(),
                    'Provider fetched at':fetched,'Provider Event ID':str(event['id'])})
                if side not in card['Available Sides']:card['Available Sides'].append(side)
    return list(grouped.values())


def download(store, api_key, current=None, get=None):
    current=current or datetime.now(timezone.utc);get=get or requests.get
    report={'provider':'The Odds API','checked_at':current.isoformat(),'state':'paused',
            'configured':bool(api_key),'http_requests':0,'games_requested':0,'games_available':0,
            'lines':0,'fantasy_lines':0,'complete':False}
    if not api_key:
        report['note']='THE_ODDS_API_KEY is missing from this runtime. Add the same key to GitHub Actions Secrets for background use.'
        return [],report
    if not getattr(store,'remote',False):
        report['note']='Permanent storage required for the free-credit guard.';return [],report
    fingerprint=hashlib.sha256(api_key.encode()).hexdigest()[:20]
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
        schedule_key='the_odds_api_schedule:'+fingerprint
        cached=store.get('source',schedule_key) or {}
        fetched=stamp(cached.get('checked_at'))
        if fetched and 0<=(current-fetched).total_seconds()<1800:events=cached.get('events') or []
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
        covered=0
        for event in games:
            cache_key='the_odds_api_game:'+fingerprint+':'+event['id']
            cached=store.get('source',cache_key) or {};fetched=stamp(cached.get('fetched_at'))
            if fetched and 0<=(current-fetched).total_seconds()<1800:
                body=cached['event'];captured=cached['fetched_at']
            else:
                report['budget']=reserve(store,api_key,current,remaining)
                body=request('/events/'+event['id']+'/odds',{'bookmakers':'prizepicks,draftkings',
                    'regions':'us_dfs','markets':','.join(MARKETS),'oddsFormat':'american','dateFormat':'iso'})
                report['games_requested']+=1
                if not isinstance(body,dict) or body.get('id')!=event['id'] or {body.get('home_team'),body.get('away_team')}!={event['home_team'],event['away_team']}:
                    raise ValueError('Fallback game identity mismatch; response rejected.')
                captured=current.isoformat();store.put('source',cache_key,{'fetched_at':captured,'event':body})
            decoded=decode(body,captured,current);rows.extend(decoded);covered+=bool(decoded)
        report.update(state='received' if rows else 'no_current_lines',complete=bool(games) and covered==len(games))
    except ValueError as error:
        report.update(state='partial' if rows else 'paused',note=str(error))
    report['lines']=len(rows);report['fantasy_lines']=sum(c['Stat Key']=='fantasy_score' for c in rows)
    report['note']=report.get('note') or ('Saved regular markets only; full Fantasy coverage is not guaranteed. Alternate tiers are not inferred.')
    store.put('source','the_odds_api_fallback_latest',report)
    return rows,report
