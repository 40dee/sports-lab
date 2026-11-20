"""Pinned 2,727-replay high-gap audit, saved records only. No model or grade writes."""
import ast,sys,json,math
from pathlib import Path
from collections import Counter,defaultdict
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from projection_v2 import _batch_fetch
from minutes_error import split
from predictive_context import build
source=Path('app.py').read_text();nodes=[]
for node in ast.parse(source).body:
    if isinstance(node,ast.If) and "'--rebuild-compact-fantasy'" in (ast.get_source_segment(source,node.test) or ''):break
    nodes.append(node)
env={'__name__':'minutes_error_audit','__file__':'app.py'}
exec(compile(ast.Module(body=nodes,type_ignores=[]),'app.py','exec'),env)
if not env['_database_online']:raise SystemExit('Permanent storage unavailable')
store=env['workflow_store'];manifest=json.loads(Path('scripts/minutes_audit_cohort.json').read_text())
grades=dict(store.items('grade'));records={};ids=manifest['ids']
for offset in range(0,len(ids),50):
    chunk=ids[offset:offset+50]
    postgres=store.remote and not getattr(store,'endpoint',None)
    payload="(payload::jsonb - 'snapshot' #- '{result,roster}' #- '{result,advanced}' #- '{result,position_data}')::text" if postgres else "json_remove(payload,'$.snapshot','$.result.roster','$.result.advanced','$.result.position_data')"
    gap="CAST(payload::jsonb#>>'{result,standardized_gap}' AS DOUBLE PRECISION)" if postgres else "CAST(json_extract(payload,'$.result.standardized_gap') AS REAL)"
    sql='SELECT key,'+payload+' FROM sports_records WHERE namespace=? AND key IN ('+','.join('?' for _ in chunk)+') AND '+gap+'>=0.5'
    if callable(getattr(store,'query',None)):rows=store.query(sql,('forecast',*chunk),True)
    else:
        with store.connection() as con:rows=con.execute(sql.replace('?','%s') if postgres else sql,('forecast',*chunk)).fetchall()
    records.update((k,json.loads(v)) for k,v in rows)
if len(records)!=375:raise SystemExit(f'Pinned high-gap cohort mismatch: expected 375, found {len(records)}')
print('MINUTES_AUDIT_PROGRESS '+json.dumps({'pinned_high_gap':len(records)}),flush=True)
refs={(f.get('result') or {}).get('games',{}).get('source_ref') for f in records.values() if isinstance((f.get('result') or {}).get('games'),dict)}
cache={k:v.get('data') for k,v in _batch_fetch(store,'source',refs).items()}
all_rows=[];missing=Counter();features=defaultdict(Counter);by_stat=defaultdict(Counter);player_games=defaultdict(list)
for key,f in records.items():
    r=f.get('result') or {};c=f['card'];g=grades.get(key) or {};row=split(c,r,g)
    row.update(id=key,stat=r.get('stat_group'),direction=r.get('direction'))
    all_rows.append(row)
    if row['status']!='ok':missing[row['status']]+=1;continue
    player_games[(str(f.get('event_id')),str(r.get('player_id')))].append(row)
    data=r.get('games');data=cache.get(data.get('source_ref')) if isinstance(data,dict) else data
    try:history=pd.read_json(__import__('io').StringIO(data),orient='records') if isinstance(data,str) else None
    except (ValueError,TypeError):history=None
    context=build(history,c,f['created_at'],r.get('teammate_context'),r.get('availability'))
    labels=['Played previous day' if context['played_previous_day'] else 'No appearance previous day' if context['played_previous_day'] is False else 'Rest unavailable']
    shift=context['minutes_shift'];labels.append('Minutes increased >=5' if shift is not None and shift>=5 else 'Minutes decreased >=5' if shift is not None and shift<=-5 else 'Minutes stable' if shift is not None else 'Minutes trend unavailable')
    out=context['out_teammates'];labels.append('Teammate Out' if out else 'No listed teammate Out' if out==0 else 'Teammate context unavailable')
    for label in labels:features[label]['wins' if row['win'] else 'losses']+=1
    if not row['win']:
        stat=by_stat[row['stat']];stat['misses']+=1
        stat['minutes_off_5']+=row['minutes_off_5'];stat['minutes_dominant']+=row['dominant']=='minutes';stat['rate_dominant']+=row['dominant']=='rate'
        stat['minutes_correction_rescues']+=row['minutes_correction_rescues'];stat['rate_correction_rescues']+=row['rate_correction_rescues']
known=[r for r in all_rows if r['status']=='ok'];misses=[r for r in known if not r['win']];large=[r for r in misses if r['minutes_off_5']]
report={'source_run':manifest['source_run'],'cohort_digest':manifest['cohort_digest'],'high_gap_picks':len(records),
 'grade_outcomes':dict(Counter((grades.get(k) or {}).get('outcome','MISSING') for k in records)),
 'known_minutes':len(known),'missing':dict(missing),'misses_with_minutes':len(misses),
 'misses_minutes_off_5':len(large),'minutes_off_5_fraction':len(large)/len(misses) if misses else None,
 'minutes_dominant_misses':sum(r['dominant']=='minutes' for r in misses),'rate_dominant_misses':sum(r['dominant']=='rate' for r in misses),
 'minutes_correction_rescues':sum(r['minutes_correction_rescues'] for r in misses),'rate_correction_rescues':sum(r['rate_correction_rescues'] for r in misses),
 'large_minutes_correction_rescues':sum(r['minutes_correction_rescues'] for r in large),
 'large_minutes_overprojected':sum(r['minutes_delta']>=5 for r in misses),'large_minutes_underprojected':sum(r['minutes_delta']<=-5 for r in misses),
 'both_corrections_rescue':sum(r['minutes_correction_rescues'] and r['rate_correction_rescues'] for r in misses),
 'unique_player_games':len(player_games),'unique_missed_player_games':sum(any(not r['win'] for r in rows) for rows in player_games.values()),
 'by_stat':dict(by_stat),'feature_slices':dict(features),
 'note':'Symmetric minutes/rate product decomposition of final projection vs actual. Effective rate includes pace, matchup and alpha effects. Counterfactuals rechoose direction after substituting actual minutes or actual rate; they are diagnostic, not causal proof. Picks share player-games. No provider calls or model writes.'}
print('MINUTES_ERROR_JSON '+json.dumps(report,allow_nan=False),flush=True)
