import unittest
import test_combo_regression as fixtures

env=fixtures.env

class ComboExposureTests(unittest.TestCase):
 def pool(self,count=18):
  pool,_=env['combo_pool'](fixtures.populated(count),fixtures.REF)
  for leg in pool:leg['score']=1.0
  return pool
 def test_equal_ranked_options_use_new_players_when_available(self):
  options=env['combo_candidates'](self.pool(),6,'balanced-v1')
  self.assertEqual(len(options),3)
  players=[{leg['player_id'] for leg in legs} for legs in options]
  self.assertEqual(len(set.union(*players)),18)
 def test_other_sizes_exposure_changes_first_option_within_score_floor(self):
  pool=self.pool(12)
  original=env['combo_candidates'](pool,2,'balanced-v1')[0]
  used={leg['player_id']:1 for leg in original}
  changed=env['combo_candidates'](pool,2,'balanced-v1',used)[0]
  self.assertFalse(set(used)&{leg['player_id'] for leg in changed})
  self.assertGreaterEqual(env['combo_rank'](changed,'balanced-v1'),env['combo_rank'](original,'balanced-v1')-.10)
 def test_diversity_cannot_force_a_far_weaker_pair(self):
  pool=self.pool(4)
  for leg in pool[2:]:leg['score']=.3
  baseline=env['combo_candidates'](pool,2,'balanced-v1')[0]
  changed=env['combo_candidates'](pool,2,'balanced-v1',{leg['player_id']:20 for leg in baseline})[0]
  self.assertGreaterEqual(env['combo_rank'](changed,'balanced-v1'),env['combo_rank'](baseline,'balanced-v1')-.10)
 def test_other_size_and_played_entries_count_once_and_old_days_do_not(self):
  store=fixtures.populated(12);pool=self.pool(12)
  combo=env['combo_snapshot'](pool[:2],'balanced-v1',fixtures.REF)
  store.put('combo',combo['id'],combo)
  store.put('combo_suggestions','2026-11-09:standard:research',{'sizes':{'2':[combo['id']],'6':[]}})
  store.put('combo_played',combo['id'],{'combo_id':combo['id'],'active':True})
  counts=env['combo_player_exposure'](store,'standard','research',6,fixtures.REF)
  self.assertEqual(counts,{leg['player_id']:1 for leg in combo['legs']})
  self.assertEqual(env['combo_player_exposure'](store,'standard','research',6,'2026-11-10T18:00Z'),{})
 def test_small_pool_returns_available_options_without_inventing_players(self):
  pool=self.pool(2)
  options=env['combo_candidates'](pool,2,'balanced-v1',{'0':10,'1':10})
  self.assertEqual(len(options),1)
  self.assertEqual({l['player_id'] for l in options[0]},{'0','1'})
