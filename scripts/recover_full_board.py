"""One bounded recovery using existing OddsPAPI allowance; never purchase credits."""
import ast
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
source = Path('app.py').read_text()
nodes = []
for node in ast.parse(source).body:
    if isinstance(node, ast.If) and "'--rebuild-compact-fantasy'" in (ast.get_source_segment(source, node.test) or ''):
        break
    nodes.append(node)
env = {'__name__': 'full_board_recovery', '__file__': 'app.py'}
exec(compile(ast.Module(body=nodes, type_ignores=[]), 'app.py', 'exec'), env)
wf = env['workflow']
if not env['_database_online'] or not wf.store.remote:
    raise SystemExit('Permanent storage unavailable; no downloads made.')
day = env['slate_day']()
claim = wf.store.query('INSERT INTO sports_records VALUES (?,?,?,?) ON CONFLICT(namespace,key) DO NOTHING RETURNING key',
    ('board_recovery', day, '{}', env['now']()), fetch=True)
if not claim:
    raise SystemExit('Recovery already claimed for this slate; no additional requests made.')
account = env['odds_account_snapshot'](wf.store, force=True)
budget = env['odds_monthly_budget'](wf.store, account)
used_today = int(wf.store.get('request_budget', day + ':oddspapi', 0))
allowance = min(10, budget['remaining'])
print('RECOVERY_ALLOWANCE', env['encode']({'provider_used':account['used'], 'provider_limit':account['limit'],
    'guard_remaining':budget['remaining'], 'maximum_requests':allowance}), flush=True)
if not allowance:
    raise SystemExit('Monthly reservation guard has no remaining allowance; no downloads made.')
cap_key = day + ':oddspapi_cap'
old_cap = wf.store.get('request_budget', cap_key, budget['daily_cap'])
cap = used_today + allowance
original_budget = env['odds_monthly_budget']
def recovery_budget(store, snapshot, reference=None):
    result = original_budget(store, snapshot, reference)
    result['daily_cap'] = cap if result['remaining'] else 0
    return result
env['odds_monthly_budget'] = recovery_budget
wf.store.put('request_budget', cap_key, cap)
report = {'started_at':env['now'](), 'games':[], 'max_requests':allowance}
try:
    env['board_run_stage'](wf.store, 'importing')
    games = [g for g in env['upcoming_nba']() if env['today_pregame'](g.get('startTime'))]
    catalog = env['market_catalog']()
    board = wf.store.get('board','board') or {}
    imported = []
    for game in games:
        fixture = str(game['fixtureId'])
        result = {'game':str(game.get('participant1Name'))+' vs '+str(game.get('participant2Name'))}
        try:
            rows, summary = env['decode_game'](game, env['game_odds'](fixture,force=True), catalog)
            result.update(lines=len(rows), fantasy_lines=sum(bool(env['fantasy_weights'](c)) for c in rows))
            if rows:
                past=[c for c in board.get('v4_rows',[]) if not env['today_pregame'](c.get('Start (UTC)'))]
                board['v4_rows'] = past + env['merge_scan_rows'](board.get('v4_rows',[]), rows, {fixture})
                board['v4_scan_at'] = env['now']()
                wf.store.put('board','board',board)
                imported.extend(rows)
        except (ValueError,KeyError,TypeError):
            result['state'] = 'Unavailable; saved projections preserved'
        report['games'].append(result)
        print('RECOVERED_GAME',env['encode'](result),flush=True)
    if imported:
        env['board_run_stage'](wf.store, 'preparing', False)
        health = env['refresh_board_health'](wf, {'v4_rows':imported})
        prior = wf.store.get('health',day) or {}
        health['players'] = {**(prior.get('players') or {}), **health['players']}
        wf.store.put('health',day,health)
        report['preparation'] = env['prepare_saved_board'](wf, board, budget_seconds=420,max_picks=10000)
    env['board_run_stage'](wf.store,'finished')
    report['coverage'] = env['board_coverage'](wf,board)
    report['combos'] = env['update_combos'](wf.store,generate=True)
finally:
    wf.store.put('request_budget',cap_key,old_cap)
    report['requests_reserved'] = int(wf.store.get('request_budget',day+':oddspapi',used_today))-used_today
    report['finished_at'] = env['now']()
    wf.store.put('source','full_board_recovery_latest',report)
    print('FULL_BOARD_RECOVERY',env['encode'](report),flush=True)
