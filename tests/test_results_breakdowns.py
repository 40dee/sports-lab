import copy
import unittest

from result_scopes import results_breakdowns, standard_playable_rows


def row(identifier='a', **changes):
    value = {'ID': identifier, 'Event': 'game', 'Player': 'Player One',
             'Stat columns': ['PTS'], 'Period': 'fullgame', 'Platform': 'prizepicks',
             'Line type': 'Standard', 'Selectable': True, 'Grade status': 'FINAL',
             'Created (UTC)': '2026-11-17T20:00:00Z', 'Start (UTC)': '2026-11-17T23:00:00Z',
             'Outcome': 'WIN', 'Pick': 'MORE', 'Estimated hit probability': .6}
    value.update(changes)
    return value


class BreakdownTests(unittest.TestCase):
    def test_direction_counts_and_probabilities_use_original_frozen_values(self):
        rows = [row(), row('b', **{'Pick': 'LESS', 'Outcome': 'LOSS', 'Estimated hit probability': .55}),
                row('c', **{'Estimated hit probability': None})]
        before = copy.deepcopy(rows)
        result = results_breakdowns(rows)
        more, less = result['direction']
        self.assertEqual((more['Settled picks'], more['Wins'], more['Hit rate']), (2, 2, 1))
        self.assertEqual((less['Settled picks'], less['Losses'], less['Hit rate']), (1, 1, 0))
        self.assertEqual(more['Estimates available'], 1)
        self.assertEqual(more['Average estimate'], .6)
        self.assertEqual(result['confidence'][-1]['Saved confidence'], 'Unavailable')
        self.assertIsNone(result['confidence'][-1]['Average estimate'])
        self.assertEqual(rows, before)

    def test_only_final_pregame_decisions_count(self):
        for change in ({'Outcome': 'TIE'}, {'Outcome': 'DNP'}, {'Outcome': 'PENDING'},
                       {'Grade status': 'PENDING'}, {'Pick': 'PASS'},
                       {'Created (UTC)': '2026-11-17T23:00:00Z'},
                       {'Created (UTC)': None}, {'Start (UTC)': 'invalid'}):
            with self.subTest(change=change):
                self.assertEqual(results_breakdowns([row(**change)]), {'direction': [], 'confidence': []})

    def test_probability_boundaries_and_invalid_estimates(self):
        values = [0, .5, .55, .6, .65, .7, 1, None, float('nan'), float('inf'), -1, 60, True, 'bad']
        rows = [row(str(i), **{'Estimated hit probability': p}) for i, p in enumerate(values)]
        buckets = results_breakdowns(rows)['confidence']
        self.assertEqual([b['Settled picks'] for b in buckets], [1, 1, 1, 1, 1, 2, 7])
        self.assertEqual(sum(b['Settled picks'] for b in buckets), len(rows))

    def test_scope_uses_timestamp_order_before_result_filter(self):
        first = row('first', **{'Created (UTC)': '2026-11-17T13:00:00-07:00', 'Outcome': 'LOSS'})
        later = row('later', **{'Created (UTC)': '2026-11-17T12:30:00-08:00'})
        selected = standard_playable_rows([later, first])
        self.assertEqual(selected, [first])
        self.assertEqual(results_breakdowns(selected)['direction'][0]['Wins'], 0)


if __name__ == '__main__':
    unittest.main()


class ResultsDisplayTests(unittest.TestCase):
    def test_scope_and_breakdowns_render_with_saved_probabilities(self):
        from pathlib import Path
        from streamlit.testing.v1 import AppTest
        import test_combo_regression as fixtures

        key, frozen = fixtures.populated(1).items('forecast')[0]
        store = fixtures.Memory()
        frozen['card']['Period'] = 'result'
        key = fixtures.env['identity'](frozen['card'])
        frozen['id'] = key
        # Legacy forecasts have no selection_allowed key; verified Standard sides still count.
        frozen['result'].update(estimated_hit_probability=.6, market={'book':'pinnacle','more':.44,'less':.56,'match_method':'teams-time-player-stat-period-line'})
        store.put('forecast', key, frozen)
        store.put('grade', key, {'status': 'FINAL', 'outcome': 'LOSS', 'actual': 18})
        later = copy.deepcopy(frozen)
        later['card']['Line'] = 17.5
        later['created_at'] = '2026-11-09T17:00:00Z'
        later['result']['estimated_hit_probability'] = .55
        later_key = fixtures.env['identity'](later['card'])
        later['id'] = later_key
        store.put('forecast', later_key, later)
        store.put('grade', later_key, {'status': 'FINAL', 'outcome': 'WIN', 'actual': 18})

        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'), default_timeout=60).run()
        app.session_state['_sports_lab_offline_records_v1'] = store.records
        app.session_state['sports_lab_page_buttons'] = 'Results'
        app.run()
        self.assertFalse(app.exception, list(app.exception))
        grid=next(m.value for m in app.markdown if '<div class="sl-results-kpis">' in m.value)
        self.assertIn('<span>Settled picks</span><strong>1</strong>',grid)
        self.assertEqual(grid.count('class="sl-results-kpi"'),5)
        direction = next(m.value for m in app.markdown if '<span>MORE</span>' in m.value and '<span>LESS</span>' in m.value)
        self.assertIn('0W–1L · 1 settled', direction)
        self.assertIn('<span>Brier score</span>', grid)
        self.assertIn('0.360', grid)
        app.button_group(key='results_view_tabs').set_value('Sharp +EV').run()
        self.assertFalse(app.exception, list(app.exception))
        sharp = next(m.value for m in app.markdown if '🔥 Sharp +EV (54%+' in m.value and 'sl-results-kpi' in m.value)
        self.assertIn('100.0%', sharp)
        self.assertIn('1W–0L · 1 settled', sharp)
        app.button_group(key='results_view_tabs').set_value('Overview').run()
        app.selectbox(key='results_scope').select('All research (raw)').run()
        self.assertFalse(app.exception, list(app.exception))
        grid=next(m.value for m in app.markdown if '<div class="sl-results-kpis">' in m.value)
        self.assertIn('<span>Settled picks</span><strong>2</strong>',grid)
        app.selectbox(key='results_outcome').select('WIN').run()
        direction = next(m.value for m in app.markdown if '<span>MORE</span>' in m.value and '<span>LESS</span>' in m.value)
        self.assertIn('1W–0L · 1 settled', direction)


