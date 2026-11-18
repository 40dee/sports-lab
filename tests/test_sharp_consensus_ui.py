import ast,copy,math,unittest
from pathlib import Path
from unittest.mock import Mock
TREE=ast.parse((Path(__file__).resolve().parents[1]/'app.py').read_text())
class SharpConsensusTests(unittest.TestCase):
 def setUp(self):
  self.env={'line_edge_info':lambda *args:None,'math':math,'engine_no_vig':Mock(return_value=None),'identity':lambda c:c['id']}
  names=('ui_number','pick_offered_sides','consensus_edge_info','consensus_matches_filter','consensus_sort_records','ui_sharp_side_badge')
  for name in names:
   node=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name==name)
   exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),self.env)
  self.card={'id':'a','Platform':'prizepicks','Line Type':'Standard'}
 def info(self,more=.554,direction='LESS',card=None):
  return self.env['consensus_edge_info'](card or self.card,{'direction':direction,'market':{'more':more,'less':1-more,'book_count':3}})
 def test_opposite_offered_side_separate_from_model_and_immutable(self):
  result={'direction':'LESS','projection':9,'market':{'more':.554,'less':.446,'book_count':3}}
  original=copy.deepcopy(result);card=copy.deepcopy(self.card)
  info=self.env['consensus_edge_info'](card,result)
  self.assertEqual(info['model_probability'],.446);self.assertTrue(info['sharp_ev']);self.assertFalse(info['aligned'])
  self.assertEqual(info['best_side'],'MORE')
  badge=self.env['ui_sharp_side_badge'](info,'LESS')
  self.assertIn('↑ MORE 55.4% (3 books)',badge)
  self.assertEqual(result,original);self.assertEqual(card,self.card)
 def test_one_sided_demon_never_surfaces_less(self):
  card=dict(self.card,**{'Line Type':'Demon','Available Sides':['MORE']})
  info=self.info(.44,'LESS',card)
  self.assertEqual(info['best_side'],'MORE');self.assertFalse(info['sharp_ev']);self.assertFalse(info['aligned'])
  self.assertEqual(self.env['ui_sharp_side_badge'](info,'MORE'),'')
  # Explicit omission, unknown specials and malformed availability remain conservative.
  for sides in ([],None,['INVALID']):
   c=dict(card,**{'Available Sides':sides});self.assertFalse(self.info(.6,'MORE',c)['sharp_ev'])
  card.pop('Available Sides');self.assertIsNone(self.info(.6,'MORE',card)['best_side'])
 def test_explicit_side_omission_on_standard_is_honored(self):
  card=dict(self.card,**{'Available Sides':['LESS']})
  info=self.info(.6,'MORE',card)
  self.assertFalse(info['aligned']);self.assertFalse(info['sharp_ev']);self.assertEqual(info['best_side'],'LESS')
 def test_exact_thresholds_filters_and_non_prizepicks(self):
  aligned=self.info(.535,'MORE');ev=self.info(.54,'MORE')
  matches=self.env['consensus_matches_filter']
  self.assertTrue(matches(aligned,'✓ Sharp aligned (53.5%+)'))
  self.assertFalse(matches(aligned,'🔥 Sharp +EV (54%+)'))
  self.assertTrue(matches(ev,'🔥 Sharp +EV (54%+)'))
  self.assertFalse(self.info(.5349,'MORE')['aligned'])
  self.assertFalse(self.info(.5399,'MORE')['sharp_ev'])
  self.assertTrue(matches({},'All'))
  self.assertFalse(self.info(.6,'MORE',dict(self.card,Platform='kalshi'))['sharp_ev'])
 def test_matching_board_fallback_and_missing_evidence(self):
  helper=self.env['consensus_edge_info'];engine=self.env['engine_no_vig']
  self.assertIsNone(helper(self.card,{})['best_probability'])
  engine.return_value={'more':.55,'less':.45,'book_count':2}
  info=helper(self.card,{'direction':'MORE'})
  self.assertTrue(info['aligned']);self.assertEqual(info['source'],'matching saved board')
  engine.reset_mock();self.info();engine.assert_not_called()
 def test_invalid_probability_pairs_do_not_pass(self):
  for market in ({'more':float('nan'),'less':.4},{'more':1.2,'less':-.2},{'more':.6,'less':.6}):
   info=self.env['consensus_edge_info'](self.card,{'direction':'MORE','market':market})
   self.assertFalse(info['aligned']);self.assertFalse(info['sharp_ev'])
 def test_sort_uses_best_offered_probability_missing_last_and_stable_ties(self):
  records=[{'id':k} for k in ('a','b','c','d')];original=copy.deepcopy(records)
  infos={'a':{'best_probability':.55},'b':{'best_probability':.6},'d':{'best_probability':.55}}
  sort=self.env['consensus_sort_records']
  self.assertEqual([c['id'] for c in sort(records,infos,{},'Highest sharp %')],['b','a','d','c'])
  self.assertEqual([c['id'] for c in sort(records,infos,{'a':.7,'b':.3},'Model score')],['a','b','c','d'])
  self.assertEqual(records,original)
if __name__=='__main__':unittest.main()
