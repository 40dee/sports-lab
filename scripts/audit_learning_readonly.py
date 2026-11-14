"""Read-only audit of effective learning examples across every saved market."""
import ast,math
from pathlib import Path
from collections import Counter,defaultdict
source=Path('app.py').read_text();nodes=[]
for node in ast.parse(source).body:
    if isinstance(node,ast.If) and "'--rebuild-compact-fantasy'" in (ast.get_source_segment(source,node.test) or ''):break
    nodes.append(node)
env={'__name__':'learning_audit','__file__':'app.py'}
exec(compile(ast.Module(body=nodes,type_ignores=[]),'app.py','exec'),env)
store=env['workflow_store'];grades=dict(store.items('grade'));models=dict(store.items('model'))
groups=defaultdict(list);seen=set();counts=Counter()
forecasts=env['unique_forecasts'](env['saved_forecast_records'](store))
for key,frozen in forecasts:
    counts['saved_forecasts']+=1
    grade=grades.get(key) or {};counts['outcome:'+str(grade.get('outcome','UNRESOLVED'))]+=1
    result=frozen.get('result') or {};card=frozen.get('card') or {}
    if grade.get('status')!='FINAL' or grade.get('outcome') not in ('WIN','LOSS'):continue
    try:
        numbers=[float(result['baseline']),float(result['matchup_projection']),float(grade['actual']),float(card['Line'])]
        if not all(math.isfinite(x) for x in numbers):continue
        group=result['stat_group'];event=str(frozen['event_id']);player=str(result['player_id'])
        stamp=env['utc_timestamp'](card.get('Start (UTC)'))
        if env['pd'].isna(stamp):continue
    except (KeyError,TypeError,ValueError):counts['invalid_training_metadata']+=1;continue
    identity=(event,player,group,numbers[3])
    if identity in seen:continue
    seen.add(identity);groups[group].append({'day':stamp.strftime('%Y-%m-%d'),'event':event,'player':player})
reports=[]
for group,rows in sorted(groups.items()):
    days=sorted({r['day'] for r in rows});events={r['event'] for r in rows}
    blocks=[]
    if len(rows)<120:blocks.append('fewer than 120 settled line examples')
    if len(events)<25:blocks.append('fewer than 25 games')
    if len(days)<12:blocks.append('fewer than 12 graded dates')
    model=models.get(group) or {}
    reports.append({'market':group,'settled_line_examples':len(rows),'distinct_player_games':len({(r['event'],r['player']) for r in rows}),
       'games':len(events),'graded_dates':len(days),'first_date':days[0],'last_date':days[-1],
       'active_version':model.get('version','initial-v1'),'active_alpha':model.get('alpha',1.0),'initial_gate_blocks':blocks})
print('LEARNING_AUDIT',env['encode']({'counts':dict(counts),'markets':reports,'active_models':models,
    'review_records':len(store.items('model_review'))}),flush=True)