class SharpMarketTests(unittest.TestCase):
    def record(self, **changes):
        return row(**dict({'Actual': 8, 'Line': 10.5, 'Pick': 'MORE', 'Outcome': 'LOSS',
            'Market': {'match_method': 'teams-time-player-stat-period-line',
                       'book': 'pinnacle', 'more': .44, 'less': .56}}, **changes))

    def breakdown(self, rows):
        from result_scopes import sharp_market_breakdowns
        return sharp_market_breakdowns(rows)

    def test_sharp_side_disagrees_with_model_and_grades_its_own_win(self):
        rows = [self.record()]
        before = copy.deepcopy(rows)
        result = self.breakdown(rows)
        self.assertEqual([r['Category'].split()[0] for r in result], ['🔥', '🎯', '⚠️'])
        self.assertEqual([r['Wins'] for r in result], [1, 1, 0])
        self.assertEqual([r['Losses'] for r in result], [0, 0, 1])
        self.assertEqual(rows, before)

    def test_unoffered_and_empty_sides_never_enter_sharp_cohorts(self):
        for sides in (['MORE'], []):
            result = self.breakdown([self.record(**{'Line type': 'Demon', 'Available sides': sides})])
            self.assertEqual([r['Category'].split()[0] for r in result], ['⚠️'])
        self.assertEqual(self.breakdown([self.record(**{'Line type':'Demon', 'Pick':'LESS', 'Outcome':'WIN'})]), [])

    def test_only_final_nonpush_pregame_rows_with_finite_values_count(self):
        for change in ({'Grade status':'PENDING'}, {'Outcome':'TIE'}, {'Outcome':'DNP'},
                       {'Created (UTC)':'2026-11-18T00:00Z'},
                       {'Created (UTC)':'2026-11-17T23:00Z'}, {'Start (UTC)':None},
                       {'Actual':10.5}, {'Actual':float('nan')}, {'Line':float('inf')}, {'Actual':True}):
            with self.subTest(change=change):
                self.assertEqual(self.breakdown([self.record(**change)]), [])

    def test_weighted_books_override_legacy_aggregate_and_pinnacle_marker(self):
        market = {'match_method':'teams-time-player-stat-period-line', 'more':.5, 'less':.5,
                  'books':[{'book':'pinnacle','more':.6,'less':.4},
                           {'book':'bovada','more':.4,'less':.6}]}
        rows = [self.record(Market=market, Actual=12, Outcome='WIN')]
        before = copy.deepcopy(rows)
        result = self.breakdown(rows)
        self.assertEqual([r['Category'].split()[0] for r in result], ['🔥','✓','🎯'])
        self.assertTrue(all(r['Settled']==1 and r['Hit rate']==1 for r in result))
        self.assertEqual(rows, before)

    def test_exact_match_validation_and_invalid_books_fail_closed(self):
        for market in (None, {}, {'more':.6,'less':.6},
                       {'match_method':'other','more':.6,'less':.4},
                       {'match_method':'teams-time-player-stat-period-line','more':.6,'less':.4,'books':[]},
                       {'match_method':'teams-time-player-stat-period-line','more':.6,'less':.4,'books':[{}]}):
            self.assertEqual(self.breakdown([self.record(Market=market)]), [])

    def test_thresholds_and_standard_fallback(self):
        for p, symbols in ((.535,['✓','🎯']),(.54,['🔥','✓','🎯']),(.49,[]),(.489,['⚠️'])):
            market={'book':'pinnacle','more':p,'less':1-p,'match_method':'teams-time-player-stat-period-line'}
            # Only MORE is offered so a high LESS probability cannot enter sharp cohorts.
            result=self.breakdown([self.record(Market=market, **{'Available sides':['MORE']})])
            self.assertEqual([r['Category'].split()[0] for r in result],symbols)
        for platform, tier in (('prizepicks','Regular'),('draftkings','Standard')):
            result=self.breakdown([self.record(Platform=platform, **{'Line type':tier})])
            self.assertEqual(any(r['Category'].startswith('🔥') for r in result),platform=='prizepicks')
