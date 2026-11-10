import ast
import copy
import hashlib
import json
import math
import unittest
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd

SOURCE=Path(__file__).resolve().parents[1]/'app.py'
tree=ast.parse(SOURCE.read_text())
names={'json_safe','encode','identity','utc_timestamp','market_signature','unique_forecasts',
 'forecast_index','saved_prediction','slate_day','today_pregame','player_health_key',
 'pick_is_selectable','pick_offered_sides','line_freshness'}
names.update(n.name for n in tree.body if isinstance(n,ast.FunctionDef) and
             (n.name.startswith('combo_') or n.name in ('update_combos','record_combo_actual')))
env=dict(globals(),SLATE_TIMEZONE='America/Los_Angeles',COMBO_POLICIES=('balanced-v1','diverse-v1'))
env.update(market_columns=lambda c:c.get('Stat Key'),fantasy_weights=lambda _:None)
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[]),str(SOURCE),'exec'),env)
REF='2026-11-09T18:00:00Z'

class Memory:
 def __init__(self):self.records={}
 def put(self,ns,k,v,immutable=False):
  if not immutable or (ns,k) not in self.records:self.records[ns,k]=copy.deepcopy(v)
 def get(self,ns,k,default=None):return copy.deepcopy(self.records.get((ns,k),default))
 def items(self,ns):return [(k,copy.deepcopy(v)) for (n,k),v in self.records.items() if n==ns]


