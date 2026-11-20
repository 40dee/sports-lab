import unittest
import pandas as pd
from predictive_context import build
class PredictiveContextTests(unittest.TestCase):
 def history(self):
  return pd.DataFrame([{'GAME_ID':str(i),'GAME_DATE':f'2026-11-{19-i:02}T03:00Z','MIN':35 if i<5 else 25} for i in range(10)])
 def test_rest_and_minutes_shift_use_pregame_history_only(self):
  history=self.history();before=history.copy(deep=True)
  c={'Start (UTC)':'2026-11-20T03:00Z'}
  future=pd.DataFrame([{'GAME_ID':'future','GAME_DATE':'2026-11-20T04:00Z','MIN':70}])
  r=build(pd.concat([history,history.iloc[[0]],future]),c,'2026-11-19T20:00Z')
  self.assertTrue(r['played_previous_day']);self.assertEqual(r['days_since_last_appearance'],1)
  self.assertEqual(r['minutes_shift'],10);self.assertIsNone(r['starting_status']);self.assertIsNone(r['usage_adjustment'])
  pd.testing.assert_frame_equal(history,before)
 def test_future_injury_context_is_rejected_and_missing_is_unknown(self):
  c={'Start (UTC)':'2026-11-20T03:00Z'}
  r=build(self.history(),c,'2026-11-19T20:00Z',{'available':True,'checked_at':'2026-11-19T21:00Z','out_teammates':[{}]})
  self.assertIsNone(r['out_teammates'])
  r=build(self.history(),c,'2026-11-19T20:00Z',{'available':True,'checked_at':'2026-11-19T19:00Z','out_teammates':[{}]})
  self.assertEqual(r['out_teammates'],1)
 def test_missing_history_does_not_infer_rest_or_role(self):
  r=build(None,{'Start (UTC)':'2026-11-20T03:00Z'},'2026-11-19T20:00Z')
  self.assertIsNone(r['played_previous_day']);self.assertIsNone(r['minutes_shift'])
