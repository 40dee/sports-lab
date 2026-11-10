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
payload=store.get('source','pp_current_regular_fantasy_v1') or {}
items=payload.get('items') or []
if not payload.get('verified_live_response') or not items:
    report['reason']='Verified saved regular response unavailable.';finish();raise SystemExit(0)
board=store.get('board','board') or {}
merged,count=env['pp_merge_tiers'](board.get('v4_rows') or [],items,payload['fetched_at'],fantasy_only=True)
board['v4_rows']=merged;store.put('board','board',board)
wf.store=env['CompactViewStore'](store)
ids={str(item['id']) for item in items}
subset={'v4_rows':[c for c in merged if str(c.get('Tier Projection ID')) in ids and str(c.get('Line Type','')).lower()=='standard']}
report['imported']=count
report['preparation']=env['prepare_saved_board'](wf,subset,budget_seconds=60,max_picks=12)
report['state']='complete';report['provider_requests']=0
index=env['forecast_index'](wf.store.items('forecast'))
report['regular_lines']=[{'player':c['Player'],'line':c['Line'],'sides':c.get('Available Sides'),'projection':((env['saved_prediction'](index,c) or {}).get('result') or {}).get('projection'),'direction':((env['saved_prediction'](index,c) or {}).get('result') or {}).get('direction')} for c in subset['v4_rows'] if env['today_pregame'](c.get('Start (UTC)'))]
finish()
