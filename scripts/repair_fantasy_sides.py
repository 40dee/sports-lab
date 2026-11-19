"""Inspect or reprocess saved Fantasy transport without provider requests."""
import ast,sys,json
from pathlib import Path
from collections import Counter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
source=Path('app.py').read_text();nodes=[]
for node in ast.parse(source).body:
    if isinstance(node,ast.If) and "'--rebuild-compact-fantasy'" in (ast.get_source_segment(source,node.test) or ''):break
    nodes.append(node)
env={'__name__':'saved_fantasy_sides','__file__':'app.py'}
exec(compile(ast.Module(body=nodes,type_ignores=[]),'app.py','exec'),env)
if not env['_database_online']:raise SystemExit('Permanent storage unavailable')
store=env['workflow_store'];reports={}
for key in ('pp_fantasy_daily_v1','pp_fantasy_compact_v1'):
    saved=store.get('source',key) or {};items=saved.get('items') or []
    reports[key]={'source_keys':sorted(saved),'records':len(items),'fetched_at':saved.get('fetched_at'),'slate_day':saved.get('slate_day'),
        'key_counts':dict(Counter(k for row in items if isinstance(row,dict) for k in row)),
        'samples':[{k:v for k,v in row.items() if k not in ('player','event')} for row in items[:2]]}
print('FANTASY_SIDE_INSPECTION_JSON '+json.dumps(reports,default=str),flush=True)
if '--rebuild' in sys.argv:
    # Metadata-only rebuild: never recalculate projections or refresh any provider.
    wf=env['workflow'];board=store.get('board','board') or {};items=[];stamps=[]
    for key in ('pp_fantasy_daily_v1','pp_fantasy_compact_v1'):
        saved=store.get('source',key) or {}
        proof=store.get('source','pp_fantasy_recovery_report') or {}
        live=saved.get('mode')=='live' or (key=='pp_fantasy_compact_v1' and proof.get('http_status')==200)
        for item in saved.get('items') or []:
            items.append(env['normalize_pp_projection'](dict(item,_sports_lab_fetched_at=item.get('_sports_lab_fetched_at') or saved.get('fetched_at')),live_response=live))
        if saved.get('fetched_at'):stamps.append(saved['fetched_at'])
    if not stamps:raise SystemExit('No saved capture timestamp')
    merged,count=env['pp_merge_tiers'](board.get('v4_rows') or [],items,max(stamps),fantasy_only=True)
    board['v4_rows']=merged;store.put('board','board',board)
    fantasy=[c for c in merged if env['fantasy_weights'](c)]
    lookup=env['forecast_index'](env['saved_forecast_records'](store))
    counts=Counter()
    for c in fantasy:
        frozen=env['saved_prediction'](lookup,c)
        counts[env['pick_display_label'](c,(frozen or {}).get('result') or {})]+=1
    print('FANTASY_SIDE_REBUILD_JSON '+json.dumps({'imported':count,'display_labels':dict(counts),'provider_requests':0,'frozen_forecasts_changed':0}),flush=True)
