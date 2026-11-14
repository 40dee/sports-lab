"""Activate daily capture policy using storage only; no line-provider keys."""
import ast
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
source=Path('app.py').read_text();nodes=[]
for node in ast.parse(source).body:
    if isinstance(node,ast.If) and "'--rebuild-compact-fantasy'" in (ast.get_source_segment(source,node.test) or ''):break
    nodes.append(node)
env={'__name__':'activate_daily_cache','__file__':'app.py'}
exec(compile(ast.Module(body=nodes,type_ignores=[]),'app.py','exec'),env)
wf=env['workflow'];store=wf.store
if not env['_database_online'] or not store.remote:
    raise SystemExit('Permanent storage unavailable; activation not saved.')
from daily_capture import close_capture
store.put('board_capture_policy','enabled',True)
day=env['slate_day']();board=store.get('board','board') or {}
stamp=env['utc_timestamp'](board.get('v4_scan_at'))
if store.get('daily_board_capture',day) or (board.get('v4_rows') and env['pd'].notna(stamp) and env['slate_day'](stamp)==day):
    close_capture(store,day,env['now']())
print('DAILY_CACHE_POLICY',env['encode']({'enabled':store.get('board_capture_policy','enabled'),
      'day':day,'capture':store.get('daily_board_capture',day),'saved_rows':len(board.get('v4_rows') or []),
      'provider_requests':0}))
