import copy,unittest
from market_compare import compare_no_vig
from result_scopes import standard_playable_rows
import pick_reliability
class SemanticMarketTests(unittest.TestCase):
 def setUp(self):
  self.card={"Player":"Player One","Game":"Team A vs Team B","Start (UTC)":"2026-11-17T23:00:00Z","Period":"fullgame","Line":20.5,"Stat Key":["PTS"],"Platform":"prizepicks","Market ID":"ppfeed:any","Line Type":"Goblin"}
  self.rows=[dict(self.card,**{"Platform":"draftkings","Fixture ID":"dk_game","Market ID":"dk_points","Line Type":"Standard","Direction":d,"Odds":p,"Start (UTC)":"2026-11-17T23:05:00Z"}) for d,p in (("MORE",-135),("LESS",105))]
 def compare(self,rows=None,card=None):
  clean=lambda x:"".join(c for c in str(x).lower() if c.isalnum())
  return compare_no_vig(card or self.card,self.rows if rows is None else rows,clean,clean,lambda c:c["Stat Key"],lambda c:None)
 def test_cross_provider_matching_does_not_require_shared_ids_or_tiers(self):
  result=self.compare()
  self.assertIsNotNone(result)
  self.assertAlmostEqual(result["more"]+result["less"],1)
 def test_exact_line_stat_period_and_fixture_are_required(self):
  for change in ({"Line":21.5},{"Stat Key":["REB"]},{"Period":"1h"},{"Game":"Team A vs Team C"},{"Start (UTC)":"2026-11-18T23:00Z"}):
   with self.subTest(change=change):
    self.assertIsNone(self.compare(card=dict(self.card,**change)))
 def test_one_sided_and_conflicting_price_pairs_are_not_invented(self):
  self.assertIsNone(self.compare(rows=self.rows[:1]))
  bad=copy.deepcopy(self.rows)
  other=dict(bad[0],Odds=-150)
  self.assertIsNone(self.compare(rows=bad+[other]))
 def test_reversed_team_order_is_allowed(self):
  self.assertIsNotNone(self.compare(card=dict(self.card,Game="Team B vs Team A")))
class ResultsScopeTests(unittest.TestCase):
 def row(self,identifier,line,outcome,created):
  return {"ID":identifier,"Event":"event","Player":"Player","Platform":"prizepicks","Stat columns":["PTS"],"Period":"fullgame","Line":line,"Line type":"Standard","Selectable":True,"Grade status":"FINAL","Outcome":outcome,"Created (UTC)":created,"Start (UTC)":"2026-11-17T23:00Z"}
 def test_earliest_line_is_chosen_before_wins_are_filtered(self):
  loss=self.row("first",20.5,"LOSS","2026-11-17T20:00Z")
  win=self.row("later",19.5,"WIN","2026-11-17T21:00Z")
  self.assertEqual(standard_playable_rows([win,loss]),[loss])
 def test_pass_special_tier_and_late_rows_are_excluded(self):
  row=self.row("a",20.5,"WIN","2026-11-17T20:00Z")
  for change in ({"Selectable":False},{"Line type":"Goblin"},{"Created (UTC)":"2026-11-17T23:00Z"},{"Grade status":"PENDING"}):
   self.assertEqual(standard_playable_rows([dict(row,**change)]),[])
 def test_reliability_features_do_not_depend_on_stripped_context(self):
  card={"Line Type":"Standard","Stat Key":["PTS"]}
  result={"projection":22,"baseline":20,"minutes":30,"ranking_games":12,"direction":"MORE","game_context":{"home_spread":-10},"roster":{"large":"payload"}}
  stripped={k:v for k,v in result.items() if k not in ("game_context","roster")}
  self.assertEqual(pick_reliability.features(card,result),pick_reliability.features(card,stripped))
