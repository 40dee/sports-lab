"""Read-only saved-day Standard/v2 and pregame sportsbook attribution; no provider keys."""
import ast,sys,json,math,argparse
from pathlib import Path
from collections import defaultdict,Counter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
parser=argparse.ArgumentParser();parser.add_argument('--day',default='2026-11-19');args=parser.parse_args()
source=Path('app.py').read_text();nodes=[]
for node in ast.parse(source).body:
    if isinstance(node,ast.If) and "'--rebuild-compact-fantasy'" in (ast.get_source_segment(source,node.test) or ''):break
    nodes.append(node)
env={'__name__':'settled_consensus_audit','__file__':'app.py'}
exec(compile(ast.Module(body=nodes,type_ignores=[]),'app.py','exec'),env)
if not env['_database_online']:raise SystemExit('Permanent storage unavailable')
from result_scopes import standard_playable_rows
from line_edges import saved_quotes,detect,stamp
from market_compare import compare_no_vig
import projection_v2
store=env['workflow_store'];day=args.day
records=env['unique_forecasts'](store.forecast_items(day=day));lookup=dict(records)
grades=dict(store.items('grade'));rows=[]
for k,f in records:
    c=f['card'];r=f.get('result') or {};g=grades.get(k) or {}
    rows.append({'ID':k,'Platform':c.get('Platform'),'Line type':c.get('Line Type'),'Selectable':r.get('selection_allowed') is not False and env['pick_is_selectable'](c,r),
        'Created (UTC)':f.get('created_at'),'Start (UTC)':c.get('Start (UTC)'),'Outcome':g.get('outcome'),'Grade status':g.get('status'),
        'Event':f.get('event_id'),'Stat columns':env['market_columns'](c),'Player':c.get('Player'),'Period':c.get('Period')})
selected=standard_playable_rows(rows);selected_ids={r['ID'] for r in selected}
print('DAY_AUDIT_PROGRESS '+json.dumps({'day':day,'selected_standard':len(selected)}),flush=True)
class DayStore:
    def items(self,namespace):
        return list(grades.items()) if namespace=='grade' else [(k,f) for k,f in records if k in selected_ids] if namespace=='forecast' else store.items(namespace)
    def research_forecast_items(self):return self.items('forecast')
    def get(self,*a,**k):return store.get(*a,**k)
    def query(self,*a,**k):return store.query(*a,**k)
projection_v2.install(env)
replay=projection_v2.run_backtest(DayStore(),env,max_samples=None,include_rows=True)
v2={r['id']:r for r in replay.pop('replay_rows',[])}
print('DAY_AUDIT_PROGRESS '+json.dumps({'replayed':len(v2),'excluded':replay.get('excluded')}),flush=True)
quote_rows,captures=saved_quotes(store,day);by_player=defaultdict(list)
def name(value):
    value=str(value or '')
    if ',' in value:
        last,first=value.split(',',1);value=first+' '+last
    return env['normalize_player'](value)
for q in quote_rows:by_player[name(q['Player'])].append(q)
compare=lambda c,quotes:compare_no_vig(c,quotes,env['normalize_player'],env['engine_team_key'],env['market_columns'],env['fantasy_weights'])
buckets=defaultdict(list);other=Counter();moved=[]
for row in selected:
    k=row['ID'];f=lookup[k];c=f['card'];r=f['result'];g=grades.get(k) or {}
    if g.get('status')!='FINAL' or g.get('outcome') not in ('WIN','LOSS'):
        other[g.get('outcome') or 'PENDING']+=1;continue
    win=int(g['outcome']=='WIN');entry=dict(v2.get(k) or {},id=k,v1_win=win)
    buckets['Overall v1 Standard'].append(entry)
    if env['fantasy_weights'](c):buckets['Fantasy Score Standard'].append(entry)
    created=stamp(f.get('created_at'))
    quotes=[q for q in by_player[name(c.get('Player'))] if stamp(q.get('Provider fetched at')) and created and stamp(q['Provider fetched at'])<=created]
    market=r.get('market') or {}
    if projection_v2.saved_book_direction(market) is None:market=compare(c,quotes) or {}
    evidence=market.get('books') or [market]
    if any(stamp(b.get('captured_at')) and created and stamp(b['captured_at'])>created for b in evidence):market={}
    probability=market.get(str(r.get('direction')).lower())
    if isinstance(probability,(float,int)) and math.isfinite(probability) and 0<=probability<=1:
        label='<49% opposition' if probability<.49 else '49%–<53.5%' if probability<.535 else '>=53.5% aligned'
    else:label='No matched frozen/pre-freeze quote'
    buckets[label].append(entry)
    if not market:
        edge=detect(c,r,quotes,compare,env['pick_offered_sides'](c) or (),env['market_columns'](c),bool(env['fantasy_weights'](c)))
        if edge and abs(edge['book_line']-float(c['Line']))>=1.0:
            actual=g.get('actual');line=float(c['Line'])
            if actual is not None and float(actual)!=line:
                alt=dict(entry,market_win=int((float(actual)>line)==(edge['side']=='MORE')),market_side=edge['side'],book_line=edge['book_line'],pp_line=line)
                moved.append(alt)
                buckets['Moved-line >=54%, >=1 point offset: model sides'].append(entry)
def summary(entries):
    out={'settled':len(entries),'v1_wins':sum(r['v1_win'] for r in entries)}
    out['v1_hit']=out['v1_wins']/len(entries) if entries else None
    paired=[r for r in entries if 'v2_win' in r]
    out['replayable']=len(paired)
    for ver in ('v1','v2'):
        out[ver+'_paired_hit']=sum(r[ver+'_win'] for r in paired)/len(paired) if paired else None
        out[ver+'_mae']=sum(abs(r[ver+'_proj']-r['actual']) for r in paired)/len(paired) if paired else None
    cal=[r for r in paired if r.get('v1_prob') is not None and r.get('v2_prob') is not None]
    out['calibration_n']=len(cal)
    for ver in ('v1','v2'):out[ver+'_brier']=sum((r[ver+'_prob']-r[ver+'_win'])**2 for r in cal)/len(cal) if cal else None
    return out
print('SETTLED_CONSENSUS_JSON '+json.dumps({'day':day,'selected_standard':len(selected),'unsettled_or_excluded':dict(other),'results':{k:summary(v) for k,v in buckets.items()},'v2_replay':replay,
    'moved_line_market_side':{'settled':len(moved),'wins':sum(r['market_win'] for r in moved),'hit_rate':sum(r['market_win'] for r in moved)/len(moved) if moved else None},
    'quote_capture_times':captures,'notes':'Standard playable one per player/stat/game selected before outcome filtering. Exact consensus buckets use frozen v1 direction, with v2 compared on the same records. All v1 vs v2 metrics use replayable paired records; all-settled v1 counts are also reported. Raw quotes must precede forecast creation. Moved-line uses >=54% offered side and >=1 point offset, separate overlapping subset. Frozen records unchanged, no grading writes or provider calls.'},allow_nan=False),flush=True)
