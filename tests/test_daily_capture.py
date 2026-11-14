import unittest
from datetime import datetime, timezone
from test_targeted_quotes import Store
from daily_capture import claim_capture, close_capture, require_capture
import odds_fallback

class DailyCaptureTests(unittest.TestCase):
    def test_one_atomic_capture_per_day_and_next_day_opens(self):
        store=Store();day='2026-11-13';stamp=day+'T18:00:00Z'
        self.assertTrue(claim_capture(store,day,stamp))
        self.assertFalse(claim_capture(store,day,stamp))
        require_capture(store,day)
        close_capture(store,day,stamp)
        with self.assertRaises(ValueError):require_capture(store,day)
        self.assertFalse(claim_capture(store,day,stamp))
        self.assertTrue(claim_capture(store,'2026-11-14',stamp))

    def test_enabled_policy_blocks_unscheduled_calls_before_capture(self):
        store=Store();store.put('board_capture_policy','enabled',True)
        with self.assertRaises(ValueError):require_capture(store,'2026-11-13')

    def test_closed_capture_blocks_backup_credit_reservations(self):
        store=Store();day='2026-11-13'
        claim_capture(store,day,day+'T18:00:00Z');close_capture(store,day,day+'T18:01:00Z')
        with self.assertRaises(ValueError):
            odds_fallback.reserve(store,'key',datetime(2026,11,13,20,tzinfo=timezone.utc),500)
        self.assertEqual(store.query("SELECT key FROM sports_records WHERE namespace='request_budget'",fetch=True),[])

    def test_interrupted_capture_cannot_be_claimed_again(self):
        store=Store();day='2026-11-13';claim_capture(store,day,day+'T18:00:00Z')
        self.assertFalse(claim_capture(store,day,day+'T18:30:00Z'))
        close_capture(store,day,day+'T18:30:00Z')
        with self.assertRaises(ValueError):require_capture(store,day)

class DailyWorkerTests(unittest.TestCase):
    def test_completed_capture_reuses_cache_and_failure_cannot_retry(self):
        import ast
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import Mock
        import pandas as pd
        source=ast.parse(Path('app.py').read_text())
        node=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='daily_cached_board_run')
        day='2026-11-13';stamp=day+'T18:00:00Z'
        provider=Mock(return_value={'capture':'done'})
        env={'slate_day':lambda *args:day,'now':lambda:stamp,'pd':pd,
             'utc_timestamp':lambda v:pd.to_datetime(v,utc=True,errors='coerce'),
             'odds_account_snapshot':Mock(side_effect=ValueError('No account')),
             'automatic_board_run_with_fallback':provider,
             'prepare_saved_board':Mock(return_value={'ready':1}),
             'board_coverage':Mock(return_value={'ready':1})}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),env)
        store=Store();wf=SimpleNamespace(store=store)
        self.assertEqual(env['daily_cached_board_run'](wf),{'capture':'done'})
        env['daily_cached_board_run'](wf);provider.assert_called_once()
        self.assertEqual(store.get('daily_board_capture',day)['state'],'closed')
        # A first-pass exception also seals the day before any later worker.
        store=Store();wf.store=store;provider.reset_mock();provider.side_effect=ValueError('Provider failed')
        with self.assertRaises(ValueError):env['daily_cached_board_run'](wf)
        env['daily_cached_board_run'](wf);provider.assert_called_once()
