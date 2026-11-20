import ast,unittest
from pathlib import Path
from unittest.mock import MagicMock
import pandas as pd
import test_request_control as controls
import test_combo_regression as fixtures
TREE=ast.parse(Path('app.py').read_text())
class DailyBoardUiTests(unittest.TestCase):
 def render(self,sides):
  st=MagicMock();st.session_state={};left,right=MagicMock(),MagicMock();left.button.return_value=False;right.button.return_value=False;st.columns.return_value=(left,right)
  store=MagicMock();store.get.return_value={};store.items.return_value=[]
  card={'Platform':'prizepicks','Line Type':'Standard','Available Sides':sides,'Player':'Player','Game':'A vs B','Line':4,'Start (UTC)':'2026-11-20T01:10:00Z','Odds checked (UTC)':'2026-11-19T23:04:00Z'}
  result={'direction':'MORE','projection':4.4,'estimated_hit_probability':.57}
  env=dict(fixtures.env,st=st,workflow_store=store,pd=pd,SLATE_TIMEZONE='America/Los_Angeles',
    ui_name=str,ui_photo=lambda f:'',ui_text=str,ui_stat=lambda c:'REB',ui_tier_marker=lambda c:'',
    player_health_key=lambda c:'player',slate_day=lambda:'2026-11-19',identity=lambda c:'key',
    model_direction_label=lambda r:'↑ MORE',pick_display_label=lambda c,r:'PASS',fantasy_weights=lambda c:None,pick_side_note=lambda *a:'Side not offered',
    consensus_edge_info=lambda *a:{},ui_card_badges=lambda *a:'',ui_sharp_side_badge=lambda *a:'',ui_line_edge_badge=lambda *a:'',ui_sparkline=lambda *a:'',
    line_freshness=lambda c,**kw:controls.env['line_freshness'](c,'2026-11-20T00:54:00Z',**kw))
  for name in ('ui_number','ui_html','ui_card'):
   node=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name==name)
   exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),env)
  env['ui_card'](card,{'result':result})
  return st.markdown.call_args.args[0],right.button.call_args.kwargs
 def test_same_day_afternoon_capture_shows_probability_and_enables_save(self):
  html,button=self.render(['MORE','LESS'])
  self.assertIn('57%</b>',html);self.assertIn('03:04 PM PT',html);self.assertFalse(button['disabled'])
 def test_unoffered_side_remains_disabled(self):
  html,button=self.render(['LESS'])
  self.assertNotIn('57%</b>',html);self.assertTrue(button['disabled'])
if __name__=='__main__':unittest.main()
