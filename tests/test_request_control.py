import ast
import copy
import concurrent.futures
from contextlib import contextmanager
from datetime import datetime,timezone
from decimal import Decimal
import hashlib,json,math,os,sqlite3,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np
import pandas as pd
import requests

SOURCE=Path(__file__).resolve().parents[1]/'app.py'
tree=ast.parse(SOURCE.read_text())
names={'Store','now','json_safe','encode','identity','utc_timestamp','slate_day','today_pregame',
 'odds_cache_key','odds_daily_cap','reserve_odds_request','api_get','odds_fixture_params',
 'line_freshness','odds_refresh_plan','player_health_key','merge_scan_rows','decode_game',
 'numeric_line','outcome_direction','explicit_tier','automatic_board_run'}
env=dict(globals(),st=SimpleNamespace(session_state={}),SLATE_TIMEZONE='America/Los_Angeles',
         BASE='https://odds.invalid',BOOKS=['prizepicks'],re=__import__('re'))
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in names],type_ignores=[]),str(SOURCE),'exec'),env)
class FakeResponse:
 status_code=200
 def json(self):return {'value':'fresh'}
class RequestTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'store.db'
  self.store=env['Store'](self.path)
  self.settings={'ODDS_API_KEY':'secret-credential','ODDSPAPI_DAILY_REQUEST_CAP':'3'}
  self.calls=Mock(return_value=FakeResponse())
  env.update(workflow_store=self.store,setting=lambda k,default='':self.settings.get(k,default),
             requests=SimpleNamespace(get=self.calls,RequestException=requests.RequestException))
 def tearDown(self):self.temp.cleanup()
 def test_cache_shared_across_app_and_new_worker_store(self):
  a=env['api_get']('/odds',{'fixtureId':'one'})
  env['workflow_store']=env['Store'](self.path)
  b=env['api_get']('/odds',{'fixtureId':'one'})
  self.assertEqual(a,b);self.assertEqual(self.calls.call_count,1)
  self.assertEqual(self.store.get('request_budget',env['slate_day']()+':oddspapi'),1)
  self.assertNotIn('secret-credential',self.store.export())
 def test_manual_worker_share_cap_and_cache_hit_allowed_after_cap(self):
  for i in range(3):env['api_get']('/odds',{'fixtureId':str(i)})
  with self.assertRaisesRegex(ValueError,'guard reached'):env['api_get']('/odds',{'fixtureId':'4'})
  self.assertEqual(self.calls.call_count,3)
  self.assertEqual(env['api_get']('/odds',{'fixtureId':'0'}),{'value':'fresh'})
 def test_budget_atomic_under_concurrent_requests(self):
  def attempt(_):
   try:env['reserve_odds_request'](env['Store'](self.path));return 1
   except ValueError:return 0
  with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
   self.assertEqual(sum(pool.map(attempt,range(12))),3)
  self.assertEqual(self.store.get('request_budget',env['slate_day']()+':oddspapi'),3)
 def test_migration_does_not_reset_old_worker_reservations(self):
  self.store.put('automation',env['slate_day'](),{'odds_requests':3})
  with self.assertRaises(ValueError):env['api_get']('/odds',{'fixtureId':'1'})
  self.calls.assert_not_called()
 def test_failed_account_pauses_other_games_without_credential_leak(self):
  response=FakeResponse();response.status_code=429;self.calls.return_value=response
  with self.assertRaisesRegex(ValueError,'HTTP 429'):env['api_get']('/odds',{'fixtureId':'1'})
  with self.assertRaisesRegex(ValueError,'paused'):env['api_get']('/odds',{'fixtureId':'2'})
  self.assertEqual(self.calls.call_count,1)
 def test_lower_worker_cap_cannot_be_bypassed_by_browser_default(self):
  self.settings['ODDSPAPI_DAILY_REQUEST_CAP']='1'
  env['api_get']('/odds',{'fixtureId':'one'})
  self.settings['ODDSPAPI_DAILY_REQUEST_CAP']='34'
  with self.assertRaisesRegex(ValueError,'guard reached'):env['api_get']('/odds',{'fixtureId':'two'})
  self.assertEqual(self.calls.call_count,1)
 def test_provider_error_keeps_old_fetch_timestamp_and_avoids_immediate_retry(self):
  params={'fixtureId':'1'};key=env['odds_cache_key']('/odds',params)
  old=(pd.Timestamp.now(tz='UTC')-pd.Timedelta(hours=1)).isoformat()
  self.store.put('odds_cache',key,{'payload':{'value':'old'},'fetched_at':old})
  self.calls.side_effect=requests.RequestException('secret-credential in external error')
  with self.assertRaisesRegex(ValueError,'Unable to connect'):env['api_get']('/odds',params)
  self.assertEqual(self.store.get('odds_cache',key)['fetched_at'],old)
  with self.assertRaisesRegex(ValueError,'cooling down'):env['api_get']('/odds',params)
  self.assertEqual(self.calls.call_count,1)
  self.assertNotIn('secret-credential',self.store.export())
 def test_fresh_request_lock_does_not_spend_quota(self):
  params={'fixtureId':'1'};key=env['odds_cache_key']('/odds',params)
  self.store.put('odds_lock',key,{})
  with self.assertRaisesRegex(ValueError,'Another worker'):env['api_get']('/odds',params)
  self.calls.assert_not_called()
  self.assertIsNone(self.store.get('request_budget',env['slate_day']()+':oddspapi'))
 def test_market_definitions_shared_for_one_day_and_force_refreshes_odds(self):
  params={'language':'en'};key=env['odds_cache_key']('/markets',params)
  old=(pd.Timestamp.now(tz='UTC')-pd.Timedelta(hours=2)).isoformat()
  self.store.put('odds_cache',key,{'fetched_at':old,'payload':[]})
  self.assertEqual(env['api_get']('/markets',params),[]);self.calls.assert_not_called()
  env['api_get']('/odds',{'fixtureId':'1'});env['api_get']('/odds',{'fixtureId':'1'},force=True)
  self.assertEqual(self.calls.call_count,2)
 def test_backups_restore_new_namespaces(self):
  env['api_get']('/odds',{'fixtureId':'1'})
  exported=json.loads(self.store.export())
  restored=env['Store'](Path(self.temp.name)/'restored.db')
  restored.restore(exported)
  self.assertEqual(len(restored.items('odds_cache')),1)

