import ast,math,unittest
from pathlib import Path
from html import escape
from types import SimpleNamespace
from unittest.mock import Mock
import pandas as pd
SOURCE=Path(__file__).resolve().parents[1]/'app.py'
TREE=ast.parse(SOURCE.read_text())
class MobileRedesignTests(unittest.TestCase):
 def env(self):
  env={'math':math,'pd':pd,'ui_text':lambda x:escape(str(x))}
  for name in ('ui_number','ui_sparkline','ui_card_badges','compact_forecast'):
   node=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name==name)
   exec(compile(ast.Module(body=[node],type_ignores=[]),str(SOURCE),'exec'),env)
  return env
 def test_recent_values_survive_compact_projection(self):
  env=self.env();values=list(range(10));record=env['compact_forecast']({'result':{'recent_values':values,'roster':{}},'snapshot':{}})
  self.assertEqual(record['result']['recent_values'],values)
  # Both SQL dialects preserve all result fields except roster/context.
  node=next(n for n in TREE.body if isinstance(n,ast.ClassDef) and n.name=='Store')
  method=next(n for n in node.body if isinstance(n,ast.FunctionDef) and n.name=='forecast_items')
  self.assertNotIn('recent_values',ast.unparse(method))
 def test_sparkline_direction_ties_and_missing_history(self):
  env=self.env();render=env['ui_sparkline']
  self.assertEqual(render(None,10,'MORE'),'')
  self.assertEqual(render([float('nan')],10,'MORE'),'')
  for side in ('MORE','LESS'):
   svg=render([8,10,12],10,side)
   self.assertEqual(svg.count('<rect'),3)
   self.assertIn('#23e689',svg);self.assertIn('#e04f5f',svg);self.assertIn('#91acbc',svg)
  self.assertEqual(render(list(range(20)),10,'MORE').count('<rect'),10)
 def test_consensus_badges_derive_sharp_weights_without_rewriting_saved_result(self):
  import copy
  result={'market':{'more':.5,'less':.5,'book_count':2,'books':[{'book':'pinnacle','more':.6,'less':.4},{'book':'bovada','more':.4,'less':.6}]}}
  original=copy.deepcopy(result)
  self.assertIn('✓ Consensus 55%',self.env()['ui_card_badges'](result,'MORE'))
  self.assertEqual(result,original)
 def test_saved_badge_thresholds_and_escaping(self):
  render=self.env()['ui_card_badges']
  for value,symbol in ((.535,'✓'),(.489,'⚠️'),(.51,'')):
   html=render({'market':{'more':value,'book_count':2}},'MORE')
   self.assertIn('Consensus',html)
   if symbol:self.assertIn(symbol,html)
   else:self.assertNotIn('✓',html);self.assertNotIn('⚠️',html)
  self.assertEqual(render({},'MORE'),'')
  html=render({'teammate_context':{'available':True,'out_teammates':[{'name':'<unsafe>'}]}},'MORE')
  self.assertIn('🏥',html);self.assertNotIn('<unsafe>',html)
if __name__=='__main__':unittest.main()
