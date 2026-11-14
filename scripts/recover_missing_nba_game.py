"""Single guarded request for the missing NBA Clippers–Thunder fixture."""
import ast
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
source=Path('app.py').read_text()
nodes=[]
for node in ast.parse(source).body:
    if isinstance(node,ast.If) and "'--rebuild-compact-fantasy'" in (ast.get_source_segment(source,node.test) or ''):break
    nodes.append(node)
env={'__name__':'missing_nba_game','__file__':'app.py'}
exec(compile(ast.Module(body=nodes,type_ignores=[]),'app.py','exec'),env)
wf=env['workflow'];day=env['slate_day']()
if not env['_database_online'] or not wf.store.remote:raise SystemExit('Permanent storage unavailable.')
games=[g for _,g in wf.store.items('odds_fixture') if env['is_nba_fixture'](g)
       and env['today_pregame'](g.get('startTime'))
       and 'clippers' in (str(g.get('participant1Name'))+' '+str(g.get('participant2Name'))).lower()
       and 'thunder' in (str(g.get('participant1Name'))+' '+str(g.get('participant2Name'))).lower()]
if len(games)!=1:raise SystemExit('One unique NBA fixture not verified; no request made.')
catalog=env['market_catalog']()
account=env['odds_account_snapshot'](wf.store,force=True)
base=env['odds_monthly_budget'];budget=base(wf.store,account)
if budget['remaining']<1:raise SystemExit('No guarded monthly allowance.')
claimed=wf.store.query('INSERT INTO sports_records VALUES (?,?,?,?) ON CONFLICT(namespace,key) DO NOTHING RETURNING key',
    ('missing_game_recovery',day,'{}',env['now']()),fetch=True)
if not claimed:raise SystemExit('Single-request recovery already claimed.')
key=day+':oddspapi_cap';old=wf.store.get('request_budget',key,budget['daily_cap'])
used=int(wf.store.get('request_budget',day+':oddspapi',0));cap=used+1
def single_request_budget(store,snapshot,reference=None):
    result=base(store,snapshot,reference);result['daily_cap']=cap if result['remaining'] else 0
    return result
env['odds_monthly_budget']=single_request_budget
wf.store.put('request_budget',key,cap)
report={'maximum_requests':1}
try:
    game=games[0];fixture=str(game['fixtureId'])
    rows,_=env['decode_game'](game,env['game_odds'](fixture,force=True),catalog)
    report['lines']=len(rows)
    board=wf.store.get('board','board') or {}
    if rows:
        past=[c for c in board.get('v4_rows',[]) if not env['today_pregame'](c.get('Start (UTC)'))]
        board['v4_rows']=past+env['merge_scan_rows'](board.get('v4_rows',[]),rows,{fixture})
        board['v4_scan_at']=env['now']();wf.store.put('board','board',board)
        pp=[c for c in rows if c.get('Platform')=='prizepicks']
        report['prizepicks_lines']=len(pp);report['fantasy_lines']=sum(bool(env['fantasy_weights'](c)) for c in pp)
        env['board_run_stage'](wf.store,'preparing',False)
        health=env['refresh_board_health'](wf,{'v4_rows':pp})
        old_health=wf.store.get('health',day) or {}
        health['players']={**(old_health.get('players') or {}),**health['players']}
        wf.store.put('health',day,health)
        report['preparation']=env['prepare_saved_board'](wf,{'v4_rows':pp},budget_seconds=90,max_picks=10000)
    env['board_run_stage'](wf.store,'finished')
    report['coverage']=env['board_coverage'](wf,board)
    report['combos']=env['update_combos'](wf.store,generate=True)
finally:
    wf.store.put('request_budget',key,old)
    report['requests_reserved']=int(wf.store.get('request_budget',day+':oddspapi',used))-used
    wf.store.put('source','missing_nba_game_recovery_latest',report)
    print('MISSING_NBA_GAME',env['encode'](report),flush=True)
