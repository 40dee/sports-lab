"""Spend at most four existing fallback credits on the earliest two Fantasy games."""
import ast
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from odds_fallback import refresh_upcoming_quotes, FANTASY_MARKETS

source = Path('app.py').read_text()
nodes = []
for node in ast.parse(source).body:
    if isinstance(node, ast.If) and "'--rebuild-compact-fantasy'" in (ast.get_source_segment(source, node.test) or ''):
        break
    nodes.append(node)
env = {'__name__': 'fantasy_refresh', '__file__': 'app.py'}
exec(compile(ast.Module(body=nodes, type_ignores=[]), 'app.py', 'exec'), env)
wf = env['workflow']
if not env['_database_online'] or not wf.store.remote:
    raise SystemExit('Permanent database unavailable; no provider requests made.')
board = wf.store.get('board', 'board') or {}
cards = [c for c in board.get('v4_rows', []) if env['fantasy_weights'](c)
         and env['today_pregame'](c.get('Start (UTC)'))
         and c.get('Line Source') == 'The Odds API']
events = []
for c in sorted(cards, key=lambda c: c['Start (UTC)']):
    event = c.get('Provider Event ID')
    if event and event not in events:
        events.append(event)
cards = [c for c in cards if c.get('Provider Event ID') in events[:2]]
rows, report = refresh_upcoming_quotes(wf.store, env['setting']('THE_ODDS_API_KEY', ''),
                                      cards, markets=FANTASY_MARKETS)
changed = set(report['events_updated'])
if changed:
    board['v4_rows'] = [c for c in board.get('v4_rows', []) if not (
        c.get('Line Source') == 'The Odds API' and c.get('Provider Event ID') in changed
        and env['fantasy_weights'](c))] + rows
    board['v4_scan_at'] = env['now']()
    wf.store.put('board', 'board', board)
    env['board_run_stage'](wf.store, 'preparing', False)
    health = env['refresh_board_health'](wf, {'v4_rows': rows})
    prior = wf.store.get('health', env['slate_day']()) or {}
    health['players'] = {**(prior.get('players') or {}), **health['players']}
    wf.store.put('health', env['slate_day'](), health)
    report['preparation'] = env['prepare_saved_board'](wf, {'v4_rows': rows}, budget_seconds=360, max_picks=10000)
    env['board_run_stage'](wf.store, 'finished')
    report['coverage'] = env['board_coverage'](wf, board)
    report['combos'] = env['update_combos'](wf.store, generate=True)
wf.store.put('source', 'fantasy_only_refresh_latest', report)
print('FANTASY_ONLY_REFRESH', env['encode'](report))
