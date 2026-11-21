import ast
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock
import pandas as pd

source=Path('app.py').read_text()
tree=ast.parse(source)
names={'utc_timestamp','slate_day','today_pregame','scheduled_scoreboard_starts'}
nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
env={'pd':pd,'SLATE_TIMEZONE':'America/Los_Angeles'}
exec(compile(ast.Module(body=nodes,type_ignores=[]),'app.py','exec'),env)

class ScheduledScoreboardTests(TestCase):
    def test_pacific_date_range_and_future_filter(self):
        current=pd.Timestamp('2026-11-21T18:00:00Z')
        fetch=Mock(side_effect=[
            {'events':[{'date':'2026-11-21T17:00:00Z'},{'date':'2026-11-21T20:00:00Z'}]},
            {'events':[{'date':'2026-11-22T02:00:00Z'},{'date':'2026-11-22T20:00:00Z'}]}])
        starts=env['scheduled_scoreboard_starts'](fetch,'https://example.test/',current)
        self.assertEqual(len(starts),2)
        self.assertEqual(fetch.call_count,2)
        self.assertEqual([call.kwargs['ttl_seconds'] for call in fetch.call_args_list],[300,300])
        self.assertEqual([call.args[1]['dates'] for call in fetch.call_args_list],
                         ['20261121','20261122'])

    def test_scoreboard_errors_are_not_silenced(self):
        fetch=Mock(side_effect=RuntimeError('ESPN unavailable'))
        with self.assertRaises(RuntimeError):
            env['scheduled_scoreboard_starts'](fetch,'https://example.test/',
                pd.Timestamp('2026-11-21T18:00:00Z'))

    def test_invalid_events_fail_loudly(self):
        fetch=Mock(return_value={'events':None})
        with self.assertRaises(ValueError):
            env['scheduled_scoreboard_starts'](fetch,'https://example.test/',
                pd.Timestamp('2026-11-21T18:00:00Z'))
