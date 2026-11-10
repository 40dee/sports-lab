"""One user-requested detailed regular-line repair, at most one quoted credit."""
import ast
from pathlib import Path
from urllib.parse import urlencode
source=Path('app.py').read_text();nodes=[]
for node in ast.parse(source).body:
    if isinstance(node,ast.If) and "'--rebuild-compact-fantasy'" in ast.get_source_segment(source,node.test):break
    nodes.append(node)
env={'__name__':'regular_line_repair','__file__':'app.py'}
exec(compile(ast.Module(body=nodes,type_ignores=[]),'app.py','exec'),env)
if not env['_database_online']:raise SystemExit('Permanent storage unavailable; no page requested.')
store=env['workflow_store'];wf=env['workflow'];day=env['slate_day']()
report={'checked_at':env['now'](),'requests':0,'state':'blocked'}
def finish():
    store.put('source','pp_regular_repair_report',report)
    print('REGULAR_LINE_REPAIR '+env['encode'](report))
health=env['check_prizepicks_source'](store)
try:remaining=int(health.get('remaining'))
except (TypeError,ValueError):remaining=0
if health.get('http_status')!=200 or health.get('mode')!='live' or health.get('usage_unit')!='credits' or remaining<1:
    report['reason']='Live account with at least one credit was not confirmed.';finish();raise SystemExit(0)
key=env['setting']('PRIZEPICKS_API_KEY','')
headers={'Authorization':'Bearer '+key,'X-User-Request':'Refresh current regular NBA Fantasy Score lines specifically requested by the user'}
requests=env['requests'];chosen=None
params={'league':'NBA','status':'active','stat':'Fantasy Score','tier':'standard','view':'compact','limit':100}
quote=requests.get('https://prizepicksapi.com/api/v1/credit-costs',headers=headers,
    params={'path':'/api/v1/projections?'+urlencode(params)},timeout=25)
if quote.status_code==200 and quote.json().get('quoted_credits')==1:chosen=params
if chosen is None:
    report['reason']='No regular-only compact page quoted at one credit; no download made.';finish();raise SystemExit(0)
budget=day+':pp_current_regular_fantasy_repair'
reserved=store.query('INSERT INTO sports_records VALUES (?,?,?,?) ON CONFLICT(namespace,key) DO NOTHING RETURNING key',
    ('request_budget',budget,'1',env['now']()),fetch=True)
if not reserved:
    report['reason']='The one-credit regular repair has already been reserved today.';finish();raise SystemExit(0)
response=requests.get('https://prizepicksapi.com/api/v1/projections',headers=headers,params=chosen,timeout=25)
report.update(requests=1,http_status=response.status_code,remaining=response.headers.get('X-Credits-Remaining'),cost=response.headers.get('X-Credits-Cost'))
if response.status_code!=200:
    report['reason']='Detailed regular-line source did not return a successful page.';finish();raise SystemExit(0)
body=response.json();meta=body.get('meta') or {}
if meta.get('mode')!='live' or not isinstance(body.get('data'),list):
    report['reason']='Live detailed data not confirmed.';finish();raise SystemExit(0)
fetched=env['now']()
items=[dict(env['normalize_pp_projection'](item,live_response=True),_sports_lab_fetched_at=fetched) for item in body['data']]
payload={'items':items,'fetched_at':fetched,'updated_at':fetched,'slate_day':day,'partial':bool(meta.get('next_cursor')),'filters':chosen,'verified_live_response':True}
store.put('source','pp_current_regular_fantasy_v1',payload)
report['source_records']=len(items)
report['explicit_regular_sides']=[{'player':(item.get('player') or {}).get('name'),'line':item.get('line'),
    'sides':[s.get('side') for s in item.get('selections') or [] if s.get('available') is True and s.get('tier')=='standard']} for item in items]
board=store.get('board','board') or {}
try:
    merged,count=env['pp_merge_tiers'](board.get('v4_rows') or [],items,fetched,fantasy_only=False)
    board['v4_rows']=merged;store.put('board','board',board);report['imported']=count
    compact=store.get('source','pp_fantasy_compact_v1') or {}
    combined=env['pp_cached_items']((compact,payload))
    store.put('source','pp_fantasy_compact_v1',dict(compact,items=combined,updated_at=fetched,slate_day=day))
    wf.store=env['CompactViewStore'](store)
    downloaded_ids={str(item['id']) for item in items}
    subset={'v4_rows':[c for c in merged if str(c.get('Tier Projection ID')) in downloaded_ids and str(c.get('Line Type','')).lower() in ('standard','regular')]}
    report['preparation']=env['prepare_saved_board'](wf,subset,budget_seconds=120,max_picks=30)
    report['combos']=env['update_combos'](wf.store,generate=True)
    pool,excluded=env['combo_pool'](wf.store,tier_mode='standard')
    report['regular_eligible']=len(pool);report['regular_excluded']=excluded
    report['regular_players']=[{'player':x['card']['Player'],'line':x['card']['Line'],'direction':x['direction']} for x in pool]
    report['state']='complete'
except (ValueError,TypeError,KeyError) as error:
    report['state']='Saved source; import or calculation needs review';report['reason']=str(error)
finish()
