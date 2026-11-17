import ast
import unittest
import test_combo_regression as fixtures
node=next(n for n in fixtures.tree.body if isinstance(n,ast.FunctionDef) and n.name=='combo_hit_rate_summary')
env={}
exec(compile(ast.Module(body=[node],type_ignores=[]),str(fixtures.SOURCE),'exec'),env)

class ComboHitRateTests(unittest.TestCase):
    def test_full_hits_misses_pending_voids_and_duplicates(self):
        rows=[{'Combo ID':str(i),'Day':'2026-11-16','Size':2,'Result':state}
              for i,state in enumerate(('ALL_HIT','MISSED','PENDING','VOID_REVIEW'))]
        rows.append(dict(rows[0]))
        result=env['combo_hit_rate_summary'](rows)
        self.assertEqual([r['Players'] for r in result],[2,3,4,5,6])
        self.assertEqual(result[0]['Hit rate'],'50.0%')
        self.assertEqual(result[0]['Settled combos'],2)
        self.assertEqual(result[0]['Pending'],1)
        self.assertEqual(result[0]['Tie / DNP review'],1)
        self.assertEqual(result[-1]['Hit rate'],'—')
    def test_leg_wins_do_not_count_as_combo_wins(self):
        rows=[{'Combo ID':'a','Day':'2026-11-15','Size':6,'Result':'MISSED','Wins':5},
              {'Combo ID':'b','Day':'2026-11-16','Size':6,'Result':'ALL_HIT','Wins':6}]
        result=env['combo_hit_rate_summary'](rows)[-1]
        self.assertEqual(result['Hit rate'],'50.0%')
        self.assertEqual(result['Days'],2)

class TierRateTests(unittest.TestCase):
    def setUp(self):
        self.env={}
        for name in ('combo_line_type','combo_tier_result_rows'):
            node=next(n for n in fixtures.tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
            exec(compile(ast.Module(body=[node],type_ignores=[]),str(fixtures.SOURCE),'exec'),self.env)
    def combo(self,tiers,created='2026-11-16T18:00Z',version='original-gap-v1'):
        return {'day':'2026-11-16','size':len(tiers),'policy':'balanced-v1','created_at':created,
            'ranking_model':{'version':version},'legs':[{'card':{'Line Type':t}} for t in tiers]}
    def test_classifies_actual_legs_not_builder_mode(self):
        classify=self.env['combo_line_type']
        self.assertEqual(classify(self.combo(['Goblin','Goblin'])),'Goblin')
        self.assertEqual(classify(self.combo(['Standard','Demon'])),'Mixed')
        self.assertEqual(classify(self.combo(['regular','Standard'])),'Standard')
        self.assertEqual(classify(self.combo(['Standard','unknown'])),'Unverified')
    def test_excludes_later_rebuilds_other_tiers_and_other_ranking_versions(self):
        combos={str(i):self.combo(['Goblin','Goblin'],f'2026-11-16T18:0{i}Z') for i in range(4)}
        combos['standard']=self.combo(['Standard','Standard'])
        combos['learned']=self.combo(['Goblin','Goblin'],version='reliability-blend-v1')
        grades={k:{'state':'MISSED' if k in ('0','1','2') else 'ALL_HIT'} for k in combos}
        rows=self.env['combo_tier_result_rows'](combos,grades,'Goblin','balanced-v1','original-gap-v1')
        self.assertEqual([r['Combo ID'] for r in rows],['0','1','2'])
        self.assertTrue(all(r['Result']=='MISSED' for r in rows))
