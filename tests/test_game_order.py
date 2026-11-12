import ast, copy, unittest
from pathlib import Path
import pandas as pd
TREE=ast.parse(Path(__file__).resolve().parents[1].joinpath('app.py').read_text())
env={'pd':pd,'utc_timestamp':lambda v:pd.to_datetime(v,utc=True,errors='coerce')}
exec(compile(ast.Module(body=[n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name=='earliest_game_first'],type_ignores=[]),'app.py','exec'),env)
class OrderTests(unittest.TestCase):
 def test_tipoff_first_preserves_rank_and_every_line(self):
  cards=[{'id':'late','Start (UTC)':'2026-11-13T02:10Z'},
         {'id':'early-top','Start (UTC)':'2026-11-13T00:10Z'},
         {'id':'early-next','Start (UTC)':'2026-11-12T16:10-08:00'}]
  original=copy.deepcopy(cards)
  result=env['earliest_game_first'](cards)
  self.assertEqual([c['id'] for c in result],['early-top','early-next','late'])
  self.assertEqual(cards,original)
 def test_unknown_start_is_last(self):
  self.assertEqual(env['earliest_game_first']([{'id':'unknown'},{'id':'valid','Start (UTC)':'2026-11-13T00:10Z'}])[0]['id'],'valid')
