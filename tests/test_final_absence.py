import ast
import copy
import unittest
from pathlib import Path

source = Path(__file__).resolve().parents[1] / 'app.py'
tree = ast.parse(source.read_text())
env = {}
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef)
    and n.name == 'confirmed_recap_dnp'], type_ignores=[]), str(source), 'exec'), env)


class FinalAbsenceTests(unittest.TestCase):
    def summary(self, story):
        return {'header': {'id': '123'}, 'article': {'id': 'recap', 'gameId': '123',
            'type': 'Recap', 'story': story}, 'injuries': [{'injuries': [{
                'status': 'Day-To-Day', 'athlete': {'id': '7', 'displayName': 'Player One'}}]}]}

    def test_explicit_same_game_absence_variants(self):
        for story in ('They played Sunday without forward Player One (wrist).',
                      'They were also without Player One, who sat out because of illness.',
                      'Player One (knee) and Player Two (wrist) each sat for the second straight.',
                      'Player One also sat out to manage his knees.',
                      'The team rested Player One.', 'Player One did not play due to illness.'):
            with self.subTest(story=story):
                result = env['confirmed_recap_dnp'](self.summary(story), '7')
                self.assertEqual(result['status'], 'DNP')
                self.assertIsNone(result['actual'])
                self.assertTrue(result['evidence'])

    def test_no_guess_from_injury_or_absence_or_missed_shot(self):
        for story in ('Player One missed both free throws.', 'Player One scored 12 points.',
                      'Player One injured his knee during the game.',
                      'The team will be playing without Player One tomorrow.',
                      'Player Two did not play.', 'Up next: They played without Player One.'):
            with self.subTest(story=story):
                self.assertIsNone(env['confirmed_recap_dnp'](self.summary(story), '7'))

    def test_exact_identity_and_event_required(self):
        value = self.summary('Player One did not play.')
        self.assertIsNone(env['confirmed_recap_dnp'](value, '8'))
        value['article']['gameId'] = 'other'
        self.assertIsNone(env['confirmed_recap_dnp'](value, '7'))

    def test_linked_identity_without_injury_entry(self):
        value = self.summary('<a href="https://www.espn.com/nba/player/_/id/7/player-one">Player One</a> did not play.')
        value['injuries'] = []
        self.assertEqual(env['confirmed_recap_dnp'](value, '7')['status'], 'DNP')

# Extract the pure grading helpers without executing Streamlit's UI at import.
import pandas as pd
import math

helpers = [n for n in tree.body if isinstance(n, ast.FunctionDef)
           and n.name in ('completed_actual', 'permanent_final_absence')]
grade_env = {'pd': pd, 'math': math, 'encode': repr,
             'confirmed_recap_dnp': lambda summary, player_id: None}
exec(compile(ast.Module(body=helpers, type_ignores=[]), str(source), 'exec'), grade_env)


class ExactOutAndRetryTests(unittest.TestCase):
    def final_summary(self, completed=True, injury_id='7', status='Out', boxscore_id=None):
        players = []
        if boxscore_id is not None:
            players = [{'statistics': [{'labels': ['MIN', 'PTS'], 'athletes': [
                {'athlete': {'id': boxscore_id}, 'stats': ['20', '10']}]}]}]
        return {'header': {'competitions': [{'status': {'type': {'completed': completed}}}]},
                'boxscore': {'players': players},
                'injuries': [{'injuries': [{'athlete': {'id': injury_id}, 'status': status}]}]}

    def test_final_exact_out_absent_is_dnp(self):
        actual = grade_env['completed_actual'](self.final_summary(), '7', ['PTS'])
        self.assertEqual(actual, {'status': 'DNP', 'actual': None,
            'verification': 'Final game: player listed Out and absent from completed ESPN boxscore.'})

    def test_other_player_out_does_not_count(self):
        self.assertIsNone(grade_env['completed_actual'](
            self.final_summary(injury_id='8'), '7', ['PTS']))

    def test_not_final_or_not_out_does_not_count(self):
        self.assertIsNone(grade_env['completed_actual'](
            self.final_summary(completed=False), '7', ['PTS']))
        self.assertIsNone(grade_env['completed_actual'](
            self.final_summary(status='Questionable'), '7', ['PTS']))

    def test_boxscore_row_takes_priority(self):
        actual = grade_env['completed_actual'](
            self.final_summary(boxscore_id='7'), '7', ['PTS'])
        self.assertEqual(actual['status'], 'FINAL')
        self.assertEqual(actual['actual'], 10)

    def test_retry_cutoff_requires_age_attempts_and_exact_error(self):
        current = pd.Timestamp('2026-11-20T12:00:00Z')
        reason = 'Final game: exact player ID absent from boxscore; appearance or identity needs verification'
        check = grade_env['permanent_final_absence']
        self.assertTrue(check(reason, 6, '2026-11-17T10:00:00Z', current))
        self.assertFalse(check(reason, 5, '2026-11-17T10:00:00Z', current))
        self.assertFalse(check(reason, 6, '2026-11-19T10:00:00Z', current))
        self.assertFalse(check('Other error', 6, '2026-11-17T10:00:00Z', current))
