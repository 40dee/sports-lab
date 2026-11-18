import ast,copy,unittest
from pathlib import Path
import pandas as pd
TREE=ast.parse((Path(__file__).resolve().parents[1]/'app.py').read_text())
NODE=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name=='scheduled_board_refresh_due')
ENV={'pd':pd,'SLATE_TIMEZONE':'America/Los_Angeles','utc_timestamp':lambda v:pd.to_datetime(v,utc=True,errors='coerce')}
exec(compile(ast.Module(body=[NODE],type_ignores=[]),'app.py','exec'),ENV)
def local(time,day='2026-11-18'):
 return pd.Timestamp(day+' '+time,tz='America/Los_Angeles')
class ScheduledCaptureTests(unittest.TestCase):
 def due(self,time,starts,ledger=None,day='2026-11-18'):
  return ENV['scheduled_board_refresh_due'](local(time,day),[local(start,day) for start in starts],ledger or {})
 def test_normal_evening_slate_waits_until_130(self):
  starts=['16:00','16:00','16:30','17:00','18:00','19:00']
  for time in ('08:00','10:00','13:29:59'):self.assertFalse(self.due(time,starts))
  for time in ('13:30','13:40','15:59'):self.assertTrue(self.due(time,starts))
 def test_one_noon_game_does_not_spend_evening_capture(self):
  starts=['12:00']+['16:00']*6
  for time in ('08:00','10:00','11:59','13:29:59'):self.assertFalse(self.due(time,starts))
  self.assertTrue(self.due('13:30',starts))
 def test_sunday_early_heavy_slate_captures_two_hours_before_first_game(self):
  starts=['10:00','10:30','11:00','12:00','12:30','16:00','18:00']
  self.assertFalse(self.due('07:59:59',starts,day='2026-11-22'))
  self.assertTrue(self.due('08:00',starts,day='2026-11-22'))
 def test_main_ties_and_three_main_games_take_priority(self):
  for starts in (['12:00','13:00','16:00','17:00'],['10:00']*5+['16:00']*3):
   self.assertFalse(self.due('08:00',starts));self.assertTrue(self.due('13:30',starts))
 def test_all_early_and_two_pm_boundary(self):
  self.assertFalse(self.due('07:59',['10:00','12:00']))
  self.assertTrue(self.due('08:00',['10:00','12:00']))
  self.assertFalse(self.due('10:00',['13:30']))
  self.assertTrue(self.due('11:30',['13:30']))
  self.assertFalse(self.due('11:29',['14:00']))
  self.assertTrue(self.due('11:30',['14:00']))
 def test_pacific_daylight_saving_and_utc_inputs(self):
  for day in ('2026-07-12','2026-11-22'):
   self.assertFalse(self.due('13:29',['16:00'],day=day))
   self.assertTrue(self.due('13:30',['16:00'],day=day))
 def test_refresh_guard_invalid_and_finished_games_and_no_mutations(self):
  fn=ENV['scheduled_board_refresh_due'];starts=[local('16:00')];ledger={'refreshes':1};before=copy.deepcopy(ledger)
  self.assertFalse(fn(local('13:30'),starts,ledger));self.assertEqual(ledger,before)
  self.assertEqual(starts,[local('16:00')])
  for values in ([],['bad'],[local('08:00')],[local('13:30')]):
   self.assertFalse(fn(local('13:30'),values,{}))
  self.assertTrue(fn(local('13:30'),['bad',local('16:00')],{}))
if __name__=='__main__':unittest.main()
