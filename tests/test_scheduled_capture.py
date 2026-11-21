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
 def test_normal_evening_slate_waits_until_245(self):
  starts=['16:00','16:00','16:30','17:00','18:00','19:00']
  for time in ('08:00','10:00','14:44:59'):self.assertFalse(self.due(time,starts))
  for time in ('14:45','14:50','15:59'):self.assertTrue(self.due(time,starts))
 def test_one_noon_game_does_not_spend_evening_capture(self):
  starts=['12:00']+['16:00']*6
  for time in ('08:00','10:00','11:59','14:44:59'):self.assertFalse(self.due(time,starts))
  self.assertTrue(self.due('14:45',starts))
 def test_sunday_early_heavy_slate_captures_75_minutes_before_first_game(self):
  starts=['10:00','10:30','11:00','12:00','12:30','16:00','18:00']
  self.assertFalse(self.due('08:44:59',starts,day='2026-11-22'))
  self.assertTrue(self.due('08:45',starts,day='2026-11-22'))
 def test_main_ties_and_three_main_games_take_priority(self):
  for starts in (['12:00','13:00','16:00','17:00'],['10:00']*5+['16:00']*3):
   self.assertFalse(self.due('08:00',starts));self.assertTrue(self.due('14:45',starts))
 def test_all_early_and_two_pm_boundary(self):
  self.assertFalse(self.due('08:44:59',['10:00','12:00']))
  self.assertTrue(self.due('08:45',['10:00','12:00']))
  self.assertFalse(self.due('10:00',['13:30']))
  self.assertTrue(self.due('12:15',['13:30']))
  self.assertFalse(self.due('12:44',['14:00']))
  self.assertTrue(self.due('12:45',['14:00']))
 def test_pacific_daylight_saving_and_utc_inputs(self):
  for day in ('2026-07-12','2026-11-22'):
   self.assertFalse(self.due('14:44:59',['16:00'],day=day))
   self.assertTrue(self.due('14:45',['16:00'],day=day))
 def test_refresh_guard_invalid_and_finished_games_and_no_mutations(self):
  fn=ENV['scheduled_board_refresh_due'];starts=[local('16:00')];ledger={'refreshes':1};before=copy.deepcopy(ledger)
  self.assertFalse(fn(local('14:45'),starts,ledger));self.assertEqual(ledger,before)
  self.assertEqual(starts,[local('16:00')])
  for values in ([],['bad'],[local('08:00')],[local('14:45')]):
   self.assertFalse(fn(local('14:45'),values,{}))
  self.assertTrue(fn(local('14:45'),['bad',local('16:00')],{}))
 def test_scheduled_scoreboard_includes_late_utc_tipoffs_but_only_today_pacific(self):
  helper=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name=='scheduled_scoreboard_starts')
  today_node=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name=='today_pregame')
  slate_node=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name=='slate_day')
  for day in ('2026-07-12','2026-11-22'):
   late=local('22:00',day).tz_convert('UTC')
   tomorrow=local('00:30',day)+pd.DateOffset(days=1)
   from unittest.mock import Mock
   fetch=Mock(side_effect=[
    {'events':[{'date':local('08:00',day).isoformat()},{'date':'bad'}]},
    {'events':[{'date':late.isoformat()},{'date':tomorrow.isoformat()}]}])
   env=dict(ENV)
   exec(compile(ast.Module(body=[slate_node,today_node,helper],type_ignores=[]),'app.py','exec'),env)
   starts=env['scheduled_scoreboard_starts'](fetch,'offline/',local('14:45',day))
   self.assertEqual(fetch.call_count,2)
   begin=pd.Timestamp(day,tz='America/Los_Angeles')
   expected=[begin.strftime('%Y%m%d'),(begin+pd.DateOffset(days=1)).strftime('%Y%m%d')]
   self.assertEqual([c.args[1]['dates'] for c in fetch.call_args_list],expected)
   self.assertEqual(starts,[late])
if __name__=='__main__':unittest.main()
