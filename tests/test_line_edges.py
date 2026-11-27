import unittest,copy
from line_edges import detect
from market_compare import compare_no_vig
class LineEdgeTests(unittest.TestCase):
 def setUp(self):
  self.card={'Platform':'prizepicks','Player':'Player','Game':'A vs B','Start (UTC)':'2026-11-18T23:00Z','Period':'result','Stat Key':'PTS','Line':23.5,'Odds checked (UTC)':'2026-11-18T18:05Z'}
 def rows(self,line=24.5):
  return [dict(self.card,Platform=book,Line=line,Direction=side,Odds=-115 if side=='MORE' else -105,**{'Odds checked (UTC)':'2026-11-18T18:00Z','Provider fetched at':'2026-11-18T18:05Z','Market ID':'points'}) for book in ('draftkings','fanduel','betmgm') for side in ('MORE','LESS')]
 def compare(self,c,rows):
  clean=lambda x:str(x).lower().replace(' ','')
  return compare_no_vig(c,rows,clean,clean,lambda c:[c['Stat Key']],lambda c:None)
 def run_edge(self,rows,offered=('MORE','LESS'),columns=('PTS',)):
  return detect(self.card,{},rows,self.compare,offered,columns)
 def test_moved_line_probability_uses_pinnacle_three_to_one_weight(self):
  import math
  from statistics import NormalDist
  from unittest.mock import patch
  rows=[]
  for book,prices in (('pinnacle',(-150,150)),('bovada',(150,-150))):
   rows.extend(dict(row,Platform=book,Odds=price) for row,price in zip(self.rows()[:2],prices))
  original=copy.deepcopy(rows);edge=self.run_edge(rows)
  self.assertEqual(edge['book_count'],2);self.assertEqual(edge['side'],'MORE')
  normal=NormalDist()
  self.assertAlmostEqual(edge['probability'],normal.cdf(normal.inv_cdf(.55)+1/9.5))
  with patch.dict('line_edges.BOOK_WEIGHTS',{'pinnacle':1.,'bovada':1.}):
   equal_weight_edge=self.run_edge(rows)
   self.assertIsNotNone(equal_weight_edge)
   self.assertAlmostEqual(equal_weight_edge['probability'],normal.cdf(1/9.5))
   self.assertLess(equal_weight_edge['probability'],edge['probability'])
  self.assertEqual(rows,original)
 def test_even_money_vig_one_point_pts_move_clears_54_percent(self):
  from statistics import NormalDist
  rows=[dict(row,Odds=-110) for row in self.rows(24.5)]
  edge=self.run_edge(rows)
  self.assertIsNotNone(edge)
  self.assertEqual(edge['side'],'MORE')
  self.assertEqual(edge['pp_line'],23.5)
  self.assertEqual(edge['book_line'],24.5)
  self.assertEqual(edge['spread'],9.5)
  self.assertGreaterEqual(edge['probability'],.54)
  self.assertAlmostEqual(edge['probability'],NormalDist().cdf(1/9.5))
 def test_pp_and_book_captures_must_be_within_thirty_minutes(self):
  for captured,expected in (('2026-11-18T17:35Z',True),('2026-11-18T17:34:59Z',False),('2026-11-18T18:35Z',True),('2026-11-18T18:35:01Z',False),(None,False)):
   with self.subTest(captured=captured):
    self.card['Odds checked (UTC)']=captured
    self.assertEqual(self.run_edge(self.rows()) is not None,expected)
  self.card.update({'Tier Source':'prizepicksapi.com','Tier Fetched At':'2026-11-18T18:05Z','Tier Observed At':'2026-11-18T16:00Z'})
  self.assertIsNone(self.run_edge(self.rows()))
 def test_more_and_less_edges_with_two_books(self):
  edge=self.run_edge(self.rows());self.assertEqual(edge['side'],'MORE');self.assertEqual(edge['book_count'],3);self.assertGreaterEqual(edge['probability'],.54)
  edge=self.run_edge(self.rows(21.5));self.assertEqual(edge['side'],'LESS');self.assertGreaterEqual(edge['probability'],.54)
 def test_requires_two_books_two_sides_and_offered_direction(self):
  self.assertIsNone(self.run_edge(self.rows()[:2]))
  self.assertIsNone(self.run_edge(self.rows()[::2]))
  self.assertIsNone(self.run_edge(self.rows(),('LESS',)))
  self.assertIsNone(self.run_edge(self.rows(21.5),('MORE',)))
  self.assertIsNone(self.run_edge(self.rows(),()))
 def test_exact_pair_identity_time_and_floors(self):
  self.assertIsNone(self.run_edge(self.rows()+self.rows(23.5)))
  for field,value in (('Player','Other'),('Game','C vs D'),('Period','firsthalf'),('Stat Key','AST'),('Provider fetched at','2026-11-18T20:00Z')):
   self.assertIsNone(self.run_edge([dict(r,**{field:value}) for r in self.rows()]))
  original=copy.deepcopy(self.card);rows=self.rows(100)
  self.assertGreater(self.run_edge(rows)['probability'],.65)
  self.assertLessEqual(self.run_edge(rows)['probability'],1.0)
  self.assertEqual(self.card,original)
  # Use a realistic half-point count market for the Poisson interpolation.
  self.card.update({'Stat Key':'FG3M','Line':2.5})
  count_edge=self.run_edge(self.rows(3.5),columns=('FG3M',))
  self.assertIsNotNone(count_edge)
  self.assertGreaterEqual(count_edge['spread'],3)
 def test_app_offered_side_guard_and_filter_integration(self):
  import ast
  from pathlib import Path
  from types import SimpleNamespace
  tree=ast.parse(Path('app.py').read_text())
  env={'st':SimpleNamespace(session_state={}), 'normalize_player':lambda v:v.lower(), 'ui_name':lambda v:v,
       'engine_no_vig':self.compare,'market_columns':lambda c:[c['Stat Key']],'fantasy_weights':lambda c:None}
  for name in ('pick_offered_sides','line_edge_info','consensus_matches_filter'):
   node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
   exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),env)
  card=dict(self.card,**{'Line Type':'Demon','Available Sides':['MORE']})
  self.assertIsNone(env['line_edge_info'](card,{},self.rows(21.5)))
  edge=env['line_edge_info'](card,{},self.rows())
  self.assertEqual(edge['side'],'MORE')
  self.assertTrue(env['consensus_matches_filter']({'line_edge':edge},'🔥 Sharp +EV (54%+)'))
  self.assertFalse(env['consensus_matches_filter']({'line_edge':edge},'✓ Sharp aligned (53.5%+)'))
  card.pop('Available Sides')
  self.assertIsNone(env['line_edge_info'](card,{},self.rows()))
 def test_integer_push_neighbors_do_not_enter_sharp_filter(self):
  import ast
  from pathlib import Path
  from types import SimpleNamespace
  tree=ast.parse(Path('app.py').read_text())
  env={'st':SimpleNamespace(session_state={}), 'normalize_player':lambda v:v.lower(), 'ui_name':lambda v:v,
       'engine_no_vig':self.compare,'market_columns':lambda c:[c['Stat Key']],'fantasy_weights':lambda c:None}
  for name in ('pick_offered_sides','line_edge_info','consensus_matches_filter'):
   node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
   exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),env)
  cases=[(4.,3.5,'MORE',-163,False),(4.,4.5,'LESS',-158,False),
         (4.,5.5,'MORE',-115,True),(23.5,24.5,'MORE',-115,True)]
  for pp,book_line,favored,price,expected in cases:
   with self.subTest(pp=pp,book=book_line,favored=favored):
    self.card['Line']=pp
    card=dict(self.card,**{'Line Type':'Standard','Available Sides':['MORE','LESS']})
    quotes=[dict(q,Odds=price if q['Direction']==favored else 123 if price<-150 else -105) for q in self.rows(book_line)]
    edge=env['line_edge_info'](card,{},quotes)
    self.assertEqual(edge is not None,expected)
    self.assertEqual(env['consensus_matches_filter']({'line_edge':edge},'🔥 Sharp +EV (54%+)'),expected)
    if edge:self.assertEqual(edge['side'],favored)
 def test_integer_full_point_boundary_near_integer_and_alternate_selection(self):
  self.card['Line']=4.
  self.assertIsNotNone(self.run_edge(self.rows(5.)))
  # An ineligible half-point line must not hide an eligible farther line.
  edge=self.run_edge(self.rows(4.5)+self.rows(5.5))
  self.assertEqual(edge['book_line'],5.5)
  self.card['Line']=4.0000001
  self.assertIsNone(self.run_edge(self.rows(4.5)))
  self.card['Line']=float('nan')
  self.assertIsNone(self.run_edge(self.rows(4.5)))
 def test_display_rejects_push_neighbor_from_warm_import(self):
  import ast
  from pathlib import Path
  from types import SimpleNamespace
  from unittest.mock import patch
  tree=ast.parse(Path('app.py').read_text())
  env={'st':SimpleNamespace(session_state={}), 'normalize_player':lambda v:v.lower(), 'ui_name':lambda v:v,
       'engine_no_vig':self.compare,'market_columns':lambda c:[c['Stat Key']],'fantasy_weights':lambda c:None}
  for name in ('pick_offered_sides','line_edge_info','consensus_matches_filter'):
   node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
   exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),env)
  card=dict(self.card,Line=4.,**{'Line Type':'Standard'})
  for book_line in (3.5,4.5):
   with self.subTest(book_line=book_line), patch('line_edges.detect',return_value={'book_line':book_line,'probability':.55}):
    edge=env['line_edge_info'](card,{},self.rows(book_line))
    self.assertIsNone(edge)
    self.assertFalse(env['consensus_matches_filter']({'line_edge':edge},'🔥 Sharp +EV (54%+)'))
if __name__=='__main__':unittest.main()
