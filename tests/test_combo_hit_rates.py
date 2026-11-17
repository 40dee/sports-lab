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
