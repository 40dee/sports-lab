import unittest
import pandas as pd
from daily_learning import evaluate_daily

class DailyLearningTests(unittest.TestCase):
    def frame(self):
        return pd.DataFrame([
            dict(start='2026-11-10T20:00:00Z',created='2026-11-10T18:00:00Z',event='a',player='1',baseline=10,adjusted=20,line=15,actual=10),
            dict(start='2026-11-11T20:00:00Z',created='2026-11-11T18:00:00Z',event='b',player='2',baseline=10,adjusted=20,line=15,actual=20)])
    def test_daily_fit_runs_before_twelve_dates_and_holdout_is_never_used_to_choose_alpha(self):
        result=evaluate_daily(self.frame())
        self.assertEqual(result['status'],'evaluated')
        self.assertEqual(result['candidate_alpha'],0)
        self.assertEqual(result['holdout_date'],'2026-11-11')
        self.assertEqual(result['candidate_holdout']['accuracy'],0)
        self.assertEqual(result['incumbent_holdout']['accuracy'],1)
        self.assertEqual(result['activation'],'unchanged')
    def test_alternate_lines_do_not_add_independent_player_games(self):
        frame=self.frame();frame=pd.concat([frame,frame.iloc[[0]]],ignore_index=True)
        result=evaluate_daily(frame)
        self.assertEqual(result['train_examples'],2)
        self.assertEqual(result['train_player_games'],1)
    def test_late_forecasts_and_nonfinite_inputs_do_not_enter_training(self):
        frame=self.frame();frame.loc[0,'created']='2026-11-10T21:00:00Z'
        self.assertEqual(evaluate_daily(frame)['status'],'waiting_for_two_graded_dates')
        frame=self.frame();frame.loc[0,'actual']=float('nan')
        self.assertEqual(evaluate_daily(frame)['examples'],1)

    def test_mixed_iso_timestamp_precision_keeps_every_valid_pregame_example(self):
        frame=self.frame()
        extra=dict(frame.iloc[0]);extra.update(start='2026-11-10T20:00:00.000+00:00',created='2026-11-10T18:00:00.123456+00:00',player='3')
        frame=pd.concat([frame,pd.DataFrame([extra])],ignore_index=True)
        result=evaluate_daily(frame)
        self.assertEqual(result['examples'],3)
        self.assertEqual(result['train_examples'],2)
        self.assertEqual(result['train_player_games'],2)


    def test_pacific_evening_is_one_slate_across_utc_midnight(self):
        frame=self.frame()
        frame.loc[0,['start','created']]=['2026-11-19T23:00Z','2026-11-19T21:00Z']
        frame.loc[1,['start','created']]=['2026-11-20T03:00Z','2026-11-19T21:00Z']
        self.assertEqual(evaluate_daily(frame)['graded_dates'],1)
        frame.loc[1,['start','created']]=['2026-11-21T03:00Z','2026-11-20T21:00Z']
        self.assertEqual(evaluate_daily(frame)['holdout_date'],'2026-11-20')
    def test_pacific_day_respects_summer_dst(self):
        frame=self.frame()
        frame.loc[0,['start','created']]=['2026-07-20T23:00Z','2026-07-20T21:00Z']
        frame.loc[1,['start','created']]=['2026-07-21T06:59Z','2026-07-20T21:00Z']
        self.assertEqual(evaluate_daily(frame)['graded_dates'],1)

    def test_paused_alpha_tuning_collects_counts_without_fitting(self):
        report=evaluate_daily(self.frame(),incumbent_alpha=.75,allow_tuning=False)
        self.assertEqual(report['status'],'alpha_tuning_paused')
        self.assertEqual(report['examples'],2)
        self.assertEqual(report['incumbent_alpha'],.75)
        self.assertNotIn('candidate_alpha',report)
        self.assertEqual(report['activation'],'unchanged')
