import ast,json,unittest
from types import SimpleNamespace
from unittest.mock import Mock
import test_combo_regression as combos
from test_request_control import env as request_env

env=dict(request_env)
for node in combos.tree.body:
 if isinstance(node,ast.FunctionDef) and node.name in ('model_direction_label','check_prizepicks_source','normalize_pp_projection','recover_fantasy_source'):
  exec(compile(ast.Module(body=[node],type_ignores=[]),str(combos.SOURCE),'exec'),env)
class ModelLabelTests(unittest.TestCase):
 def test_unknown_offered_sides_do_not_erase_frozen_model_direction(self):
  result={'direction':'LESS','projection':18.3}
  card={'Platform':'prizepicks','Line Type':'Unverified'}
  self.assertEqual(env['model_direction_label'](result),'↓ LESS')
  self.assertFalse(combos.env['pick_is_selectable'](card,result))
  card['Available Sides']=['MORE']
  self.assertFalse(combos.env['pick_is_selectable'](card,result))
  self.assertEqual(result['direction'],'LESS')
 def test_missing_and_equal_projections_never_get_an_invented_direction(self):
  self.assertEqual(env['model_direction_label']({}),'Not calculated')
  self.assertEqual(env['model_direction_label']({'projection':float('nan'),'direction':'MORE'}),'Not calculated')
  self.assertEqual(env['model_direction_label']({'projection':20.5,'direction':'LESS','tie':True}),'PASS · equal to line')
class SourceHealthTests(unittest.TestCase):
 def setUp(self):
  self.store=combos.Memory()
  self.response=SimpleNamespace(status_code=200,headers={'X-Credits-Remaining':'42'},json=lambda:{
    'meta':{'mode':'live','source_status':'fresh'},'data':[{'league':'NBA','projection_count':200,
      'markets':['Fantasy Score'],'status':'fresh'},{'league':'NFL','projection_count':12}],
    'secret':'must-not-persist'})
  self.get=Mock(return_value=self.response)
  env.update(setting=lambda *args:'private-key',requests=SimpleNamespace(get=self.get,RequestException=request_env['requests'].RequestException))
 def test_coverage_check_does_not_download_or_spend_reserved_requests(self):
  self.store.put('request_budget','today:prizepicks',10)
  report=env['check_prizepicks_source'](self.store)
  self.assertEqual(self.get.call_args.args[0],'https://prizepicksapi.com/api/v1/coverage')
  self.assertEqual(len(report['nba']),1)
  self.assertEqual(self.store.get('request_budget','today:prizepicks'),10)
  self.assertNotIn('private-key',json.dumps(report));self.assertNotIn('must-not-persist',json.dumps(report))
 def test_account_and_provider_failures_get_specific_safe_states(self):
  for status,state in ((401,'invalid_or_revoked_key'),(403,'account_restricted'),(429,'rate_or_allowance_limit'),(503,'provider_unavailable')):
   with self.subTest(status=status):
    self.response.status_code=status
    self.assertEqual(env['check_prizepicks_source'](self.store)['state'],state)


class CompactRecoveryTests(unittest.TestCase):
 def test_flat_record_preserves_only_explicit_side_evidence(self):
  row={'id':'actual','is_demo':False,'player':'Real Player','team':'BOS','league':'NBA',
    'stat':'Fantasy Score','period':'Full game','line':37.5,'opponent':'NYK',
    'event_starts_at':'2026-11-10T03:00Z','more_available':True,'more_tier':'goblin','status':'active'}
  normalized=env['normalize_pp_projection'](row)
  self.assertEqual(normalized['market']['stat'],'Fantasy Score')
  self.assertEqual(normalized['selections'],[{'side':'more','available':True,'tier':'goblin'}])
  self.assertIs(normalized['is_demo'],False)
 def test_repair_cannot_exceed_two_quoted_credits_and_never_resets_old_counter(self):
  import tempfile
  from pathlib import Path
  with tempfile.TemporaryDirectory() as directory:
   store=env['Store'](Path(directory)/'db.sqlite')
   day=env['slate_day']();store.put('request_budget',day+':prizepicks',10)
   row={'id':'live','player':{'name':'Real'},'market':{'stat':'Fantasy Score'},'is_demo':False}
   response=SimpleNamespace(status_code=200,headers={'X-Credits-Remaining':'3','X-Credits-Cost':'1'},
       json=lambda:{'data':[row],'meta':{'mode':'live','next_cursor':'next'}})
   quote=SimpleNamespace(status_code=200,json=lambda:{'quoted_credits':1})
   getter=Mock(side_effect=[quote,response,response,quote])
   old=dict(env)
   try:
    env.update(check_prizepicks_source=lambda _:dict(http_status=200,mode='live',remaining='4',usage_unit='credits'),
       setting=lambda *_:'private',requests=SimpleNamespace(get=getter),
       pp_merge_tiers=lambda *args,**kwargs:([dict(Platform='prizepicks')],1),
       fantasy_weights=lambda _:True,prepare_saved_board=lambda *args,**kwargs:{'ready':1},
       update_combos=lambda *args,**kwargs:{'generated':0})
    wf=SimpleNamespace(store=store)
    result=env['recover_fantasy_source'](wf)
    self.assertEqual(result['requests'],2)
    self.assertEqual(store.get('request_budget',day+':prizepicks'),10)
    self.assertEqual(store.get('request_budget',day+':pp_compact_recovery'),2)
    second=env['recover_fantasy_source'](wf)
    self.assertEqual(second['requests'],0)
    self.assertTrue(all('oddspapi' not in call.args[0] for call in getter.call_args_list))
   finally:env.clear();env.update(old)
