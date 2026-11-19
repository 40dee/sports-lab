"""Read-only, pregame-only November 18 consensus attribution. No provider keys."""
import ast,sys,json,math
from pathlib import Path
from collections import defaultdict,Counter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
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
store=env['workflow_store'];day='2026-11-18'
records=env['unique_forecasts'](store.forecast_items(day=day));lookup=dict(records)
# Grade reads are narrow and do not trigger grading/maintenance.
grades=dict(store.items('grade'))
rows=[]
for k,f in records:
    c=f['card'];r=f.get('result') or {};g=grades.get(k) or {}
    rows.append({'ID':k,'Platform':c.get('Platform'),'Line type':c.get('Line Type'),'Selectable':r.get('selection_allowed') is not False and env['pick_is_selectable'](c,r),
        'Created (UTC)':f.get('created_at'),'Start (UTC)':c.get('Start (UTC)'),'Outcome':g.get('outcome'),'Grade status':g.get('status'),
        'Event':f.get('event_id'),'Stat columns':env['market_columns'](c),'Player':c.get('Player'),'Period':c.get('Period')})
selected=standard_playable_rows(rows);buckets=defaultdict(Counter);other=Counter();quote_rows,captures=saved_quotes(store,day)
def name(value):
    value=str(value or '')
    if ',' in value:
        last,first=value.split(',',1);value=first+' '+last
    return env['normalize_player'](value)
by_player=defaultdict(list)
for q in quote_rows:by_player[name(q['Player'])].append(q)
compare=lambda c,quotes:compare_no_vig(c,quotes,env['normalize_player'],env['engine_team_key'],env['market_columns'],env['fantasy_weights'])
def add(label,win):buckets[label]['wins' if win else 'losses']+=1
for row in selected:
    k=row['ID'];f=lookup[k];c=f['card'];r=f['result'];g=grades.get(k) or {}
    if g.get('status')!='FINAL' or g.get('outcome') not in ('WIN','LOSS'):
        other[g.get('outcome') or 'PENDING']+=1;continue
    win=g['outcome']=='WIN';add('Overall v1 Standard',win)
    if env['fantasy_weights'](c):add('Fantasy Score Standard',win)
    created=stamp(f.get('created_at'))
    quotes=[q for q in by_player[name(c.get('Player'))] if stamp(q.get('Provider fetched at')) and created and stamp(q['Provider fetched at'])<=created]
    market=r.get('market') or compare(c,quotes) or {}
    evidence=market.get('books') or [market]
    if any(stamp(b.get('captured_at')) and created and stamp(b['captured_at'])>created for b in evidence):market={}
    probability=market.get(str(r.get('direction')).lower())
    if isinstance(probability,(float,int)) and math.isfinite(probability) and 0<=probability<=1:
        label='<49% opposition' if probability<.49 else '49%–<53.5%' if probability<.535 else '>=53.5% aligned'
    else:label='No matched frozen/pre-freeze quote'
    add(label,win)
    if not market:
        edge=detect(c,r,quotes,compare,env['pick_offered_sides'](c) or (),env['market_columns'](c),bool(env['fantasy_weights'](c)))
        if edge:
            actual=g.get('actual');line=float(c['Line'])
            if actual is not None and float(actual)!=line:
                add('Moved-line >=54%: offered market side', (float(actual)>line)==(edge['side']=='MORE'))
                add('Same moved-line subset: v1 side',win)
print('SETTLED_CONSENSUS_JSON '+json.dumps({'day':day,'selected_standard':len(selected),'unsettled_or_excluded':dict(other),'results':{k:dict(v,settled=v['wins']+v['losses'],hit_rate=v['wins']/(v['wins']+v['losses'])) for k,v in buckets.items()},'quote_capture_times':captures,'notes':'Standard playable one per player/stat/game, selected before outcome filtering. Frozen v1 directions; raw quotes must precede forecast creation. Moved-line alternative side is separate and overlaps the no-exact-quote cohort. No grading writes or provider calls.'}),flush=True)
