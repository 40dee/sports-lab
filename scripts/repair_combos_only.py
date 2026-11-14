import ast
from pathlib import Path
source=Path('app.py').read_text()
nodes=[]
for node in ast.parse(source).body:
    if isinstance(node,ast.If) and "'--rebuild-compact-fantasy'" in (ast.get_source_segment(source,node.test) or ''):break
    nodes.append(node)
env={'__name__':'combo_only','__file__':'app.py'}
exec(compile(ast.Module(body=nodes,type_ignores=[]),'app.py','exec'),env)
store=env['workflow_store']
from collections import Counter
board=store.get('board','board') or {}
for tier in ('standard','goblin','demon','mixed'):
    pool,excluded=env['combo_pool'](store,tier_mode=tier)
    print('COMBO_DIAG',tier,env['encode']({'players':len(pool),'games':dict(Counter(x['event_id'] for x in pool)),'excluded':excluded}),flush=True)
fresh=[]
for card in board.get('v4_rows') or []:
    if str(card.get('Platform')).lower()=='prizepicks' and env['today_pregame'](card.get('Start (UTC)')) and env['line_freshness'](card)[0]:
        fresh.append({k:card.get(k) for k in ('Player','Stat','Line','Line Type','Available Sides','Market ID','Line Source','Odds checked (UTC)')})
print('FRESH_PP_SAMPLE',env['encode'](fresh[:15]),flush=True)
print('COMBO_REBUILD',env['encode'](env['update_combos'](store,generate=True)),flush=True)
