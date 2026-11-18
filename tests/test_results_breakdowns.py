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
        frozen['result'].update(selection_allowed=True, estimated_hit_probability=.6)
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
        metrics = {m.label: m.value for m in app.metric}
        self.assertEqual(metrics['Settled picks'], '1')
        direction = next(d.value for d in app.dataframe if 'Direction' in d.value.columns and 'Settled picks' in d.value.columns)
        self.assertEqual(direction.iloc[0]['Losses'], 1)
        self.assertEqual(direction.iloc[0]['Average estimate'], '60.0%')
        app.selectbox(key='results_scope').select('All research (raw)').run()
        self.assertFalse(app.exception, list(app.exception))
        metrics = {m.label: m.value for m in app.metric}
        self.assertEqual(metrics['Settled picks'], '2')
        app.selectbox(key='results_outcome').select('WIN').run()
        direction = next(d.value for d in app.dataframe if 'Direction' in d.value.columns and 'Settled picks' in d.value.columns)
        self.assertEqual(direction.iloc[0]['Settled picks'], 1)
        self.assertEqual(direction.iloc[0]['Wins'], 1)
