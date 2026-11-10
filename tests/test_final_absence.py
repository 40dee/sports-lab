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
