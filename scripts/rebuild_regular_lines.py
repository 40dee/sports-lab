"""Import exact public PrizePicks quotes observed in the browser; no provider requests."""
import ast, copy
from pathlib import Path
source=Path('app.py').read_text();nodes=[]
for node in ast.parse(source).body:
    if isinstance(node,ast.If) and "'--rebuild-compact-fantasy'" in ast.get_source_segment(source,node.test):break
    nodes.append(node)
env={'__name__':'web_verified_rebuild','__file__':'app.py'}
exec(compile(ast.Module(body=nodes,type_ignores=[]),'app.py','exec'),env)
if not env['_database_online']:raise SystemExit('Permanent storage unavailable')
store=env['workflow_store'];wf=env['workflow'];wf.store=env['CompactViewStore'](store)
# Public NBA full-game board. Each exact quote displayed enabled Less and More,
# with no Goblin/Demon icon. Observation time is fixed, never renewed on rerun.
observed='2026-11-10T02:32:20Z'
quotes={
'Fantasy Score':{'Stephen Curry':43.5,'Yaxel Lendeborg':38.5,'Dejounte Murray':37.5,'Jeremiah Fears':33.5,'Brandin Podziemski':31.5,'Derik Queen':27,'Draymond Green':25.5,"De'Anthony Melton":19.5},
'Points':{'Stephen Curry':27.5,'Yaxel Lendeborg':20,'Jeremiah Fears':17.5,'Saddiq Bey':18.5,'Dejounte Murray':18,'Brandin Podziemski':15.5,"De'Anthony Melton":9.5,'Bennedict Mathurin':9.5,'Derik Queen':12,'Gui Santos':8.5,'Georges Niang':8,'Brandon Williams':9.5,'Herbert Jones':9.5,'Karlo Matković':8.5,'Draymond Green':5.5}}
current=env['utc_timestamp'](env['now']())
if (current-env['utc_timestamp'](observed)).total_seconds()>1800:raise SystemExit('Web observation expired; no import')
board=store.get('board','board') or {}; existing=board.get('v4_rows') or []; cards=[];skipped=[]
# Missing offered sides remain blocked; keep serialization direction valid.
for row in existing:
    if row.get('Decoded') and row.get('Direction')=='UNKNOWN' and row.get('Available Sides')==[]:row['Direction']='MORE'
normalize=env['normalize_player']
for stat,players in quotes.items():
    for player,line in players.items():
        matches=[c for c in existing if normalize(c.get('Player'))==normalize(player) and env['today_pregame'](c.get('Start (UTC)')) and 'Golden State' in c.get('Game','') and 'New Orleans' in c.get('Game','')]
        templates=[c for c in matches if (bool(env['fantasy_weights'](c)) if stat=='Fantasy Score' else tuple(sorted(env['market_columns'](c) or ()))==env['pp_stat_key'](stat))] if 'pp_stat_key' in env else [c for c in matches if (bool(env['fantasy_weights'](c)) if stat=='Fantasy Score' else c.get('Stat Key')=='points')]
        if not templates:skipped.append({'player':player,'stat':stat,'reason':'No verified saved player/game/stat template'});continue
        fixtures={(str(c.get('Fixture ID')),c.get('Start (UTC)')) for c in templates}
        if len(fixtures)!=1:skipped.append({'player':player,'stat':stat,'reason':'Ambiguous game template'});continue
        c=copy.deepcopy(templates[0]);c.update({'Platform':'prizepicks','Line':line,'Raw Handicap':line,'Line Type':'Standard','Direction':'MORE','Available Sides':['MORE','LESS'],'Line Source':'PrizePicks web verification','Line Status':'Exact official web quote verified','Web verified at':observed,'Odds checked (UTC)':observed,'Web source URL':'https://app.prizepicks.com/','Web observed stat':stat,'Web observed player':player})
        for field in ('Tier Source','Tier Observed At','Tier Fetched At','Tier Projection ID','Manual observed at'):c.pop(field,None)
        cards.append(c)
replacements={env['market_signature'](c) for c in cards}
board['v4_rows']=[c for c in existing if env['market_signature'](c) not in replacements]+cards
store.put('source','pp_official_web_verification',{'observed_at':observed,'url':'https://app.prizepicks.com/','quotes':quotes,'sides':['MORE','LESS'],'tiers':'Standard','provider_requests':0})
store.put('board','board',board)
prep=env['prepare_saved_board'](wf,{'v4_rows':cards},budget_seconds=90,max_picks=30)
oldhealth=store.get('health',env['slate_day']()) or {}
health=env['refresh_board_health'](wf,{'v4_rows':cards})
health['players']={**(oldhealth.get('players') or {}),**health['players']};wf.store.put('health',env['slate_day'](),health)
combo=env['update_combos'](wf.store,generate=True)
pool,excluded=env['combo_pool'](wf.store,tier_mode='standard')
suggestions=wf.store.get('combo_suggestions',env['slate_day']()+':standard')
print('WEB_VERIFIED_REBUILD '+env['encode']({'imported':len(cards),'skipped':skipped,'preparation':prep,'combo':combo,'eligible':[{'player':x['card']['Player'],'stat':x['card']['Stat'],'line':x['card']['Line'],'side':x['direction'],'score':x['score']} for x in pool],'excluded':excluded,'suggestions':suggestions,'provider_requests':0}))
