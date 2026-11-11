"""Offline regressions: complete daily traversal and whole-pool combo search."""
import unicodedata
import ast
import copy
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pandas as pd
import test_combo_regression as combos

SOURCE=Path(__file__).resolve().parents[1]/'app.py'
TREE=ast.parse(SOURCE.read_text())
NAMES={'pp_download_pages','normalize_pp_projection','import_daily_prizepicks_board',
       'pp_native_game_templates','pp_merge_tiers','pp_stat_key','pp_card_key','pp_nba_team_code',
       'normalize_player','numeric_line','utc_timestamp','slate_day','today_pregame'}
env=dict(combos.env,re=re,unicodedata=unicodedata)
exec(compile(ast.Module(body=[n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name in NAMES],type_ignores=[]),str(SOURCE),'exec'),env)
REF='2026-11-09T18:00:00Z'
FILTERS={'league':'NBA','status':'active','view':'compact','limit':1000}

class FullBoardTests(unittest.TestCase):
 def setUp(self):self.store=combos.Memory()
 def page(self,ids,cursor=None,status='fresh'):
  return {'data':[{'id':str(i)} for i in ids],
          'meta':{'mode':'live','source_status':status,'next_cursor':cursor}}
 def test_all_pages_and_later_reuse_without_duplicate_requests(self):
  fetch=Mock(side_effect=[self.page(range(1000),'next'),self.page(range(1000,1258))])
  saved,calls=env['pp_download_pages'](self.store,'board',fetch,FILTERS,max_pages=10,reference=REF,continuation_seconds=86400)
  self.assertEqual(calls,2);self.assertEqual(len(saved['items']),1258)
  self.assertTrue(saved['pagination_complete']);self.assertFalse(saved['partial'])
  self.assertEqual(fetch.call_args_list[1].args[0]['cursor'],'next')
  saved,calls=env['pp_download_pages'](self.store,'board',fetch,FILTERS,max_pages=10,
       reference='2026-11-09T20:00:00Z',continuation_seconds=86400)
  self.assertEqual(calls,0);self.assertEqual(fetch.call_count,2)
 def test_interrupted_download_keeps_pages_and_continues_cursor(self):
  fetch=Mock(side_effect=[self.page(range(1000),'next'),ValueError('offline')])
  with self.assertRaises(ValueError):env['pp_download_pages'](self.store,'board',fetch,FILTERS,10,REF,86400)
  self.assertEqual(len(self.store.get('source','board')['items']),1000)
  continuation=Mock(return_value=self.page(range(1000,1100)))
  saved,calls=env['pp_download_pages'](self.store,'board',continuation,FILTERS,10,REF,86400)
  self.assertEqual(continuation.call_args.args[0]['cursor'],'next')
  self.assertEqual(len(saved['items']),1100)
 def test_source_partial_is_not_reported_complete_or_repeatedly_redownloaded(self):
  fetch=Mock(return_value=self.page(range(5),status='partial'))
  saved,_=env['pp_download_pages'](self.store,'board',fetch,FILTERS,10,REF,86400)
  self.assertTrue(saved['partial']);self.assertTrue(saved['pagination_complete'])
  env['pp_download_pages'](self.store,'board',fetch,FILTERS,10,REF,86400)
  self.assertEqual(fetch.call_count,1)
 def test_any_partial_page_keeps_whole_traversal_incomplete(self):
  fetch=Mock(side_effect=[self.page([1],'next',status='partial'),self.page([2])])
  saved,_=env['pp_download_pages'](self.store,'board',fetch,FILTERS,10,REF,86400)
  self.assertTrue(saved['pagination_complete']);self.assertTrue(saved['partial'])
 def test_backup_snapshot_reuse_spends_nothing_and_preserves_quote_time(self):
  import hashlib
  import odds_fallback
  from datetime import datetime,timezone
  self.store.remote=True
  key='offline-test';fingerprint=hashlib.sha256(key.encode()).hexdigest()[:20]
  row={'Odds checked (UTC)':REF}
  self.store.put('source','the_odds_api_daily_v2:'+fingerprint+':2026-11-09',
    {'complete':True,'rows':[row],'report':{'complete':True,'checked_at':REF,'http_requests':6}})
  get=Mock(side_effect=AssertionError('No provider call allowed'))
  rows,report=odds_fallback.download(self.store,key,datetime(2026,11,9,20,tzinfo=timezone.utc),get)
  get.assert_not_called();self.assertEqual(rows,[row]);self.assertEqual(report['checked_at'],REF)
  self.assertEqual(report['http_requests'],0)
 def test_repeated_cursor_and_demo_are_rejected(self):
  for fetch in (Mock(side_effect=[self.page([1],'next'),self.page([2],'next')]),
                Mock(return_value={'meta':{'mode':'demo'},'data':[]})):
   with self.assertRaises(ValueError):env['pp_download_pages'](combos.Memory(),'board',fetch,FILTERS,10,REF,86400)
 def test_pool_retains_players_beyond_24(self):
  store=combos.populated(40)
  pool,_=combos.env['combo_pool'](store,REF)
  self.assertEqual(len(pool),40)
  self.assertIn('39',{leg['player_id'] for leg in pool})
 def test_alternative_entries_include_other_teams_at_comparable_scores(self):
  pool,_=combos.env['combo_pool'](combos.populated(40),REF)
  for i,leg in enumerate(pool):
   leg['team_id']='A' if i<12 else ('B' if i<24 else ('C' if i<32 else 'D'))
   leg['score']=.9 if i<24 else .89
  slips=combos.env['combo_candidates'](pool,2,'balanced-v1')
  self.assertEqual(len(slips),3)
  self.assertGreaterEqual(len({leg['team_id'] for slip in slips for leg in slip}),3)
 def test_native_market_can_use_verified_game_without_a_same_stat_template(self):
  saved=dict(env)
  try:
   env.update(today_pregame=lambda *_:True,fantasy_weights=lambda _:False,
    market_columns=lambda _:['PTS'],market_signature=lambda r:(r['Player'],r.get('Stat'),r.get('Line')),
    pp_nearby_fantasy_candidates=lambda *_:[])
   rows=[{'Decoded':True,'Player':'Another Player','Player ID':'',
    'Game':'Boston Celtics vs New York Knicks','Fixture ID':'real-game',
    'Platform':'prizepicks','Period':'result','Stat':'Points','Line':20.5,'Start (UTC)':'2099-11-09T22:00:00Z'}]
   item={'id':'native','is_demo':False,'league':'NBA','status':'active',
    'player':{'name':'Actual Player','team':'BOS'},
    'event':{'opponent':'NYK','starts_at':'2099-11-09T22:00:00Z'},
    'market':{'stat':'Rebounds','period':'Full game'},'line':5.5,
    'selections':[{'side':'more','tier':'standard','available':True},
                  {'side':'less','tier':'standard','available':True}]}
   imported,count=env['pp_merge_tiers'](rows,[item],REF)
   self.assertEqual(count,2)
   added=[r for r in imported if r['Player']=='Actual Player']
   self.assertEqual(added[0]['Stat'],'Rebounds');self.assertEqual(added[0]['Player ID'],'')
   self.assertEqual(added[0]['Fixture ID'],'real-game')
   item['event']['opponent']='LAL'
   with self.assertRaises(ValueError):env['pp_merge_tiers'](rows,[item],REF)
  finally:env.clear();env.update(saved)
 def test_fallback_does_not_skip_native_download(self):
  fn=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name=='run_quota_fallback')
  calls=[n.func.id for n in ast.walk(fn) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)]
  self.assertIn('import_daily_prizepicks_board',calls)

if __name__=='__main__':unittest.main()
