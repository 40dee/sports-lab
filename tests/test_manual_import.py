import ast,copy,unittest
from types import SimpleNamespace
from unittest.mock import Mock
import test_combo_regression as fixtures
from test_request_control import env as request_env

env=dict(fixtures.env)
env.update(numeric_line=request_env['numeric_line'],MANUAL_STATS=('Points','Fantasy Score'))
for node in fixtures.tree.body:
 if isinstance(node,ast.FunctionDef) and node.name in ('manual_line_cards','manual_games','import_manual_lines','merge_scan_rows'):
  exec(compile(ast.Module(body=[node],type_ignores=[]),str(fixtures.SOURCE),'exec'),env)
REF=fixtures.REF
class ManualImportTests(unittest.TestCase):
 def setUp(self):
  self.store=fixtures.Memory()
  self.game={'id':'espn1','start':'2026-11-10T02:00:00Z','game':'Team A vs Team B',
   'teams':[{'id':'1','displayName':'Team A'},{'id':'2','displayName':'Team B'}]}
  self.wf=SimpleNamespace(store=self.store,e={'engine_team_key':str.lower},
   event_for=Mock(return_value=({'id':'espn1','status':{'type':{'state':'pre'}}},{})),
   player=Mock(return_value=({'id':'1'},{'id':'player1','displayName':'Real Player'},REF)))
  self.entry={'Player':'Real Player','Stat':'Points','Line':20.5,'Tier':'Goblin','Sides':'MORE'}
 def test_one_direction_and_source_provenance_retained_without_odds_call(self):
  card=env['manual_line_cards'](self.wf,self.game,[self.entry],True,REF)[0]
  self.assertEqual(card['Available Sides'],['MORE'])
  self.assertEqual(card['Line Source'],'Manual confirmation')
  self.assertFalse(env['pick_is_selectable'](card,{'direction':'LESS'}))
  self.assertTrue(env['pick_is_selectable'](card,{'direction':'MORE','selection_allowed':False}))
 def test_fantasy_requires_actual_line_no_stat_inference(self):
  entry=dict(self.entry,Stat='Fantasy Score',Line=40.5,Sides='BOTH',Tier='Standard')
  card=env['manual_line_cards'](self.wf,self.game,[entry],True,REF)[0]
  self.assertEqual(card['Stat Key'],'fantasy_score');self.assertEqual(card['Line'],40.5)
 def test_rejects_unconfirmed_started_or_wrong_event_before_any_write(self):
  with self.assertRaises(ValueError):env['manual_line_cards'](self.wf,self.game,[self.entry],False,REF)
  with self.assertRaises(ValueError):env['manual_line_cards'](self.wf,self.game,[self.entry],True,'2026-11-10T03:00Z')
  self.wf.event_for.return_value=({'id':'other','status':{'type':{'state':'pre'}}},{})
  with self.assertRaises(ValueError):env['manual_line_cards'](self.wf,self.game,[self.entry],True,REF)
  self.assertEqual(self.store.records,{})
 def test_invalid_tiers_sides_duplicates_and_nan_fail_entire_import(self):
  for updates in ({'Tier':'Unverified'},{'Sides':'guess'},{'Line':float('nan')},{'Player':''}):
   entry=dict(self.entry,**updates)
   # Empty names must not be allowed to silently become another roster player.
   with self.subTest(updates=updates),self.assertRaises(ValueError):
    env['manual_line_cards'](self.wf,self.game,[entry],True,REF)
  with self.assertRaises(ValueError):env['manual_line_cards'](self.wf,self.game,[self.entry,self.entry],True,REF)
 def test_provider_refresh_keeps_independently_confirmed_manual_lines(self):
  card=env['manual_line_cards'](self.wf,self.game,[self.entry],True,REF)[0]
  env['today_pregame']=lambda *_:True
  try:self.assertIn(card,env['merge_scan_rows']([card],[],{card['Fixture ID']}))
  finally:env['today_pregame']=fixtures.env['today_pregame']
