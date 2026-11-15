"""One explicitly requested migration from the old partial board to split feeds."""
import ast,hashlib,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
source=Path('app.py').read_text();nodes=[]
for node in ast.parse(source).body:
    if isinstance(node,ast.If) and "'--rebuild-compact-fantasy'" in (ast.get_source_segment(source,node.test) or ''):break
    nodes.append(node)
env={'__name__':'split_capture','__file__':'app.py'}
exec(compile(ast.Module(body=nodes,type_ignores=[]),'app.py','exec'),env)
wf=env['workflow'];store=wf.store;day=env['slate_day']()
if not env['_database_online'] or not store.remote:raise SystemExit('Permanent storage unavailable.')
ledger=store.get('automation',day) or {};capture=store.get('daily_board_capture',day) or {}
routing={'regular_props':'The Odds API','fantasy_score':'PrizePicksAPI','league':'NBA'}
if ledger.get('source_routing')!=routing and not store.get('source_route_migration',day):
    if capture.get('state')=='capturing':raise SystemExit('Existing capture still active; no new import made.')
    key=env['setting']('THE_ODDS_API_KEY','')
    if not key or not env['setting']('PRIZEPICKS_API_KEY',''):raise SystemExit('Both split-source keys are required.')
    fingerprint=hashlib.sha256(key.encode()).hexdigest()[:20]
    if int(store.get('request_budget',day+':the_odds_api:'+fingerprint,0) or 0):
        raise SystemExit('New key already has reserved credits today; no second capture made.')
    store.put('source_route_migration',day,{'requested_at':env['now'](),'previous_capture':capture,'previous_ledger':ledger})
    # Preserve the prior state above; only the superseded capture lock is replaced.
    store.query('DELETE FROM sports_records WHERE namespace=? AND key=?',
                ('daily_board_capture',day))
    ledger.pop('last_run',None)
    store.put('automation',day,ledger)
    print('SPLIT_CAPTURE_MIGRATION old partial capture archived; one new-key capture authorized',flush=True)
report=env['daily_cached_board_run'](wf,capture_due=True,budget_seconds=600)
print('SPLIT_CAPTURE_RESULT',env['encode'](report),flush=True)
