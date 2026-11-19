import ast,copy,math,unittest
from pathlib import Path
from unittest.mock import MagicMock,Mock
import pandas as pd
TREE=ast.parse((Path(__file__).resolve().parents[1]/'app.py').read_text())
WEIGHTS={'PTS':1.,'REB':1.2,'AST':1.5,'STL':3.,'BLK':3.,'TOV':-1.}
def compile_fn(name,env):
 node=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name==name)
 exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),env)
class FantasyOverviewRatesTests(unittest.TestCase):
 def test_overview_calls_fantasy_rates_with_saved_history(self):
  st=MagicMock();st.session_state={};st.segmented_control.return_value='Overview'
  back,save=MagicMock(),MagicMock();back.button.return_value=False;save.button.return_value=False;st.columns.return_value=(back,save)
  history=pd.DataFrame();frozen={'id':'original','result':{'direction':'MORE','projection':45.,'estimated_hit_probability':.56}}
  card={'Player':'Player A','Line':40.5,'Platform':'prizepicks','Line Type':'Standard'}
  rates=Mock();env={'st':st,'workflow_store':MagicMock(),'ui_matched_forecast':lambda *a,**k:frozen,'identity':lambda c:'key','line_freshness':lambda *a,**k:(True,''),'pick_is_selectable':lambda *a:True,'ui_name':str,'ui_photo':lambda f:'','ui_number':lambda v:float(v) if v is not None else None,'ui_text':str,'ui_html':str,'ui_tier_marker':lambda c:'','ui_stat':lambda c:'Fantasy Score','ui_card_badges':lambda *a:'','ui_history':lambda *a:history,'ui_recent_chart':Mock(),'ui_hit_strip':Mock(),'ui_drivers':Mock(),'ui_frozen_sources':Mock(),'fantasy_weights':lambda c:WEIGHTS,'ui_fantasy_rates':rates}
  env['workflow_store'].items.return_value=[]
  compile_fn('ui_detail',env);env['ui_detail'](card,{})
  rates.assert_called_once_with(card,history,frozen['result']);st.warning.assert_not_called()
 def test_rate_table_projected_metrics_and_six_components_render(self):
  st=MagicMock();a,b=MagicMock(),MagicMock();st.columns.return_value=(a,b)
  result={'projection':60.,'minutes':30.,'components':[{'Stat':stat,'Projection':2.} for stat in WEIGHTS]};original=copy.deepcopy(result)
  history=pd.DataFrame({'GAME_DATE':['2026-01-01','2026-01-02'],'GAME_ID':['a','b'],'MIN':[30.,30.],'SEASON':['2025-26']*2})
  env={'st':st,'pd':pd,'math':math,'fantasy_weights':lambda c:WEIGHTS,'score_frame':lambda h,c:pd.Series([30.,45.],index=h.index),'current_season':lambda:'2025-26'}
  compile_fn('ui_number',env);compile_fn('ui_fantasy_rates',env)
  env['ui_fantasy_rates']({'Start (UTC)':'2100-01-01T00:00Z'},history,result)
  table=st.dataframe.call_args_list[0].args[0]
  self.assertEqual(table.iloc[0]['Historical FP/min'],1.25);self.assertEqual(table.iloc[0]['Historical FP/5 min'],6.25)
  a.metric.assert_called_once_with('Projected FP/min','2.000');b.metric.assert_called_once_with('Projected minutes','30.0')
  st.expander.assert_called_once_with('Six-stat fantasy calculation')
  self.assertEqual(len(st.dataframe.call_args_list[1].args[0]),6);self.assertEqual(result,original)
if __name__=='__main__':unittest.main()
