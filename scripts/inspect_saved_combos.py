"""Audit saved Nov 16 NBA combo outcomes without provider requests or database writes."""
import ast,sys
from pathlib import Path
from collections import Counter,defaultdict
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
source=Path('app.py').read_text();nodes=[]
for node in ast.parse(source).body:
    if isinstance(node,ast.If) and "'--rebuild-compact-fantasy'" in (ast.get_source_segment(source,node.test) or ''):break
    nodes.append(node)
env={'__name__':'combo_outcome_audit','__file__':'app.py'}
exec(compile(ast.Module(body=nodes,type_ignores=[]),'app.py','exec'),env)
store=env['workflow_store']
if not env['_database_online'] or not store.remote:raise SystemExit('Permanent database unavailable.')
day='2026-11-16'
combos={k:v for k,v in store.items('combo') if v.get('day')==day}
grades=dict(store.items('grade')); forecasts=dict(env['saved_forecast_records'](store))
cohorts={k:v for k,v in store.items('combo_cohort') if k.startswith(day)}
suggestions={k:v for k,v in store.items('combo_suggestions') if k.startswith(day)}
actuals=[v for k,v in store.items('combo_actual') if v.get('combo_id') in combos]
def detail(key):
    c=combos[key];g=env['combo_directional_grade'](c,grades);legs=[]
    for leg in c['legs']:
        card=leg['card'];f=forecasts.get(leg['forecast_id']) or {};r=f.get('result') or {};out=grades.get(leg['forecast_id']) or {}
        legs.append(dict(player=card.get('Player'),stat=card.get('Stat'),tier=card.get('Line Type'),line=card.get('Line'),
            direction=leg.get('direction'),projection=r.get('projection'),actual=out.get('actual'),outcome=out.get('outcome','PENDING'),
            grade_status=out.get('status'),history_games=r.get('ranking_games'),minutes=r.get('minutes'),baseline=r.get('baseline'),
            model=r.get('learning_model'),score=leg.get('score'),forecast_id=leg['forecast_id']))
    return dict(id=key,created=c.get('created_at'),size=c['size'],quote_mode=c.get('quote_mode'),score=c.get('ranking_score'),result=g,legs=legs)
summary=defaultdict(Counter);unique={}
for k,c in combos.items():
    tier='standard' if all(str(x['card'].get('Line Type','')).lower() in ('standard','regular') for x in c['legs']) else 'special_or_mixed'
    g=env['combo_directional_grade'](c,grades)
    summary[str(c['size'])+':'+tier][g['state']]+=1
    if tier=='standard':
        for leg in c['legs']:unique.setdefault(leg['forecast_id'],leg)
leg_results=[]
for k,leg in unique.items():
    f=forecasts.get(k) or {};r=f.get('result') or {};g=grades.get(k) or {};card=leg['card']
    leg_results.append(dict(player=card.get('Player'),stat=card.get('Stat'),line=card.get('Line'),direction=leg.get('direction'),
        projection=r.get('projection'),actual=g.get('actual'),outcome=g.get('outcome','PENDING'),games=r.get('ranking_games'),
        minutes=r.get('minutes'),baseline=r.get('baseline'),score=leg.get('score')))
standard6=sorted([k for k,c in combos.items() if c['size']==6 and all(str(x['card'].get('Line Type','')).lower() in ('standard','regular') for x in c['legs'])],
    key=lambda k:combos[k]['created_at'])
sample=list(dict.fromkeys(standard6[:3]+standard6[-3:]))
report=dict(day=day,provider_requests=0,combo_count=len(combos),summary={k:dict(v) for k,v in summary.items()},
    actual_entries_recorded=len(actuals),cohorts=cohorts,suggestions={k:{p:v.get(p) for p in ('created_at','eligible_players','sizes','tier_mode','quote_mode')} for k,v in suggestions.items()},
    six_player_samples=[detail(k) for k in sample],standard_unique_legs=leg_results,
    actual_combos=[detail(x['combo_id']) for x in actuals[:10]])
print('COMBO_OUTCOME_AUDIT '+env['encode'](report),flush=True)