def populated(count=18):
 store=Memory(); cards=[]; health={}
 for i in range(count):
  c={'Decoded':True,'Platform':'prizepicks','Fixture ID':str(i//2),'Game':f'Game {i//2}',
     'Player':f'Player {i}','Stat':'Points','Stat Key':['PTS'],'Period':'fullgame',
     'Odds checked (UTC)':REF,'Line':20.5,'Line Type':'Standard','Available Sides':['MORE','LESS'],
     'Start (UTC)':'2026-11-10T02:00:00Z'}
  k=env['identity'](c)
  f={'id':k,'card':copy.deepcopy(c),'created_at':'2026-11-09T16:00:00Z',
     'event_id':str(i//2),'result':{'player_id':str(i),'projection':24.,'direction':'MORE',
     'ranking_games':8,'standardized_gap':.9-i*.01,'stat_group':'PTS'}}
  store.put('forecast',k,f)
  health[env['player_health_key'](c)]={'checked_at':REF,'injuries_available':True,
              'player_id':str(i),'team_id':str(i),'statuses':[],'blocked':False}
  cards.append(c)
 store.put('board','board',{'v4_rows':cards}); store.put('health','2026-11-09',{'players':health})
 return store

class ComboTests(unittest.TestCase):
 def test_generator_all_sizes_distinct_players_two_teams_and_bounded_exposure(self):
  store=populated(); report=env['update_combos'](store,True,REF)
  self.assertGreater(report['generated'],0)
  saved=store.get('combo_suggestions','2026-11-09')
  for size in range(2,7):
   self.assertEqual(len(saved['sizes'][str(size)]),3)
  for _,c in store.items('combo'):
   self.assertEqual(len(set(l['player_id'] for l in c['legs'])),c['size'])
   self.assertGreaterEqual(len(set(l['team_id'] for l in c['legs'])),2)
   self.assertTrue(all(sum(x['event_id']==l['event_id'] for x in c['legs'])<=2 for l in c['legs']))
   self.assertEqual(c['kind'],'paper');self.assertIsNone(c['payout_terms'])
 def test_more_only_blocks_less_without_flipping(self):
  store=populated(1); c=store.get('board','board')['v4_rows'][0]
  f=store.get('forecast',env['identity'](c)); f['result']['direction']='LESS'
  store.put('forecast',f['id'],f);c['Available Sides']=['MORE']
  store.put('board','board',{'v4_rows':[c]})
  pool,reasons=env['combo_pool'](store,REF)
  self.assertEqual(pool,[]);self.assertIn('Direction unavailable or unverified',reasons)
  self.assertEqual(store.get('forecast',f['id'])['result']['direction'],'LESS')
 def test_missing_stale_uncertain_injury_checks_block(self):
  for change in ({'checked_at':'2026-11-09T15:00:00Z'}, {'injuries_available':False},
                 {'blocked':True},{'statuses':['Questionable']},{'player_id':'wrong'}):
   store=populated(1);h=store.get('health','2026-11-09');key=next(iter(h['players']))
   h['players'][key].update(change);store.put('health','2026-11-09',h)
   self.assertEqual(env['combo_pool'](store,REF)[0],[])
 def test_insufficient_candidates_never_fill_sizes(self):
  store=populated(2);env['update_combos'](store,True,REF)
  self.assertEqual(store.get('combo_suggestions','2026-11-09')['sizes']['6'],[])
 def test_frozen_snapshots_and_first_daily_cohorts_do_not_change_on_rebuild(self):
  store=populated();env['update_combos'](store,True,REF)
  cohorts=store.items('combo_cohort'); originals=dict(store.items('combo'))
  env['update_combos'](store,True,'2026-11-09T18:10:00Z')
  self.assertEqual(store.items('combo_cohort'),cohorts)
  for key,value in originals.items():self.assertEqual(store.get('combo',key),value)
 def test_never_backfill_started_games(self):
  store=populated();env['update_combos'](store,True,'2026-11-10T03:00:00Z')
  self.assertEqual(store.items('combo'),[])
 def test_grade_pending_missing_void_allhit_missed(self):
  store=populated(2);pool,_=env['combo_pool'](store,REF)
  combo=env['combo_snapshot'](pool,'balanced-v1',REF)
  keys=[l['forecast_id'] for l in pool]
  for outcomes,state in [(('WIN','WIN'),'ALL_HIT'),(('WIN','LOSS'),'MISSED'),
        (('WIN','TIE'),'VOID_REVIEW'),(('WIN','DNP'),'VOID_REVIEW'),
        (('WIN','PENDING'),'PENDING'),(('WIN','LATE_EXCLUDED'),'PENDING')]:
   g={k:{'outcome':o,'actual':22} for k,o in zip(keys,outcomes)}
   self.assertEqual(env['combo_directional_grade'](combo,g,REF)['state'],state)
 def test_live_changes_expiry_and_duplicate_entry_rejected(self):
  store=populated(2);pool,_=env['combo_pool'](store,REF);c=env['combo_snapshot'](pool,'balanced-v1',REF)
  store.put('combo',c['id'],c)
  record=env['record_combo_actual'];terms={'0':0,'1':0,'2':3}
  record(store,c,'entry1','Power',10,terms,True,REF)
  with self.assertRaises(ValueError):record(store,c,'entry1','Power',10,terms,True,REF)
  board=store.get('board','board');board['v4_rows'][0]['Line']=21.5;store.put('board','board',board)
  self.assertFalse(env['combo_live_check'](store,c,REF)[0])
  with self.assertRaises(ValueError):record(store,c,'entry2','Power',10,terms,True,REF)
 def test_money_uses_recorded_terms_not_hardcoded_and_defers_void(self):
  e={'stake':10,'terms':{'0':0,'1':.5,'2':2}}
  self.assertEqual(env['combo_money_result'](e,{'state':'MISSED','wins':1})['Profit'],-5)
  self.assertIsNone(env['combo_money_result'](e,{'state':'VOID_REVIEW'})['Return'])
  self.assertIsNone(env['combo_money_result'](e,{'state':'PENDING'})['Return'])
  e.update(platform_confirmed_at=REF,platform_return=8)
  self.assertEqual(env['combo_money_result'](e,{'state':'MISSED','wins':0})['Profit'],-2)
 def test_overlap_deduplicated_in_leg_patterns(self):
  store=populated();env['update_combos'](store,True,REF)
  for _,c in store.items('combo'):
   for leg in c['legs']:store.put('grade',leg['forecast_id'],{'outcome':'WIN','actual':24})
  env['update_combos'](store,False,REF)
  rows=env['combo_pattern_rows'](store,env['combo_results_rows'](store))
  direction=next(r for r in rows if r['Pattern']=='Direction: MORE')
  self.assertLessEqual(direction['Unique records'],18)
  self.assertEqual(direction['Hit / all-hit rate'],1)
  self.assertEqual(store.items('combo_model'),[])
 def test_learning_requires_chronological_days_not_many_overlapping_entries(self):
  store=populated();env['update_combos'](store,True,REF)
  for key,c in store.items('combo'):store.put('combo_grade',key,{'state':'ALL_HIT'})
  reports=env['combo_learning'](store,REF)
  self.assertTrue(all(r['paired_days']==1 for r in reports))
  self.assertEqual(store.items('combo_model'),[])
 def test_guarded_policy_promotion_then_waits_for_new_days(self):
  store=Memory()
  for d in range(14):
   day=(pd.Timestamp('2026-10-01')+pd.Timedelta(days=d)).date().isoformat()
   policies={}
   for policy in env['COMBO_POLICIES']:
    keys=[]
    for j in range(3):
     key=f'{day}-{policy}-{j}';keys.append(key)
     legs=[{'forecast_id':f'{day}-{j}-{i}','event_id':f'{day}-{j}-{i}'} for i in range(6)]
     store.put('combo',key,{'legs':legs})
     store.put('combo_grade',key,{'state':'ALL_HIT' if policy=='diverse-v1' else 'MISSED'})
    policies[policy]=keys
   store.put('combo_cohort',day+':6',{'day':day,'size':6,'policies':policies})
  reports=env['combo_learning'](store,REF)
  self.assertEqual(store.get('combo_model','6')['policy'],'diverse-v1')
  self.assertTrue(next(r for r in reports if r['size']==6)['promoted'])
  self.assertEqual(len(store.items('combo_review')),1)
  env['combo_learning'](store,REF)
  self.assertEqual(len(store.items('combo_review')),1)
 def test_worker_hooks_and_browsing_no_automatic_fetch_or_training(self):
  render=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='render_combos')
  calls={n.func.attr if isinstance(n.func,ast.Attribute) else n.func.id for n in ast.walk(render)
         if isinstance(n,ast.Call) and isinstance(n.func,(ast.Name,ast.Attribute))}
  self.assertFalse(calls & {'saved_fetch','scan','learn','grade','combo_learning'})
  auto=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='automatic_board_run')
  self.assertIn('update_combos',ast.unparse(auto))
  cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Workflow')
  self.assertIn('update_combos',ast.unparse(next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='maintenance')))

