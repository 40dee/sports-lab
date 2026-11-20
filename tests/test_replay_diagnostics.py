import unittest
import pandas as pd
from projection_v2 import calibrated_hit_probability
from replay_diagnostics import uncertainty_report,pra_ablation_report


class DiagnosticsTests(unittest.TestCase):
    def test_full_diagnostic_is_readonly_and_serializable(self):
        import copy,json
        from test_projection_v2 import ReplayTests
        from projection_v2 import run_backtest
        store,env=ReplayTests().setup_data()
        original=copy.deepcopy(store.records)
        report=run_backtest(store,env,max_samples=None,diagnostics=True)
        self.assertEqual(report['picks'],2)
        self.assertEqual(report['uncertainty_diagnostic']['corrected_brier'],.25)
        self.assertEqual(report['sportsbook_inventory']['extra_requests'],0)
        self.assertEqual(store.records,original)
        json.dumps(report,allow_nan=False)
    def frame(self):
        rows=[]
        for day in (1,2,3):
            for i in range(80):
                rows.append(dict(day=f'2026-11-{day:02}',created=f'2026-11-{day:02}T20:00Z',
                    graded_at=f'2026-11-{day+1:02}T06:00Z',stat='AST+PTS+REB',actual=25 if i%2 else 15,
                    v1_proj=24,v2_proj=23,v2_prob=.8,v2_win=i%2,v2_dir='MORE',line=20.5,
                    no_vegas_proj=22.,no_blowout_proj=24.,neither_proj=25.,columns=['PTS','REB','AST'],
                    is_fantasy=False,history_values=[12,20,27,22,18],game_variance=30))
        return pd.DataFrame(rows)
    def test_future_grades_never_train_earlier_predictions(self):
        frame=self.frame();before=frame.copy(deep=True)
        report=uncertainty_report(frame,calibrated_hit_probability)
        self.assertEqual(report['folds'][0]['training_picks'],0)
        self.assertEqual(report['folds'][1]['training_picks'],80)
        for fold in report['folds']:
            if fold['latest_training_grade']:
                self.assertLessEqual(pd.Timestamp(fold['latest_training_grade']),pd.Timestamp(fold['forecast_cutoff']))
        changed=frame.copy();changed.loc[changed.day.eq('2026-11-03'),'v2_win']=1
        later=uncertainty_report(changed,calibrated_hit_probability)
        self.assertEqual(report['folds'][:2],later['folds'][:2])
        pd.testing.assert_frame_equal(frame,before)
    def test_extra_variance_reduces_normal_distribution_confidence(self):
        raw,_=calibrated_hit_probability([12,20,27,22,18],28,20.5,'MORE',['PTS'])
        uncertain,_=calibrated_hit_probability([12,20,27,22,18],28,20.5,'MORE',['PTS'],model_error_variance=100)
        self.assertLess(abs(uncertain-.5),abs(raw-.5))
    def test_ablation_uses_identical_pra_cohort_and_reports_ties(self):
        frame=self.frame();frame.loc[0,'no_vegas_proj']=20.5
        rows=pra_ablation_report(frame)
        self.assertTrue(all(r['picks']==240 for r in rows))
        self.assertEqual(next(r for r in rows if r['variant']=='No Vegas')['ties'],1)

    def test_sigmoid_training_uses_variance_adjusted_probabilities(self):
        from unittest.mock import patch
        from replay_diagnostics import fit_sigmoid
        frame=self.frame();frame['actual']=1000
        with patch('replay_diagnostics.fit_sigmoid',wraps=fit_sigmoid) as fit:
            uncertainty_report(frame,calibrated_hit_probability)
        self.assertTrue(fit.called)
        probabilities=fit.call_args.args[0]
        self.assertTrue(any(abs(p-.8)>1e-3 for p in probabilities))
    def test_no_vegas_ablation_restores_full_saved_pace(self):
        from unittest.mock import patch
        from test_projection_v2 import ReplayTests
        from projection_v2 import run_backtest
        store,env=ReplayTests().setup_data()
        for key,frozen in store.items('forecast'):
            frozen['result']['components'][0]['Pace factor']=1.1
            store.put('forecast',key,frozen)
        with patch('projection_v2.vegas_environment_factor',return_value=(1.05,True)), patch('replay_diagnostics.pra_ablation_report',return_value=[]) as report:
            run_backtest(store,env,max_samples=None,diagnostics=True)
        frame=report.call_args.args[0]
        for _,row in frame.iterrows():
            self.assertAlmostEqual(row.no_vegas_proj/row.v2_proj,1.1/(1.04*1.05))
