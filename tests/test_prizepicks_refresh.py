import ast
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import pandas as pd
import test_combo_regression as fixtures
import test_request_control as requests_fixture

source=fixtures.SOURCE.read_text();tree=ast.parse(source)
env=dict(fixtures.env)
for node in tree.body:
 if isinstance(node,ast.FunctionDef) and node.name in ('prizepicks_projection_page','pp_cached_items','pp_download_pages','normalize_pp_projection','pp_merge_tiers'):
  exec(compile(ast.Module(body=[node],type_ignores=[]),str(fixtures.SOURCE),'exec'),env)

class RefreshTests(unittest.TestCase):
 def setUp(self):
  self.old=dict(env);self.store=fixtures.Memory();self.stamp=pd.Timestamp.now(tz='UTC').isoformat()
  self.store.put('source','pp_health',dict(checked_at=self.stamp,http_status=200,mode='live',usage_unit='credits',remaining='2'))
  self.reserve=Mock(return_value=1)
  env.update(now=lambda:self.stamp,setting=lambda *args:'private-key',feed_request_cap=lambda:10,
    reserve_prizepicks_request=self.reserve,check_prizepicks_source=Mock())
 def tearDown(self):env.clear();env.update(self.old)
 def response(self,status=200,body=None,headers=None):
  return SimpleNamespace(status_code=status,json=lambda:body or {},headers=headers or {})
 def install(self,*responses):
  self.get=Mock(side_effect=list(responses));env['requests']=SimpleNamespace(get=self.get,RequestException=requests_fixture.requests.RequestException)
 def test_exhaustion_and_daily_cap_make_no_projection_or_quote_request(self):
  self.install()
  self.store.put('request_budget',env['slate_day']()+':prizepicks',10)
  with self.assertRaisesRegex(ValueError,'Daily'):env['prizepicks_projection_page'](self.store,{})
  self.store.put('request_budget',env['slate_day']()+':prizepicks',0)
  health=self.store.get('source','pp_health');health['remaining']='0';self.store.put('source','pp_health',health)
  with self.assertRaisesRegex(ValueError,'exhausted'):env['prizepicks_projection_page'](self.store,{})
  self.assertFalse(self.get.called);self.assertFalse(self.reserve.called)
 def test_overpriced_page_does_not_reserve_or_download(self):
  self.install(self.response(body={'quoted_credits':6}))
  with self.assertRaisesRegex(ValueError,'one-credit'):env['prizepicks_projection_page'](self.store,{'limit':200})
  self.assertEqual(self.get.call_count,1);self.assertFalse(self.reserve.called)
 def test_compact_quote_precedes_download_and_updates_remaining_credits(self):
  self.install(self.response(body={'quoted_credits':1}),self.response(body={'meta':{'mode':'live'},'data':[]},headers={'X-Credits-Remaining':'1','X-Credits-Cost':'1'}))
  result=env['prizepicks_projection_page'](self.store,{'view':'compact','limit':100})
  self.assertEqual(result['meta']['mode'],'live');self.reserve.assert_called_once()
  self.assertTrue(self.get.call_args_list[0].args[0].endswith('/credit-costs'))
  self.assertTrue(self.get.call_args_list[1].args[0].endswith('/projections'))
  self.assertEqual(self.store.get('source','pp_health')['remaining'],'1')
 def test_failed_page_sets_cooldown_before_another_attempt(self):
  self.install(self.response(body={'quoted_credits':1}),self.response(status=429))
  with self.assertRaisesRegex(ValueError,'429'):env['prizepicks_projection_page'](self.store,{'view':'compact','limit':100})
  with self.assertRaisesRegex(ValueError,'cooling down'):env['prizepicks_projection_page'](self.store,{'view':'compact','limit':100})
  self.assertEqual(self.get.call_count,2);self.reserve.assert_called_once()
 def test_new_download_cannot_rejuvenate_an_old_observation(self):
  card={'Platform':'prizepicks','Tier Source':'prizepicksapi.com','Start (UTC)':'2026-11-10T02:00:00Z',
    'Tier Fetched At':'2026-11-09T18:00:00Z','Tier Observed At':'2026-11-09T12:00:00Z'}
  self.assertFalse(env['line_freshness'](card,'2026-11-09T18:00:00Z')[0])
 def test_old_recovery_cache_does_not_overwrite_newer_general_feed(self):
  new={'fetched_at':'2026-11-10T01:00Z','items':[{'id':'same','line':5,'observed_at':'2026-11-10T00:59Z'}]}
  old={'fetched_at':'2026-11-09T23:00Z','items':[{'id':'same','line':11.5,'observed_at':'2026-11-09T22:59Z'},{'id':'other','line':20}]}
  for payloads in ((new,old),(old,new)):
   rows={x['id']:x for x in env['pp_cached_items'](payloads)}
   self.assertEqual(rows['same']['line'],5)
   self.assertEqual(rows['other']['_sports_lab_fetched_at'],'2026-11-09T23:00Z')
 def test_compact_pages_normalize_both_offered_sides_without_inventing_them(self):
  fetch=Mock(return_value={'meta':{'mode':'live','next_cursor':None},'data':[
    {'id':'current','player':'Real Player','stat':'Points','period':'Full game','line':5,'tier':'standard','sides':['more','less']}]})
  saved,count=env['pp_download_pages'](self.store,'test',fetch,{'view':'compact','limit':100},max_pages=1,reference='2026-11-09T18:00Z')
  self.assertEqual(count,1);row=saved['items'][0]
  self.assertEqual({x['side'] for x in row['selections']},{'more','less'})
  self.assertIs(row['is_demo'],False)
  self.assertEqual(row['_sports_lab_fetched_at'],'2026-11-09T18:00:00+00:00')
 def test_partial_page_preserves_unrelated_native_quotes(self):
  env.update(pp_card_key=lambda c:(c['Player'].lower(),('PTS',),c['Start (UTC)']),
    normalize_player=lambda s:s.lower(),pp_stat_key=lambda s:('PTS',),numeric_line=lambda x:float(x),
    market_signature=lambda c:(c['Player'],c['Line']))
  start='2100-11-10T02:00:00+00:00'
  a={'Player':'Player A','Player ID':'1','Fixture ID':'game','Start (UTC)':start,'Platform':'prizepicks','Line':10,
    'Line Type':'Standard','Tier Source':'prizepicksapi.com','Tier Projection ID':'current','Decoded':True,'Period':'result'}
  b=dict(a,Player='Player B',**{'Player ID':'2','Tier Projection ID':'unrelated'})
  item={'id':'current','is_demo':False,'status':'active','league':'NBA','player':{'name':'Player A'},
    'event':{'starts_at':start},'market':{'stat':'Points','period':'Full game'},'line':11,
    'selections':[{'side':'more','tier':'standard','available':True},{'side':'less','tier':'standard','available':True}]}
  merged,_=env['pp_merge_tiers']([a,b],[item],self.stamp)
  self.assertIn(b,merged);self.assertNotIn(a,merged)
  self.assertEqual({x['Line'] for x in merged if x['Player']=='Player A'},{11})
