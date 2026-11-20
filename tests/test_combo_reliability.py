import copy
import unittest
import combo_reliability as ranking
import test_combo_regression as fixtures

class BlendTests(unittest.TestCase):
    def model(self):
        return {'version':'green-red-reliability-v1','trained_through':'2026-11-08',
            'effective_examples':100,'prior':.5,'cells':{'minutes':{'32+':{'weight':100,'wins':100}}}}
    def test_prior_day_only_and_original_fallback(self):
        model=self.model()
        self.assertIsNotNone(ranking.context(model,'2026-11-09'))
        self.assertIsNone(ranking.context(model,'2026-11-08'))
        self.assertIsNone(ranking.context(model,'2026-11-09','original'))
        self.assertIsNone(ranking.context({},'2026-11-09'))
    def test_adjustment_is_bounded_and_never_changes_more_less_or_projection(self):
        model=self.model();card={'Line Type':'Standard'}
        result={'minutes':35,'direction':'LESS','projection':20,'ranking_games':12}
        before=copy.deepcopy(result)
        score,delta=ranking.adjust(.7,card,result,model,ranking.context(model,'2026-11-09'))
        self.assertGreater(score,.7)
        self.assertLessEqual(abs(delta),.05)
        self.assertEqual(result,before)
        self.assertEqual(ranking.adjust(.7,{'Line Type':'Demon'},result,model,ranking.context(model,'2026-11-09')),(.7,0.))
    def test_original_and_learned_snapshots_have_distinct_ids_and_mode_invalidates_cache(self):
        store=fixtures.populated(6)
        model=self.model();store.put('pick_reliability_model','latest',model)
        store.put('combo_ranking','mode','learned_blend')
        pool,_=fixtures.env['combo_pool'](store,fixtures.REF)
        learned=fixtures.env['combo_snapshot'](pool[:2],'balanced-v1',fixtures.REF)
        v1=fixtures.env['combo_inputs_version'](store,fixtures.REF)
        store.put('combo_ranking','mode','original')
        pool,_=fixtures.env['combo_pool'](store,fixtures.REF)
        original=fixtures.env['combo_snapshot'](pool[:2],'balanced-v1',fixtures.REF)
        self.assertNotEqual(learned['id'],original['id'])
        self.assertNotEqual(v1,fixtures.env['combo_inputs_version'](store,fixtures.REF))
        self.assertEqual(original['ranking_model']['version'],'eligibility-diversity-v1')