if __name__=='__main__':unittest.main()

class StandardTierTests(unittest.TestCase):
 def test_standard_filter_runs_before_one_line_per_player(self):
  store=populated(8);board=store.get('board','board')
  for card in list(board['v4_rows']):
   goblin=dict(card,Line=10.5,**{'Line Type':'Goblin','Available Sides':['MORE']})
   forecast=store.get('forecast',env['identity'](card))
   forecast.update(id=env['identity'](goblin),card=goblin)
   forecast['result']['standardized_gap']=2.0
   store.put('forecast',forecast['id'],forecast)
   board['v4_rows'].append(goblin)
  store.put('board','board',board)
  mixed,_=env['combo_pool'](store,REF)
  standard,_=env['combo_pool'](store,REF,tier_mode='standard')
  self.assertTrue(all(x['card']['Line Type']=='Goblin' for x in mixed))
  self.assertEqual(len(standard),8)
  self.assertTrue(all(x['card']['Line Type']=='Standard' for x in standard))
  env['update_combos'](store,True,REF)
  saved=store.get('combo_suggestions','2026-11-09:standard')
  self.assertEqual(saved['tier_mode'],'standard')
  for keys in saved['sizes'].values():
   for key in keys:
    self.assertTrue(all(l['card']['Line Type']=='Standard' for l in store.get('combo',key)['legs']))
 def test_standard_never_fills_with_special_lines(self):
  store=populated(8);board=store.get('board','board')
  for card in board['v4_rows']:card['Line Type']='Goblin'
  store.put('board','board',board)
  env['update_combos'](store,True,REF)
  saved=store.get('combo_suggestions','2026-11-09:standard')
  self.assertEqual(saved['eligible_players'],0)
  self.assertTrue(all(not keys for keys in saved['sizes'].values()))
 def test_goblin_and_demon_views_keep_exact_tiers(self):
  store=populated(8);board=store.get('board','board')
  for card in list(board['v4_rows']):
   original=store.get('forecast',env['identity'](card))
   for tier,line in (('Goblin',10.5),('Demon',30.5)):
    special=dict(card,Line=line,**{'Line Type':tier,'Available Sides':['MORE']})
    forecast=copy.deepcopy(original)
    forecast.update(id=env['identity'](special),card=special)
    store.put('forecast',forecast['id'],forecast);board['v4_rows'].append(special)
  store.put('board','board',board)
  env['update_combos'](store,True,REF)
  for mode in ('goblin','demon'):
   saved=store.get('combo_suggestions','2026-11-09:'+mode)
   self.assertEqual(saved['eligible_players'],8)
   for keys in saved['sizes'].values():
    for key in keys:
     self.assertTrue(all(l['card']['Line Type'].lower()==mode for l in store.get('combo',key)['legs']))
