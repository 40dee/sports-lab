import math,random,unittest
from minutes_error import split
class MinutesErrorTests(unittest.TestCase):
 def case(self,p,a,mp,ma,line=25,direction='MORE'):
  win=(a>line)==(direction=='MORE')
  return split({'Line':line},{'projection':p,'minutes':mp,'direction':direction},{'actual':a,'minutes':ma,'status':'FINAL','outcome':'WIN' if win else 'LOSS'})
 def test_minutes_only_miss_and_counterfactual_rescue(self):
  r=self.case(30,20,30,20)
  self.assertTrue(r['minutes_off_5']);self.assertEqual(r['dominant'],'minutes');self.assertTrue(r['minutes_correction_rescues']);self.assertFalse(r['rate_correction_rescues'])
  self.assertEqual(r['rate_contribution'],0)
 def test_rate_only_miss(self):
  r=self.case(30,15,30,30)
  self.assertFalse(r['minutes_off_5']);self.assertEqual(r['dominant'],'rate');self.assertTrue(r['rate_correction_rescues']);self.assertFalse(r['minutes_correction_rescues'])
 def test_five_minute_error_alone_is_not_causation(self):
  r=self.case(40,10,40,30)
  self.assertTrue(r['minutes_off_5']);self.assertFalse(r['minutes_correction_rescues']);self.assertEqual(r['dominant'],'rate')
 def test_less_side_and_exact_five_minute_boundary(self):
  self.assertTrue(self.case(20,30,20,30,direction='LESS')['minutes_correction_rescues'])
  self.assertTrue(self.case(30,20,30,25)['minutes_off_5'])
  self.assertFalse(self.case(30,20,30,25.001)['minutes_off_5'])
 def test_product_decomposition_conserves_projection_error(self):
  rng=random.Random(5)
  for _ in range(100):
   p,a,mp,ma=[rng.uniform(1,50) for _ in range(4)]
   r=self.case(p,a,mp,ma,direction='MORE' if p>25 else 'LESS')
   self.assertAlmostEqual(r['minutes_contribution']+r['rate_contribution'],p-a)
 def test_missing_or_zero_actual_minutes_are_not_imputed(self):
  for grade in ({'actual':20},{'actual':20,'minutes':None},{'actual':20,'minutes':0}):
   self.assertNotEqual(split({'Line':25},{'projection':30,'minutes':30,'direction':'MORE'},grade)['status'],'ok')
