"""Explicit Nov 17 one-time quote repull. Preserve forecasts, played entries and usage."""
import ast,hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
source=Path('app.py').read_text();nodes=[]
for node in ast.parse(source).body:
    if isinstance(node,ast.If) and "'--rebuild-compact-fantasy'" in (ast.get_source_segment(source,node.test) or ''):break
    nodes.append(node)
env={'__name__':'authorized_repull','__file__':'app.py'}
exec(compile(ast.Module(body=nodes,type_ignores=[]),'app.py','exec'),env)
wf=env['workflow'];store=wf.store;day=env['slate_day']()
if day!='2026-11-17':raise SystemExit('This authorization is for November 17 only.')
if not env['_database_online'] or not store.remote:raise SystemExit('Permanent storage required.')
if not env['setting']('THE_ODDS_API_KEY','') or not env['setting']('PRIZEPICKS_API_KEY',''):raise SystemExit('Both keys required.')
marker='2026-11-17:user_requested_repull_v1';stamp=env['now']()
claimed=store.query('INSERT INTO sports_records VALUES (?,?,?,?) ON CONFLICT(namespace,key) DO NOTHING RETURNING key',
    ('authorized_repull',marker,env['encode']({'state':'started','requested_at':stamp}),stamp),fetch=True)
if not claimed:raise SystemExit('This one-time repull was already attempted; no repeated paid requests.')
import odds_fallback
from daily_capture import close_capture
key=env['setting']('THE_ODDS_API_KEY','');fingerprint=hashlib.sha256(key.encode()).hexdigest()[:20]
markets=tuple(m for m in odds_fallback.BOARD_MARKETS if m not in odds_fallback.FANTASY_MARKETS)
profile=hashlib.sha256(','.join(markets).encode()).hexdigest()[:12]+':'
snapshot='the_odds_api_daily_v2:'+profile+fingerprint+':'+day
budget_key=day+':the_odds_api:'+fingerprint
initial_odds=int(store.get('request_budget',budget_key,0) or 0)
initial_pp=int(store.get('request_budget',day+':prizepicks',0) or 0)
original_reserve=odds_fallback.reserve
def bounded_reserve(store_arg,key_arg,current,remaining_hint,cost=odds_fallback.COST,daily_allowance=None):
    used=int(store.get('request_budget',budget_key,0) or 0)-initial_odds
    if used+cost>50:raise ValueError('One-time repull reached its additional 50-credit limit.')
    return original_reserve(store_arg,key_arg,current,remaining_hint,cost=cost,daily_allowance=initial_odds+50)
original_prepare=env['prepare_saved_board']
def prepare_after_capture(*args,**kwargs):
    close_capture(store,day,env['now']())
    return original_prepare(*args,**kwargs)
try:
    # Archive only the quote caches and capture claims that must be renewed.
    sources=['the_odds_api_fallback_latest',snapshot,'pp_fantasy_daily_v1']
    for (cache_key,payload) in store.query('SELECT key,payload FROM sports_records WHERE namespace=? AND key LIKE ?',
            ('source','the_odds_api_game_v2:'+profile+fingerprint+':%'),fetch=True):
        value=json.loads(payload);fetched=env['utc_timestamp'](value.get('fetched_at'))
        if env['pd'].notna(fetched) and env['slate_day'](fetched)==day:sources.append(cache_key)
    for ns,record_key in [('source',k) for k in sources]+[('daily_board_capture',snapshot)]:
        value=store.get(ns,record_key)
        if value is not None:
            archive_key=hashlib.sha256((marker+ns+record_key).encode()).hexdigest()
            store.put('repull_archive',archive_key,{'namespace':ns,'key':record_key,'payload':value,'archived_at':stamp},immutable=True)
            store.query('DELETE FROM sports_records WHERE namespace=? AND key=?',(ns,record_key))
    # Invalidate only this process's read cache after the archived quote deletions.
    cache=getattr(store,'_read_cache',None)
    if isinstance(cache,dict):cache.clear()
    previous_capture=store.get('daily_board_capture',day) or {}
    store.put('repull_archive',marker,{'previous_capture':previous_capture,'board':store.get('board','board'),
        'initial_odds_reserved':initial_odds,'initial_fantasy_reserved':initial_pp},immutable=True)
    store.put('daily_board_capture',day,{'state':'capturing','started_at':stamp,'authorization':marker})
    odds_fallback.reserve=bounded_reserve
    env['feed_request_cap']=lambda:initial_pp+3
    env['prepare_saved_board']=prepare_after_capture
    result=env['run_quota_fallback'](wf,budget_seconds=None)
    close_capture(store,day,env['now']())
    research=env['update_combos'](store,generate=True,quote_mode='research')
    result['research_combos']=research
    result['additional_odds_reserved']=int(store.get('request_budget',budget_key,0) or 0)-initial_odds
    result['additional_fantasy_reserved']=int(store.get('request_budget',day+':prizepicks',0) or 0)-initial_pp
    store.put('authorized_repull',marker,{'state':'finished','requested_at':stamp,'finished_at':env['now'](),
        'additional_odds_reserved':result['additional_odds_reserved'],
        'additional_fantasy_reserved':result['additional_fantasy_reserved'],
        'coverage':result.get('coverage'),'errors':result.get('errors')})
    print('AUTHORIZED_REPULL_RESULT',env['encode'](result),flush=True)
finally:
    close_capture(store,day,env['now']())
    odds_fallback.reserve=original_reserve
    env['prepare_saved_board']=original_prepare
