import copy,unittest
import test_combo_regression as fixtures

env=fixtures.env


def leg(stat,direction='MORE',team='A',event='game',score=.8):
    return dict(stat_group=stat,direction=direction,team_id=team,event_id=event,score=score)


class PairCorrelationTests(unittest.TestCase):
    def test_requested_weights_and_symmetry(self):
        cases=[('PTS','AST',(.03,-.14),(-.04,-.22)),
               ('FG3M','REB',(-.06,.02),(-.12,-.04)),
               ('TOV','STL',(.04,-.12),(-.03,-.18)),
               ('TOV','STL+BLK',(.04,-.12),(-.03,-.18))]
        for a,b,balanced,diverse in cases:
            for policy,values in (('balanced-v1',balanced),('diverse-v1',diverse)):
                for direction,expected in zip(('MORE','LESS'),values):
                    first,second=leg(a),leg(b,direction,team='B')
                    with self.subTest(a=a,b=b,policy=policy,direction=direction):
                        self.assertAlmostEqual(env['combo_pair_correlation'](first,second,policy),expected)
                        self.assertAlmostEqual(env['combo_pair_correlation'](second,first,policy),expected)
    def test_all_requested_volume_groups_and_component_permutations(self):
        for stat in ('PTS','AST','FG3M','PTS+AST','REB+PTS','REB+AST','PTS+AST+REB','PrizePicks Fantasy'):
            self.assertAlmostEqual(env['combo_pair_correlation'](leg(stat),leg('PTS',team='B'),'balanced-v1'),.03)
    def test_same_direction_volume_outranks_opposite_direction_for_both_policies(self):
        for policy in ('balanced-v1','diverse-v1'):
            self.assertGreater(env['combo_rank']([leg('PTS'),leg('AST',team='B')],policy),
                               env['combo_rank']([leg('PTS'),leg('AST','LESS',team='B')],policy))
    def test_less_scoring_more_opponent_rebounds_outranks_both_more(self):
        for policy in ('balanced-v1','diverse-v1'):
            self.assertGreater(env['combo_rank']([leg('PTS','LESS'),leg('REB',team='B')],policy),
                               env['combo_rank']([leg('PTS'),leg('REB',team='B')],policy))
    def test_unknown_pairs_and_cross_game_guards(self):
        for policy,penalty in (('balanced-v1',-.08),('diverse-v1',-.16)):
            self.assertEqual(env['combo_pair_correlation'](leg('REB'),leg('REB',team='B'),policy),penalty)
            self.assertEqual(env['combo_pair_correlation'](leg('PTS'),leg('AST',event='other',team='B'),policy),0)
            self.assertEqual(env['combo_pair_correlation'](leg('PTS',event=None),leg('AST',event=None,team='B'),policy),0)
            self.assertEqual(env['combo_pair_correlation'](leg('PTS'),leg('AST'),policy),penalty)
            self.assertEqual(env['combo_pair_correlation'](leg('PTS'),leg('AST',team=None),policy),penalty)
    def test_original_score_formula_and_same_stat_penalty_remain(self):
        legs=[leg('PTS',score=2),leg('PTS',event='different',team='B',score=.6)]
        expected=.65*.6+.35*((1.5+.6)/2)-.02
        for policy in ('balanced-v1','diverse-v1'):
            self.assertAlmostEqual(env['combo_rank'](legs,policy),expected)
    def test_note_describes_relation_not_diverse_policy_penalty(self):
        combo={'rules_version':'distinct-teams-exposure-v5','legs':[leg('PTS'),leg('AST',team='B')]}
        self.assertIn('positively aligned',env['combo_same_game_note'](combo))
        combo['legs'][1]['direction']='LESS'
        self.assertIn('opposing',env['combo_same_game_note'](combo))
        combo['rules_version']='distinct-teams-exposure-v4'
        self.assertIn('Historical',env['combo_same_game_note'](combo))
    def test_new_version_does_not_rewrite_old_saved_records(self):
        store=fixtures.populated()
        old={'rules_version':'distinct-teams-exposure-v4','ranking_score':.5}
        store.put('combo','old',old);store.put('combo_cohort','old',{'policies':{'balanced-v1':['old']}})
        before=copy.deepcopy(store.records)
        pool,_=env['combo_pool'](store,fixtures.REF)
        snapshot=env['combo_snapshot'](pool[:2],'balanced-v1',fixtures.REF)
        self.assertEqual(snapshot['rules_version'],'distinct-teams-exposure-v5')
        self.assertEqual(env['combo_inputs_version'](store,fixtures.REF)[0],'distinct-teams-exposure-v5')
        self.assertEqual(store.records,before)
