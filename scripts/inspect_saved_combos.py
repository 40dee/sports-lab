"""Read saved combo inputs without requesting market or player providers."""
import ast
import json
from collections import Counter
from pathlib import Path
source=Path('app.py').read_text()
nodes=[]
for node in ast.parse(source).body:
    if isinstance(node,ast.If) and "'--rebuild-compact-fantasy'" in ast.get_source_segment(source,node.test):
        break
    nodes.append(node)
env={'__name__':'sports_lab_saved_diagnostic','__file__':'app.py'}
exec(compile(ast.Module(body=nodes,type_ignores=[]),'app.py','exec'),env)
if not env['_database_online']:
    raise SystemExit('Permanent database unavailable; no provider calls made.')
store=env['CompactViewStore'](env['workflow_store'])
current=env['combo_clock']()
board=store.get('board','board') or {}
records=store.items('forecast')
index=env['forecast_index'](records)
checks=dict(store.items('odds_refresh'))
report={'checked_at':current.isoformat(),'provider_requests':0,'tier_pools':{},'source_snapshots':{},'current_fantasy':[],'keldon_saved':[]}
for mode in ('standard','goblin','demon','mixed'):
    pool,excluded=env['combo_pool'](store,current,tier_mode=mode)
    report['tier_pools'][mode]={'eligible':len(pool),'excluded':excluded,
        'players':[dict(player=x['card']['Player'],stat=x['card']['Stat'],line=x['card']['Line'],tier=x['card'].get('Line Type'),direction=x['direction'],score=x['score']) for x in pool]}
for key in ('pp_tiers_v1','pp_fantasy_v3','pp_fantasy_compact_v1'):
    payload=store.get('source',key) or {}
    rows=payload.get('items') or []
    counts=Counter()
    for item in rows:
        normalized=env['normalize_pp_projection'](item)
        for selection in normalized.get('selections') or []:
            if selection.get('available') is True:
                counts[str(selection.get('tier'))+':'+str(selection.get('side'))]+=1
    report['source_snapshots'][key]={'fetched_at':payload.get('fetched_at') or payload.get('updated_at'),'records':len(rows),'partial':payload.get('partial'),'explicit_sides':dict(counts)}
seen=set()
for card in board.get('v4_rows') or []:
    if str(card.get('Platform')).lower()!='prizepicks':continue
    signature=env['market_signature'](card)
    if signature in seen:continue
    seen.add(signature)
    if not env['today_pregame'](card.get('Start (UTC)'),current):continue
    result=(env['saved_prediction'](index,card) or {}).get('result') or {}
    value={'player':card.get('Player'),'game':card.get('Game'),'stat':card.get('Stat'),'line':card.get('Line'),'tier':card.get('Line Type'),
        'sides':card.get('Available Sides'),'source':card.get('Tier Source') or card.get('Line Source'),
        'fetched':card.get('Tier Fetched At') or card.get('Odds checked (UTC)'),'observed':card.get('Tier Observed At'),
        'direction':result.get('direction'),'projection':result.get('projection'),'gap':result.get('standardized_gap'),
        'freshness':env['line_freshness'](card,current,checks),'selectable':env['pick_is_selectable'](card,result)}
    if 'keldon' in str(card.get('Player','')).lower():report['keldon_saved'].append(value)
    if env['fantasy_weights'](card):report['current_fantasy'].append(value)
print('SAVED_COMBO_DIAGNOSTIC '+env['encode'](report))
