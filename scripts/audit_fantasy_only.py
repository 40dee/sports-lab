"""Audit and prepare only saved NBA Fantasy lines, without odds requests."""
import ast
from pathlib import Path
from collections import Counter
source=Path('app.py').read_text();nodes=[]
for node in ast.parse(source).body:
    if isinstance(node,ast.If) and "'--rebuild-compact-fantasy'" in (ast.get_source_segment(source,node.test) or ''):break
    nodes.append(node)
env={'__name__':'fantasy_only','__file__':'app.py'}
exec(compile(ast.Module(body=nodes,type_ignores=[]),'app.py','exec'),env)
helper=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='ui_number')
exec(compile(ast.Module(body=[helper],type_ignores=[]),'app.py','exec'),env)
wf=env['workflow'];day=env['slate_day']()
board=wf.store.get('board','board') or {}
rows=[c for c in board.get('v4_rows',[]) if env['fantasy_weights'](c)]
prep=env['prepare_saved_board'](wf,{'v4_rows':rows},budget_seconds=90,max_picks=10000)
reader=getattr(wf.store,'forecast_items',None)
records=reader(day=day) if callable(reader) else wf.store.items('forecast')
items=[i for i in env['current_fantasy_records'](rows,records,{}) if i['date']==day]
source_status=wf.store.get('source','the_odds_api_fallback_latest') or {}
games={g['game'] for g in source_status.get('game_coverage',[]) if g.get('game')}
games.update(c.get('Game') for c in rows if c.get('Game'))
report=[]
checks=dict(wf.store.items('odds_refresh'))
for game in sorted(games):
    group=[i for i in items if i['card'].get('Game')==game]
    missing_components=sum(set(env['fantasy_weights'](i['card']))!={c.get('Stat') for c in i['frozen'].get('result',{}).get('components',[])} for i in group)
    report.append({'game':game,'saved':len(group),'players':len({i['card']['Player'] for i in group}),
       'upcoming':sum(i['upcoming'] for i in group),
       'verified_upcoming':sum(i['upcoming'] and env['line_freshness'](i['card'],refresh_checks=checks)[0] and env['pick_is_selectable'](i['card'],i['frozen'].get('result') or {}) for i in group),
       'tiers':dict(Counter(i['card'].get('Line Type') for i in group)),
       'missing_six_stat_breakdown':missing_components})
result={'checked_at':env['now'](),'preparation':prep,'games':report}
wf.store.put('source','fantasy_only_audit_latest',result)
print('FANTASY_ONLY_RESULT',env['encode'](result),flush=True)
