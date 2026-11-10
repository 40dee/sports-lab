import ast,json,unittest
from types import SimpleNamespace
from unittest.mock import Mock
import test_combo_regression as combos
from test_request_control import env as request_env

env=dict(request_env)
for node in combos.tree.body:
 if isinstance(node,ast.FunctionDef) and node.name in ('model_direction_label','check_prizepicks_source'):
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
