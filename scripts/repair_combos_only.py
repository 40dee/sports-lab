import ast
from pathlib import Path
source=Path('app.py').read_text();nodes=[]
for node in ast.parse(source).body:
    if isinstance(node,ast.If) and "'--rebuild-compact-fantasy'" in (ast.get_source_segment(source,node.test) or ''):break
    nodes.append(node)
env={'__name__':'combo_only','__file__':'app.py'}
exec(compile(ast.Module(body=nodes,type_ignores=[]),'app.py','exec'),env)
wf=env['workflow'];store=wf.store;day=env['slate_day']()
board=store.get('board','board') or {}
index=env['forecast_index'](env['saved_forecast_records'](store,env['combo_clock']()))
health=store.get('health',day) or {};players=health.get('players') or {}
by_id={}
for key,value in players.items():
    if value.get('player_id'):
        match=(key.rsplit('|',1)[0],str(value['player_id']))
        if match not in by_id or str(value.get('checked_at',''))>str(by_id[match].get('checked_at','')):by_id[match]=value
missing={};aliased=0
for card in board.get('v4_rows') or []:
    if card.get('Platform')!='prizepicks' or not env['today_pregame'](card.get('Start (UTC)')):continue
    if str(card.get('Line Type')).lower() not in ('standard','regular'):continue
    frozen=env['saved_prediction'](index,card)
    if not frozen:continue
    key=env['player_health_key'](card);result=frozen.get('result') or {}
    alias=by_id.get((str(card.get('Game')),str(result.get('player_id'))))
    if key not in players and alias:
        players[key]=dict(alias);aliased+=1
    status=players.get(key) or {}
    checked=env['utc_timestamp'](status.get('checked_at'))
    if env['pd'].isna(checked) or (env['combo_clock']()-checked).total_seconds()>3600 or not status.get('injuries_available'):
        missing.setdefault(key,card)
health['players']=players;store.put('health',day,health)
if missing:
    health=env['refresh_board_health'](wf,{'v4_rows':list(missing.values())})
print('COMBO_HEALTH',env['encode']({'verified_id_aliases':aliased,'ESPN_rechecked':len(missing)}),flush=True)
snapshot=env['combo_research_suggestions'](store,'standard',6)
print('SIX_PLAYER_RESULT',env['encode'](snapshot),flush=True)
for key in snapshot['sizes'].get('6',[]):
    combo=store.get('combo',key)
    print('SIX_PLAYER_OPTION',env['encode']([{'player':leg['card']['Player'],'stat':leg['card']['Stat'],'line':leg['card']['Line'],'direction':leg['direction'],'projection':leg['projection']} for leg in combo['legs']]),flush=True)
