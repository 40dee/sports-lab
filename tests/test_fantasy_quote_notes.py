import ast
import unittest
import pandas as pd
import test_combo_regression as fixtures
env=dict(fixtures.env)
env['SLATE_TIMEZONE']='America/Los_Angeles'
node=next(n for n in fixtures.tree.body if isinstance(n,ast.FunctionDef) and n.name=='fantasy_quote_review_note')
exec(compile(ast.Module(body=[node],type_ignores=[]),str(fixtures.SOURCE),'exec'),env)

class FantasyQuoteNoteTests(unittest.TestCase):
    def test_today_aged_quote_is_a_capture_label_without_inventing_less(self):
        c={'Start (UTC)':'2026-11-18T00:40Z','Tier Source':'prizepicksapi.com',
            'Tier Fetched At':'2026-11-17T23:27Z','Tier Observed At':'2026-11-17T23:18Z',
            'Available Sides':['MORE'],'Line Type':'Standard','Platform':'prizepicks'}
        note=env['fantasy_quote_review_note']({'upcoming':True},c,{},'2026-11-18T00:01Z')
        self.assertTrue(note.startswith('Daily saved board'))
        self.assertEqual(c['Available Sides'],['MORE'])
        self.assertFalse(env['pick_is_selectable'](c,{'direction':'LESS','projection':18.44}))
    def test_missing_timestamp_remains_explicit_and_started_stays_historical(self):
        c={'Start (UTC)':'2026-11-18T00:40Z'}
        self.assertIn('missing',env['fantasy_quote_review_note']({'upcoming':True},c,{},'2026-11-18T00:01Z'))
        self.assertIn('started',env['fantasy_quote_review_note']({'upcoming':False},c,{},'2026-11-18T01:00Z'))
