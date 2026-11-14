import ast
from pathlib import Path
import unittest
import pandas as pd

tree=ast.parse(Path(__file__).resolve().parents[1].joinpath('app.py').read_text())
env={'pd':pd,'utc_timestamp':lambda value:pd.to_datetime(value,utc=True,errors='coerce')}
node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='newer_quote_rows')
exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),env)

class QuoteReplayTests(unittest.TestCase):
    def test_morning_snapshot_cannot_replace_afternoon_quotes_or_changed_lines(self):
        previous=[{'family':'player:fantasy:standard','Line':25.5,'Odds checked (UTC)':'2026-11-13T23:44:00Z'}]
        incoming=[{'family':'player:fantasy:standard','Line':23.5,'Odds checked (UTC)':'2026-11-13T18:00:00Z'}]
        self.assertEqual(env['newer_quote_rows'](previous,incoming,lambda c:c['family']),[])
        incoming[0]['Odds checked (UTC)']='2026-11-13T23:50:00Z'
        self.assertEqual(env['newer_quote_rows'](previous,incoming,lambda c:c['family']),incoming)

    def test_unknown_incoming_time_cannot_overwrite_known_quote_but_new_families_survive(self):
        previous=[{'family':'a','Odds checked (UTC)':'2026-11-13T23:44:00Z'}]
        incoming=[{'family':'a'},{'family':'b'}]
        self.assertEqual(env['newer_quote_rows'](previous,incoming,lambda c:c['family']),[incoming[1]])

    def test_native_fetch_time_does_not_promote_an_older_observation(self):
        previous=[{'family':'a','Odds checked (UTC)':'2026-11-13T23:44:00Z'}]
        incoming=[{'family':'a','Tier Source':'prizepicksapi.com','Tier Fetched At':'2026-11-13T23:50:00Z','Tier Observed At':'2026-11-13T18:00:00Z'}]
        self.assertEqual(env['newer_quote_rows'](previous,incoming,lambda c:c['family']),[])

class BackupPacingTests(unittest.TestCase):
    def test_daily_cap_is_not_monthly_exhaustion(self):
        scope={'odds_account_snapshot':lambda _: {'used':202,'limit':250},
               'odds_monthly_budget':lambda *_:{'remaining':48}}
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='primary_odds_quota_exhausted')
        exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),scope)
        self.assertFalse(scope['primary_odds_quota_exhausted'](None))
        scope['odds_monthly_budget']=lambda *_:{'remaining':0}
        self.assertTrue(scope['primary_odds_quota_exhausted'](None))

    def test_reported_daily_guard_does_not_spend_backup(self):
        from unittest.mock import Mock
        from types import SimpleNamespace
        fallback=Mock(side_effect=AssertionError('Backup must not run'))
        scope={'primary_odds_quota_exhausted':lambda _:False,
               'automatic_board_run':lambda *_:{'errors':['Daily OddsPAPI request guard reached']},
               'run_quota_fallback':fallback}
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='automatic_board_run_with_fallback')
        exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),scope)
        result=scope['automatic_board_run_with_fallback'](SimpleNamespace(store=None))
        self.assertEqual(result['active_odds_provider'],'OddsPAPI')
        fallback.assert_not_called()
