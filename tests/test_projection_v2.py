import ast,copy,json,unittest
from unittest.mock import Mock
import pandas as pd
import projection_v2 as v2
import test_combo_regression as fixtures

class ReplayTests(unittest.TestCase):
 def setup_data(self):
  store=fixtures.populated(2)
  tree=fixtures.tree
  node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="score_frame")
  env=dict(fixtures.env,engine_team_key=lambda x:str(x).lower())
  exec(compile(ast.Module(body=[node],type_ignores=[]),"app.py","exec"),env)
  rows=[dict(MIN=30,PTS=24,GAME_DATE=f"2026-11-{d:02d}T02:00Z",GAME_ID=str(d),SEASON="2026-27") for d in range(1,9)]
  for key,f in store.items("forecast"):
   f["season"]="2026-27"
   f["result"].update(games=json.dumps(rows),team="Team A",components=[{"Stat":"PTS","Pace factor":1.,"Position factor":1.}],estimated_hit_probability=.6)
   store.put("forecast",key,f)
   store.put("grade",key,dict(status="FINAL",outcome="WIN",actual=25))
  return store,env
 def test_readonly_and_cutoff_excludes_future_or_invalid_history(self):
  store,env=self.setup_data()
  before=copy.deepcopy(store.records)
  report=v2.run_backtest(store,env)
  self.assertEqual(report["picks"],2)
  self.assertEqual(store.records,before)
  for key,f in store.items("forecast"):
   rows=json.loads(f["result"]["games"])
   rows.append(dict(MIN=60,PTS=999,GAME_DATE="2026-11-10T02:00Z",GAME_ID="future",SEASON="2026-27"))
   f["result"]["games"]=json.dumps(rows);store.put("forecast",key,f)
  later=v2.run_backtest(store,env)
  self.assertEqual(report["v2_mae"],later["v2_mae"])
 def test_late_predictions_and_unoffered_candidate_sides_excluded(self):
  store,env=self.setup_data()
  for key,f in store.items("forecast"):
   f["created_at"]=f["card"]["Start (UTC)"]
   store.put("forecast",key,f)
  report=v2.run_backtest(store,env)
  self.assertEqual(report["picks"],0)
  self.assertEqual(report["excluded"]["late_or_missing_timestamp"],2)
 def test_install_leaves_live_methods_untouched(self):
  class Workflow:
   calculate=Mock()
   enrich=Mock()
  a,b=Workflow.calculate,Workflow.enrich
  env={"calculate_player_projection":object()}
  original=dict(env)
  v2.install(env,Workflow)
  self.assertEqual(env,original)
  self.assertIs(Workflow.calculate,a)
  self.assertIs(Workflow.enrich,b)
 def test_count_probabilities_handle_zero_line_and_integer_push(self):
  more=v2._discrete_tail_probability(2,2,2,"MORE")
  less=v2._discrete_tail_probability(2,2,2,"LESS")
  self.assertAlmostEqual(more+less,1)
  self.assertGreater(v2._discrete_tail_probability(2,2,0,"MORE"),.98)
 def test_foul_trouble_outlier_and_team_total_sign(self):
  minutes,_=v2.estimate_robust_minutes(pd.DataFrame({"MIN":[16,34,34,34,34,34]}))
  self.assertGreater(minutes,31)
  ctx=dict(available=True,home="A",away="B",home_spread=-10,total=220)
  adv=pd.DataFrame([dict(TEAM_NAME="A",OFF_RATING=110,PACE=100),dict(TEAM_NAME="B",OFF_RATING=110,PACE=100)])
  home,_=v2.vegas_environment_factor("A","PTS",ctx,adv,str.lower)
  away,_=v2.vegas_environment_factor("B","PTS",ctx,adv,str.lower)
  self.assertGreater(home,1)
  self.assertLess(away,1)

class BoundedReadTests(unittest.TestCase):
 def test_batches_are_bounded_and_missing_keys_do_not_fetch_individually(self):
  class Store:
   def __init__(self):self.calls=[]
   def query(self,sql,args,fetch):
    self.calls.append(args)
    return [(key,json.dumps({"key":key})) for key in args[1:]]
   def get(self,*args):raise AssertionError("No individual remote reads")
  store=Store()
  found=v2._batch_fetch(store,"source",[str(i) for i in range(121)])
  self.assertEqual(len(found),121)
  self.assertEqual(len(store.calls),3)
  self.assertTrue(all(len(args)<=51 for args in store.calls))
 def test_standard_scope_never_includes_other_platforms_or_special_tiers(self):
  store,env=ReplayTests().setup_data()
  grades=dict(store.items("grade"))
  for key,f in store.items("forecast"):
   f["card"]["Line Type"]="Goblin";store.put("forecast",key,f)
  self.assertEqual(v2._replay_candidates(store,env,grades,600,"standard_one_per_stat"),[])
  self.assertEqual(len(v2._replay_candidates(store,env,grades,600,"all_selectable")),2)
