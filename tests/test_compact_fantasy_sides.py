import ast,copy,unittest
from pathlib import Path
from pp_side_fields import projection_sides
import test_direction_and_source as source
import test_full_board as board
class CompactFantasySideTests(unittest.TestCase):
 def row(self,**fields):
  return dict(id='real',player='Player A',stat='Fantasy Score',period='Full game',league='NBA',status='active',tier='standard',line=30.5,starts_at='2100-11-10T02:00:00Z',**fields)
 def test_explicit_compact_more_and_less_and_flat_boolean_tiers(self):
  for side in ('more','less'):
   row=self.row(sides=[side]);original=copy.deepcopy(row)
   normalized=source.env['normalize_pp_projection'](row,True)
   self.assertEqual(normalized['selections'],[{'side':side,'tier':'standard','available':True}])
   self.assertEqual(row,original)
  row=source.env['normalize_pp_projection'](self.row(more_available=True,less_available=True),True)
  self.assertEqual({v['side'] for v in row['selections']},{'more','less'})
 def test_standard_missing_or_empty_compact_sides_use_regular_line_rule(self):
  self.assertEqual(len(projection_sides(self.row())[0]),2)
  self.assertEqual(len(projection_sides(self.row(sides=[],selections=[]))[0]),2)
  self.assertEqual(projection_sides(self.row(more_available=False,less_available=False))[1],True)
 def test_merge_unknown_is_verify_and_offered_sides_select_correctly(self):
  tree=ast.parse(Path(board.SOURCE).read_text());env=dict(board.env)
  for name in ('pick_offered_sides','pick_is_selectable','pick_display_label'):
   node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
   exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),env)
  template={'Player':'Player A','Player ID':'1','Fixture ID':'g','Game':'Boston Celtics vs New York Knicks','Start (UTC)':'2100-11-10T02:00:00+00:00','Platform':'prizepicks','Stat':'Fantasy Score','Stat Key':'fantasy_score','Line':30.5,'Decoded':True,'Period':'result','Line Type':'Standard'}
  for sides,expected in (([],('LESS','MORE')),(['more'],('MORE',)),(['more','less'],('LESS','MORE'))):
   item=source.env['normalize_pp_projection'](self.row(sides=sides),True)
   merged,_=env['pp_merge_tiers']([template],[item],board.REF,fantasy_only=True)
   card=merged[-1];self.assertEqual(env['pick_offered_sides'](card),expected)
   for side in ('MORE','LESS'):
    result={'direction':side,'projection':32}
    self.assertEqual(env['pick_is_selectable'](card,result),expected is not None and side in expected)
    if expected is None:self.assertEqual(env['pick_display_label'](card,result),'VERIFY SIDES')
 def test_special_tiers_and_explicit_restrictions_never_get_standard_fallback(self):
  for tier in ('goblin','demon'):
   row=self.row(sides=[],selections=[]);row['tier']=tier
   self.assertEqual(projection_sides(row),([],False))
  for field in ('more_available','less_available'):
   row=self.row(sides=[],**{field:False})
   sides,known=projection_sides(row)
   self.assertTrue(known);self.assertFalse(any(s.get('available') for s in sides))
if __name__=='__main__':unittest.main()
