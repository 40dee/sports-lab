import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from test_targeted_quotes import Store
import odds_fallback

TREE=ast.parse(Path('app.py').read_text())
class SplitSourcesTests(unittest.TestCase):
    def test_regular_request_has_no_fantasy_markets(self):
        node=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name=='run_quota_fallback')
        assignment=next(n for n in node.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='regular_markets' for t in n.targets))
        env={'BOARD_MARKETS':odds_fallback.BOARD_MARKETS,'FANTASY_MARKETS':odds_fallback.FANTASY_MARKETS}
        exec(compile(ast.Module(body=[assignment],type_ignores=[]),'app.py','exec'),env)
        self.assertTrue(env['regular_markets'])
        self.assertFalse(set(env['regular_markets']) & set(odds_fallback.FANTASY_MARKETS))
        self.assertEqual(len(env['regular_markets']),5)
        self.assertFalse(any(m.endswith('_alternate') for m in env['regular_markets']))

    def test_native_capture_requests_only_nba_fantasy_score(self):
        node=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name=='import_daily_prizepicks_board')
        downloader=Mock(return_value=({'items':[],'slate_day':'2026-11-15','partial':False},1))
        env={'feed_request_cap':lambda:10,'setting':lambda *args:'key',
             'pp_download_pages':downloader,'pp_cached_items':lambda _:[],
             'board_download_progress':Mock()}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),env)
        env['import_daily_prizepicks_board'](SimpleNamespace(store=Store()),{}, {},'2026-11-15')
        downloader.assert_called_once()
        args=downloader.call_args.args
        self.assertEqual(args[1],'pp_fantasy_daily_v1')
        self.assertEqual(args[3],{'league':'NBA','status':'active','stat':'Fantasy Score','view':'compact','limit':100})
