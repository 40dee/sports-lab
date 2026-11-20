"""Read-only rolling-day comparison plus saved combo-rule verification."""
import ast,sys,math
from pathlib import Path
from collections import defaultdict,Counter
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from daily_learning import evaluate_daily
source=Path('app.py').read_text();nodes=[]
for node in ast.parse(source).body:
    if isinstance(node,ast.If) and "'--rebuild-compact-fantasy'" in (ast.get_source_segment(source,node.test) or ''):break
    nodes.append(node)
env={'__name__':'independent_verification','__file__':'app.py'}
exec(compile(ast.Module(body=nodes,type_ignores=[]),'app.py','exec'),env)
store=env['workflow_store']
if not env['_database_online'] or not store.remote:raise SystemExit('Permanent storage unavailable.')
grades=dict(store.items('grade'));groups=defaultdict(list);seen=set()
versions=Counter()
for key,frozen in env['unique_forecasts'](env['saved_forecast_records'](store)):
    result=frozen.get('result') or {};card=frozen.get('card') or {};grade=grades.get(key) or {}
    versions[(result.get('learning_model') or {}).get('version','missing')]+=1
    if grade.get('status')!='FINAL' or grade.get('outcome') not in ('WIN','LOSS'):continue
    from pick_reliability import selectable_learning_record
    if not selectable_learning_record(card,result):continue
    try:
        row=dict(start=card['Start (UTC)'],created=frozen['created_at'],event=str(frozen['event_id']),player=str(result['player_id']),
          baseline=float(result['baseline']),adjusted=float(result['matchup_projection']),actual=float(grade['actual']),line=float(card['Line']))
        if not all(math.isfinite(row[k]) for k in ('baseline','adjusted','actual','line')):continue
        start=pd.to_datetime(row['start'],utc=True);created=pd.to_datetime(row['created'],utc=True)
        if created>=start:continue
        row['day']=start.tz_convert('America/Los_Angeles').strftime('%Y-%m-%d');group=result['stat_group']
    except (KeyError,ValueError,TypeError):continue
    identity=(row['event'],row['player'],group,row['line'])
    if identity in seen:continue
    seen.add(identity);groups[group].append(row)
reports=[];mismatches=0
for group,rows in sorted(groups.items()):
    frame=pd.DataFrame(rows);days=sorted(frame.day.unique());folds=[]
    for day in days[2:]:
        train=frame[frame.day<day];test=frame[frame.day==day]
        def score(data,alpha):
            prediction=np.maximum(0,data.baseline.to_numpy()+alpha*(data.adjusted-data.baseline).to_numpy())
            weights=(1/data.groupby(['event','player'])['line'].transform('count')).to_numpy()
            accuracy=np.average((prediction>data.line.to_numpy())==(data.actual.to_numpy()>data.line.to_numpy()),weights=weights)
            mae=np.average(abs(prediction-data.actual.to_numpy()),weights=weights)
            return float(accuracy),float(mae)
        choices=[0,.25,.5,.75,1,1.25,1.5]
        alpha=max(choices,key=lambda a:(score(train,a)[0],-score(train,a)[1],-abs(a-1)))
        candidate=score(test,alpha);original=score(test,1)
        official=evaluate_daily(frame[frame.day<=day])
        if official['candidate_alpha']!=alpha or abs(official['candidate_holdout']['accuracy']-candidate[0])>1e-10:mismatches+=1
        folds.append(dict(day=day,alpha=alpha,original_accuracy=original[0],candidate_accuracy=candidate[0],
            original_mae=original[1],candidate_mae=candidate[1],player_games=int(test.groupby(['event','player']).ngroups)))
    if folds:
        reports.append(dict(market=group,tested_dates=len(folds),player_games=sum(f['player_games'] for f in folds),
            original_accuracy=float(np.mean([f['original_accuracy'] for f in folds])),
            candidate_accuracy=float(np.mean([f['candidate_accuracy'] for f in folds])),
            accuracy_change=float(np.mean([f['candidate_accuracy']-f['original_accuracy'] for f in folds])),
            mae_change=float(np.mean([f['candidate_mae']-f['original_mae'] for f in folds])),
            better_dates=sum(f['candidate_accuracy']>f['original_accuracy']+1e-10 for f in folds),
            worse_dates=sum(f['candidate_accuracy']<f['original_accuracy']-1e-10 for f in folds),folds=folds))
print('WALKFORWARD_VERIFICATION',env['encode']({'markets':reports,'independent_calculation_mismatches':mismatches,
    'saved_forecast_versions':dict(versions),'active_models':dict(store.items('model')),
    'method':'Train earlier dates only, test next graded date. Equal weight per player-game within each day; equal weight per test day. Historical diagnostic, not prospective proof.'}),flush=True)
day=env['slate_day']();tested=0;failures=[];sizes=Counter()
for key,snapshot in store.items('combo_suggestions'):
    if not key.startswith(day):continue
    for size,ids in (snapshot.get('sizes') or {}).items():
        for identifier in ids:
            combo=store.get('combo',identifier)
            if not combo:failures.append('missing combo');continue
            tested+=1;legs=combo['legs'];sizes[size]+=1
            if len({str(x['player_id']) for x in legs})!=len(legs):failures.append('repeated player')
            if len({str(x['team_id']) for x in legs})!=len(legs):failures.append('repeated team')
            if combo.get('rules_version')!='distinct-teams-exposure-v5':failures.append('old rule version')
print('COMBO_RULE_VERIFICATION',env['encode']({'options_checked':tested,'sizes':dict(sizes),'failures':dict(Counter(failures))}),flush=True)
if mismatches or failures:raise SystemExit('Verification found discrepancies; review required.')

