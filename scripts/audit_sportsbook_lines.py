"""Read today's saved 10 AM capture. No provider keys, imports or writes."""
import ast,sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
source=Path('app.py').read_text();nodes=[]
for node in ast.parse(source).body:
    if isinstance(node,ast.If) and "'--rebuild-compact-fantasy'" in (ast.get_source_segment(source,node.test) or ''):break
    nodes.append(node)
env={'__name__':'saved_line_audit','__file__':'app.py'}
exec(compile(ast.Module(body=nodes,type_ignores=[]),'app.py','exec'),env)
if not env['_database_online']:raise SystemExit('Permanent storage unavailable')
from line_edges import saved_quotes,candidate_pairs,stamp
from sportsbook_capture import BOOKS
import pandas as pd
store=env['workflow_store'];day=env['slate_day']();reference=pd.Timestamp(day+' 10:00',tz='America/Los_Angeles')
quotes,captures,cards=saved_quotes(store,day,hour=10,include_cards=True)
board=store.get('board','board') or {}
board_stamp=stamp(board.get('v4_scan_at'))
board_local=board_stamp.astimezone(reference.tzinfo) if board_stamp else None
board_at_ten=board_local is not None and board_local.date().isoformat()==day and board_local.hour==10
if board_at_ten:cards=board.get('v4_rows') or []
unique={}
for card in cards:
    if str(card.get('Platform','')).lower()!='prizepicks':continue
    if str(card.get('Line Type','')).lower() not in ('standard','regular'):continue
    if env['utc_timestamp'](card.get('Start (UTC)'))<=reference:continue
    key=(card.get('Fixture ID'),card.get('Player'),str(card.get('Stat Key')),card.get('Period'),card['Line'])
    unique.setdefault(key,card)
counts={book:{'exact':0,'different_line':0} for book in BOOKS}
for card in unique.values():
    player=env['normalize_player'](card['Player'])
    subset=[q for q in quotes if env['normalize_player'](q['Player'])==player]
    pairs=candidate_pairs(card,subset,lambda c,r:env['engine_no_vig'](c,r))
    for book in BOOKS:
        counts[book]['exact']+=int(any(line==float(card['Line']) and any(b['book']==book for b in books) for line,books in pairs))
        counts[book]['different_line']+=int(any(line!=float(card['Line']) and any(b['book']==book for b in books) for line,books in pairs))
print('SAVED_LINE_COVERAGE_JSON '+json.dumps({'day':day,'capture_times':captures,'board_scan_at':board.get('v4_scan_at'),'denominator_source':'saved 10 AM board' if board_at_ten else 'saved 10 AM raw PP thresholds','standard_lines':len(unique),'books':counts,'scope':'Unique PP Standard thresholds in saved 10 AM PT raw captures, upcoming as of 10 AM. Exact/different may overlap; both require two-way paired quotes.'}))
