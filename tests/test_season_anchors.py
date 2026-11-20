import ast,copy,unittest
from pathlib import Path
import pandas as pd
from season_anchors import pregame_season_totals
from projection_v2 import shrunk_per_minute_rate

class SeasonAnchorTests(unittest.TestCase):
 def history(self):
  current=[dict(GAME_ID=str(i),GAME_DATE=pd.Timestamp('2026-11-01',tz='UTC')+pd.Timedelta(days=i),SEASON='2026-27',MIN=30,PTS=20 if i<5 else 10) for i in range(20)]
  prior=[dict(GAME_ID='prior'+str(i),GAME_DATE=pd.Timestamp('2026-03-01',tz='UTC')+pd.Timedelta(days=i),SEASON='2025-26',MIN=30,PTS=25) for i in range(10)]
  return pd.DataFrame(current+prior).sort_values('GAME_DATE',ascending=False)
 def test_saved_full_anchors_reproduce_full_history_from_l15_without_double_counting(self):
  history=self.history();cutoff=pd.Timestamp('2026-11-25',tz='UTC');current=history[history.SEASON.eq('2026-27')]
  totals=pregame_season_totals(history,'2026-27',cutoff)
  complete=shrunk_per_minute_rate(history,current,'PTS','2026-27',cutoff)[0]
  recent=current.head(15)
  restored=shrunk_per_minute_rate(recent,recent,'PTS','2026-27',cutoff,totals)[0]
  legacy=shrunk_per_minute_rate(recent,recent,'PTS','2026-27',cutoff)[0]
  self.assertAlmostEqual(complete,restored);self.assertNotAlmostEqual(restored,legacy)
  self.assertEqual(totals['current_season_totals']['stats']['PTS']['games'],20)
  self.assertEqual(totals['prior_season_totals']['stats']['PTS']['games'],10)
 def test_future_data_duplicates_and_invalid_minutes_never_enter_summary(self):
  history=self.history();before=history.copy(deep=True);cutoff=pd.Timestamp('2026-11-15',tz='UTC')
  future=dict(history.iloc[0],GAME_ID='future',GAME_DATE=cutoff+pd.Timedelta(days=1),PTS=999)
  invalid=dict(history.iloc[-1],GAME_ID='invalid',MIN=0,PTS=999)
  mixed=pd.concat([history,history.iloc[[0]],pd.DataFrame([future,invalid])])
  self.assertEqual(pregame_season_totals(mixed,'2026-27',cutoff),pregame_season_totals(history,'2026-27',cutoff))
  pd.testing.assert_frame_equal(history,before)
 def test_unavailable_or_future_anchors_leave_legacy_rate_unchanged(self):
  history=self.history();cutoff=pd.Timestamp('2026-11-25',tz='UTC');recent=history.head(15)
  legacy=shrunk_per_minute_rate(recent,recent,'PTS','2026-27',cutoff)[0]
  totals=pregame_season_totals(history,'2026-27',cutoff)
  for key in totals:totals[key]['as_of']='2026-12-01T00:00Z'
  self.assertEqual(legacy,shrunk_per_minute_rate(recent,recent,'PTS','2026-27',cutoff,totals)[0])
  totals=pregame_season_totals(history,'2026-27',cutoff)
  totals['current_season_totals']['stats']['PTS']['recent_game_ids']=['different']
  totals.pop('prior_season_totals')
  self.assertEqual(legacy,shrunk_per_minute_rate(recent,recent,'PTS','2026-27',cutoff,totals)[0])
 def test_live_wrapper_saves_totals_without_changing_projection_or_fantasy_math(self):
  tree=ast.parse(Path('app.py').read_text());node=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='calculate_player_projection'][-1]
  original={'projection':10.,'components':[{'Stat':'PTS','Projection':10.}]}
  env={'pd':pd,'fantasy_weights':lambda c:None,'_base_calculate_player_projection':lambda *a:copy.deepcopy(original)}
  exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),env)
  out=env['calculate_player_projection'](self.history(),{'Start (UTC)':'2030-01-01T00:00Z'},'2026-27','A','PG')
  self.assertEqual(out['projection'],10.);self.assertIn('prior_season_totals',out)
