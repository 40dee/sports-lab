import ast,copy,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pandas as pd
from pick_reliability import learn
from test_pick_reliability import record
import test_combo_regression as fixtures
TREE=ast.parse(Path(fixtures.SOURCE).read_text())
class LearningScopeTests(unittest.TestCase):
 def inputs(self):
  records=[];grades={}
  for i,kind in enumerate(('Standard','Regular','Goblin','Demon','blocked','unoffered'),1):
   key,f,g=record(day=i,player=kind)
   f['result']['matchup_projection']=f['result']['projection']
   if kind in ('Standard','Regular','Goblin','Demon'):f['card']['Line Type']=kind
   if kind=='blocked':f['result']['selection_allowed']=False
   if kind=='unoffered':f['card']['Available Sides']=['LESS']
   records.append((key,f));grades[key]=g
  return records,grades
 def test_reliability_training_evaluation_and_patterns_exclude_contamination(self):
  records,grades=self.inputs();original=copy.deepcopy(records)
  model,report=learn(records,grades)
  clean,clean_report=learn(records[:2],grades)
  self.assertEqual(model,clean)
  for field in ('input_digest','folds','patterns','examples','ranking_examples'):
   self.assertEqual(report[field],clean_report[field])
  self.assertEqual(report['examples'],2)
  self.assertEqual(report['excluded']['not_selectable_standard'],4)
  self.assertEqual(records,original)
 def test_workflow_alpha_and_daily_evaluation_only_see_selectable_regular_lines(self):
  records,grades=self.inputs();store=fixtures.Memory()
  for k,f in records:store.put('forecast',k,f)
  for k,g in grades.items():store.put('grade',k,g)
  cls=next(n for n in TREE.body if isinstance(n,ast.ClassDef) and n.name=='Workflow')
  node=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='learn')
  env={'pd':pd,'now':lambda:'2026-11-18T12:00Z','unique_forecasts':lambda x:x,'saved_forecast_records':lambda s:s.items('forecast')}
  exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),env)
  with patch('daily_learning.evaluate_daily',return_value={}) as evaluate:
   reports=env['learn'](SimpleNamespace(store=store))
  self.assertEqual(reports[0]['samples'],2)
  self.assertEqual(len(evaluate.call_args.args[0]),2)
  self.assertEqual(store.get('pick_reliability_evaluation','latest')['examples'],2)
  self.assertEqual(store.items('model'),[])
 def test_every_combo_mode_default_and_inputs_version_are_original(self):
  calls=[n for n in ast.walk(TREE) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='get' and len(n.args)>=3 and isinstance(n.args[0],ast.Constant) and n.args[0].value=='combo_ranking']
  self.assertEqual(len(calls),3)
  for call in calls:self.assertEqual(ast.literal_eval(call.args[2]),'original')
  store=fixtures.populated(6)
  self.assertEqual(fixtures.env['combo_inputs_version'](store,fixtures.REF)[1],'original')
  pool,_=fixtures.env['combo_pool'](store,fixtures.REF)
  self.assertTrue(pool)
  snapshot=fixtures.env['combo_snapshot'](pool[:2],'balanced-v1',fixtures.REF)
  self.assertEqual(snapshot['ranking_model']['version'],'original-gap-v1')
if __name__=='__main__':unittest.main()
