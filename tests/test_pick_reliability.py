import copy
import unittest
from pick_reliability import examples, features, fit, score, evaluate, learn, top_players


def record(day=1, line=10.5, actual=15, player='p1'):
    key=str((day,line,player))
    frozen={'id':key,'created_at':f'2026-11-{day:02d}T16:00:00Z','event_id':str(day),
        'card':{'Platform':'prizepicks','Player':player,'Start (UTC)':f'2026-11-{day:02d}T23:00:00Z',
            'Line':line,'Line Type':'Standard','Available Sides':['MORE','LESS']},
        'result':{'player_id':player,'stat_group':'PTS','projection':14,'baseline':13,
            'direction':'MORE','ranking_games':10,'minutes':28,'standardized_gap':.8,
            'components':[{'Pace factor':1.01,'Position factor':1.03}]}}
    grade={'status':'FINAL','outcome':'WIN' if actual>line else 'LOSS','actual':actual}
    return key,frozen,grade


class ReliabilityTests(unittest.TestCase):
    def test_alternate_thresholds_share_one_example_and_duplicates_do_not_inflate(self):
        records=[];grades={}
        for line in (9.5,10.5,11.5):
            key,f,g=record(line=line);records.append((key,f));grades[key]=g
        other=copy.deepcopy(records[0][1]);other['created_at']='2026-11-01T17:00:00Z'
        records.append(('duplicate',other));grades['duplicate']=copy.deepcopy(grades[records[0][0]])
        rows,excluded=examples(records,grades)
        self.assertEqual(len(rows),3)
        self.assertAlmostEqual(sum(r['weight'] for r in rows),1)
        self.assertEqual(excluded['duplicate_threshold'],1)

    def test_post_tip_predictions_ties_nonfinal_and_inconsistent_grades_are_excluded(self):
        records=[];grades={}
        for i,kind in enumerate(('late','tie','pending','bad_grade','valid'),1):
            key,f,g=record(day=i)
            if kind=='late':f['created_at']=f['card']['Start (UTC)']
            if kind=='tie':g.update(actual=10.5,outcome='TIE')
            if kind=='pending':g['status']='PENDING'
            if kind=='bad_grade':g['outcome']='LOSS'
            records.append((key,f));grades[key]=g
        rows,_=examples(records,grades)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['day'],'2026-11-05')

    def test_features_do_not_read_actual_results_or_mutate_forecasts(self):
        _,f,g=record();original=copy.deepcopy(f)
        before=features(f['card'],f['result'])
        g['actual']=999
        self.assertEqual(before,features(f['card'],f['result']))
        self.assertEqual(f,original)
        self.assertEqual(before['history'],'10-14')
        self.assertEqual(before['minutes'],'25-31')
        self.assertEqual(before['rate'],'PTS:0.2-0.49')

    def test_fold_training_ends_before_test_day_and_future_results_cannot_change_earlier_folds(self):
        records=[];grades={}
        for day in range(1,6):
            key,f,g=record(day=day,actual=15 if day%2 else 5)
            records.append((key,f));grades[key]=g
        rows,_=examples(records,grades)
        folds=evaluate(rows)
        self.assertTrue(all(f['trained_through']<f['day'] for f in folds))
        altered=copy.deepcopy(rows);altered[-1]['correct']=1-altered[-1]['correct']
        self.assertEqual(evaluate(rows)[:-1],evaluate(altered)[:-1])

    def test_unseen_patterns_fall_back_and_models_stay_shadow(self):
        model=fit([])
        self.assertAlmostEqual(score(model,{}),.5)
        self.assertEqual(model['status'],'shadow')
        key,f,g=record()
        model,report=learn([(key,f)],{key:g})
        self.assertEqual(report['activation'],'shadow_only')
        self.assertEqual(report['graded_dates'],1)
        self.assertTrue(report['patterns'])

    def test_small_samples_are_shrunk_and_top_players_are_distinct(self):
        key,f,g=record()
        rows,_=examples([(key,f)],{key:g});model=fit(rows)
        estimate=score(model,rows[0]['features'])
        self.assertGreater(estimate,model['prior'])
        self.assertLess(estimate,1)
        duplicate=copy.deepcopy(rows[0]);duplicate['id']='another-threshold'
        self.assertEqual(len(top_players(rows+[duplicate],lambda r:r['gap'])),1)

    def test_unavailable_directions_and_specials_cannot_train_any_patterns(self):
        k1,f1,g1=record(player='offered')
        k2,f2,g2=record(player='pass',actual=5)
        f2['card'].update({'Line Type':'Demon','Available Sides':['MORE']})
        f2['result']['direction']='LESS'
        g2['outcome']='WIN'
        model,report=learn([(k1,f1),(k2,f2)],{k1:g1,k2:g2})
        self.assertEqual(report['examples'],1)
        self.assertEqual(report['ranking_examples'],1)
        self.assertAlmostEqual(model['effective_examples'],1)
        self.assertFalse(any(p['feature']=='tier' and p['pattern']=='demon' for p in report['patterns']))