class FreshnessTests(unittest.TestCase):
 REF='2026-11-09T21:00:00Z'
 def card(self,**changes):
  c={'Fixture ID':'f1','Player':'Test Player','Game':'A vs B','Start (UTC)':'2026-11-10T00:00:00Z',
     'Odds checked (UTC)':'2026-11-09T20:45:00Z'}
  c.update(changes);return c
 def test_native_time_never_borrowed_from_fresh_odds(self):
  c=self.card(**{'Tier Source':'prizepicksapi.com','Tier Fetched At':'2026-11-09T17:00:00Z'})
  self.assertFalse(env['line_freshness'](c,self.REF)[0])
  c['Tier Fetched At']='2026-11-09T20:45:00Z';self.assertTrue(env['line_freshness'](c,self.REF)[0])
 def test_near_tipoff_threshold_and_historical_originals(self):
  c=self.card(**{'Start (UTC)':'2026-11-09T21:45:00Z','Odds checked (UTC)':'2026-11-09T20:20:00Z'})
  self.assertFalse(env['line_freshness'](c,self.REF)[0])
  c['Start (UTC)']='2026-11-09T20:00:00Z';self.assertTrue(env['line_freshness'](c,self.REF)[0])
 def test_news_targets_only_changed_game_and_unknown_not_fabricated_news(self):
  c=self.card();key=env['player_health_key'](c)
  prev={'players':{key:{'injuries_available':True,'statuses':['Questionable'],'blocked':False}}}
  new={'players':{key:{'injuries_available':True,'statuses':['Out'],'blocked':True}}}
  plan=env['odds_refresh_plan']({'v4_rows':[c]},new,prev,{},self.REF)
  self.assertEqual([v['fixture'] for v in plan],['f1']);self.assertEqual(plan[0]['reason'],'Player status changed')
  self.assertEqual(env['odds_refresh_plan']({'v4_rows':[c]},new,{}, {},self.REF),[])
 def test_target_cooldown_and_no_full_refresh_for_unchanged_far_games(self):
  c=self.card();self.assertEqual(env['odds_refresh_plan']({'v4_rows':[c]}, {},{}, {},self.REF),[])
  c['Start (UTC)']='2026-11-09T21:45:00Z'
  plan=env['odds_refresh_plan']({'v4_rows':[c]}, {},{}, {},self.REF)
  self.assertEqual(len(plan),1)
  self.assertEqual(env['odds_refresh_plan']({'v4_rows':[c]}, {},{}, {'f1':{'attempted_at':self.REF}},self.REF),[])
 def test_no_more_than_three_targeted_requests(self):
  cards=[self.card(**{'Fixture ID':str(i),'Start (UTC)':'2026-11-09T21:45:00Z'}) for i in range(12)]
  self.assertEqual(len(env['odds_refresh_plan']({'v4_rows':cards},{},{},{},self.REF)),3)
 def test_failed_required_check_and_old_native_tier_are_flagged(self):
  c=self.card();checks={'f1':{'attempted_at':'2026-11-09T20:50:00Z','state':'retained'}}
  self.assertFalse(env['line_freshness'](c,self.REF,checks)[0])
  checks['f1']['state']='updated'
  self.assertTrue(env['line_freshness'](c,self.REF,checks)[0])
  c.update({'Tier Source':'prizepicksapi.com','Tier Fetched At':'2026-11-09T20:45:00Z'})
  self.assertFalse(env['line_freshness'](c,self.REF,checks)[0])
 def test_oldest_quotes_win_equal_tipoff_priority(self):
  cards=[self.card(**{'Fixture ID':str(i),'Start (UTC)':'2026-11-09T21:45:00Z',
      'Odds checked (UTC)':'2026-11-09T20:45:00Z' if i<3 else '2026-11-09T19:00:00Z'}) for i in range(6)]
  plan=env['odds_refresh_plan']({'v4_rows':cards},{},{},{},self.REF)
  self.assertEqual([p['fixture'] for p in plan],['3','4','5'])
 def test_existing_board_bootstraps_shared_metadata_without_full_refresh(self):
  from test_combo_regression import Memory
  saved=dict(env)
  try:
   store=Memory();store.remote=True
   day=env['slate_day']();current=env['now']()
   store.put('board','board',{'v4_rows':[{'Fixture ID':'f1','Start (UTC)':'valid'}]})
   store.put('automation',day,{'refreshes':1,'feed_requests':0,'odds_requests':11})
   store.put('input_health','latest',{'checked_at':current})
   calls=[]
   def discovery():
    calls.append('schedule');store.put('odds_fixture','f1',{'fixtureId':'f1','startTime':'valid'})
    return [{'fixtureId':'f1','startTime':'valid'}]
   env.update(feed_request_cap=lambda:10,setting=lambda *_:'',today_pregame=lambda value,*_:value=='valid',
       upcoming_nba=discovery,refresh_board_health=lambda *_:{},
       refresh_saved_game_lines=lambda wf,board,*_:(board,[]),
       prepare_saved_board=lambda *_args,**_kw:{},update_combos=lambda *_args,**_kw:{},
       board_coverage=lambda *_:{})
   env['automatic_board_run'](SimpleNamespace(store=store),refresh=False)
   self.assertEqual(calls,['schedule'])
   self.assertIsNotNone(store.get('odds_fixture','f1'))
  finally:
   env.clear();env.update(saved)
 def test_workflow_turso_and_daily_guard_configuration(self):
  root=SOURCE.parent/'.github/workflows'
  audit=(root/'sports_lab_audit_schedule.yml').read_text()
  self.assertIn('TURSO_AUTH_TOKEN',audit);self.assertNotIn('Missing DATABASE_URL repository secret',audit)
  board=(root/'grade-nba.yml').read_text();self.assertIn('ODDSPAPI_DAILY_REQUEST_CAP',board)

if __name__=='__main__':unittest.main()
