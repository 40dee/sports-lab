import ast
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
import pandas as pd
import test_combo_regression as f

class ComboRefreshTests(unittest.TestCase):
    def test_new_board_and_health_invalidate_earlier_empty_view(self):
        store=f.populated(1);env=f.env
        initial=env['combo_inputs_version'](store,f.REF)
        board=store.get('board','board');board['v4_scan_at']='new';store.put('board','board',board)
        self.assertNotEqual(initial,env['combo_inputs_version'](store,f.REF))
        previous=env['combo_inputs_version'](store,f.REF)
        health=store.get('health','2026-11-09');health['checked_at']='new';store.put('health','2026-11-09',health)
        self.assertNotEqual(previous,env['combo_inputs_version'](store,f.REF))
        self.assertNotEqual(env['combo_inputs_version'](store,f.REF),env['combo_inputs_version'](store,pd.Timestamp(f.REF)+pd.Timedelta(minutes=6)))

    def test_stale_health_refresh_deduplicates_players_and_reuses_fresh_checks(self):
        env=dict(f.env)
        node=next(n for n in f.tree.body if isinstance(n,ast.FunctionDef) and n.name=='refresh_combo_health')
        check=Mock(return_value={})
        env['refresh_board_health']=check
        exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),env)
        store=f.populated(1);wf=SimpleNamespace(store=store)
        board=store.get('board','board');card=board['v4_rows'][0]
        board['v4_rows']=[card,dict(card,Line=22.5)]
        env['refresh_combo_health'](wf,board,f.REF);check.assert_not_called()
        health=store.get('health','2026-11-09')
        next(iter(health['players'].values()))['checked_at']=(pd.Timestamp(f.REF)-pd.Timedelta(hours=2)).isoformat()
        store.put('health','2026-11-09',health)
        env['refresh_combo_health'](wf,board,f.REF)
        check.assert_called_once()
        self.assertEqual(len(check.call_args.args[1]['v4_rows']),1)
