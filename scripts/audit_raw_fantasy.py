import ast,json
from pathlib import Path
source=Path('app.py').read_text();nodes=[]
for node in ast.parse(source).body:
    if isinstance(node,ast.If) and "'--rebuild-compact-fantasy'" in (ast.get_source_segment(source,node.test) or ''):break
    nodes.append(node)
env={'__name__':'fantasy_raw_audit','__file__':'app.py'}
exec(compile(ast.Module(body=nodes,type_ignores=[]),'app.py','exec'),env)
store=env['workflow_store']
catalogs=store.query("SELECT payload FROM sports_records WHERE namespace='odds_cache' AND json_extract(payload,'$.path')='/markets' ORDER BY updated DESC LIMIT 1",fetch=True)
catalog={}
if catalogs:
    body=json.loads(catalogs[0][0]).get('payload')
    if isinstance(body,dict):body=body.get('markets',body.get('data',[]))
    catalog={str(m.get('marketId')):m for m in body or [] if isinstance(m,dict)}
fantasy={key:m for key,m in catalog.items() if 'fantasy' in str(m).lower() and str(m.get('sportId'))=='11'}
print('NBA_FANTASY_DEFINITIONS',env['encode'](fantasy),flush=True)
for (payload,) in store.query("SELECT payload FROM sports_records WHERE namespace='odds_cache' AND json_extract(payload,'$.path')='/odds' ORDER BY updated DESC LIMIT 12",fetch=True):
    cached=json.loads(payload);body=cached.get('payload') or {}
    pp=(body.get('bookmakerOdds') or {}).get('prizepicks') or {}
    markets=pp.get('markets') or {}
    found=[{'id':key,'definition':catalog.get(str(key)),'active':m.get('marketActive'),'outcomes':len(m.get('outcomes') or {})} for key,m in markets.items() if str(key) in fantasy]
    print('RAW_FANTASY_GAME',env['encode']({'fixture':(cached.get('params') or {}).get('fixtureId'),'fetched':cached.get('fetched_at'),'pp_markets':len(markets),'fantasy':found}),flush=True)
