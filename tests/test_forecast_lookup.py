import ast,unittest,json,hashlib
from pathlib import Path
from decimal import Decimal
import pandas as pd
SOURCE=Path(__file__).resolve().parents[1]/'app.py'
names={'identity','utc_timestamp','market_signature','unique_forecasts','forecast_index','saved_prediction'}
env={'pd':pd,'Decimal':Decimal,'hashlib':hashlib,'encode':json.dumps,
     'market_columns':lambda c:[c['Stat']], 'fantasy_weights':lambda c:None}
tree=ast.parse(SOURCE.read_text())
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[]),str(SOURCE),'exec'),env)
class ForecastLookupTests(unittest.TestCase):
 def setUp(self):
  self.card={'Game':'Team A vs Team B','Start (UTC)':'2026-11-13T00:00Z','Player':'Player One','Stat':'PTS','Line':12.5,'Platform':'prizepicks','Period':'result','Fixture ID':'fixture','Stat Key':'PTS','Line Type':'Standard'}
  self.f={'id':'original','card':dict(self.card),'event_id':'123','created_at':'2026-11-12T18:00Z','result':{'projection':14}}
 def find(self,card,records=None):
  return env['saved_prediction'](env['forecast_index'](records or [('original',self.f)]),card)
 def test_reversed_teams_and_ten_minute_provider_tipoff_reuse_original(self):
  c=dict(self.card,Game='Team B vs Team A',**{'Start (UTC)':'2026-11-13T00:10Z'})
  self.assertIs(self.find(c),self.f)
  self.assertEqual(self.f['card']['Start (UTC)'],'2026-11-13T00:00Z')
 def test_different_day_or_large_time_difference_is_rejected(self):
  for start in ('2026-11-14T00:00Z','2026-11-13T00:16Z'):
   self.assertIsNone(self.find(dict(self.card,**{'Start (UTC)':start})))
 def test_different_market_player_team_and_platform_are_rejected(self):
  for changes in ({'Line':13.5},{'Player':'Other Player'},{'Platform':'draftkings'},{'Game':'Team A vs Team C'},{'Stat':'REB'}):
   self.assertIsNone(self.find(dict(self.card,**changes)))
 def test_ambiguous_nearby_events_are_rejected(self):
  other=dict(self.f,id='other',event_id='456')
  self.assertIsNone(self.find(dict(self.card,**{'Start (UTC)':'2026-11-13T00:10Z'}),[('original',self.f),('other',other)]))
 def test_late_prediction_is_rejected_for_earlier_provider_start(self):
  self.f['created_at']='2026-11-12T23:59Z'
  self.assertIsNone(self.find(dict(self.card,**{'Start (UTC)':'2026-11-12T23:55Z'})))
 def test_old_id_collision_does_not_match_other_day(self):
  c=dict(self.card,**{'Start (UTC)':'2026-11-14T00:00Z'})
  self.assertIsNone(env['saved_prediction']({env['identity'](c):self.f},c))
if __name__=='__main__':unittest.main()
