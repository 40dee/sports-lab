import ast,json,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pandas as pd
import test_combo_regression as fixtures

TREE=ast.parse(Path(__file__).resolve().parents[1].joinpath('app.py').read_text())


class FrozenIDTests(unittest.TestCase):
    def setUp(self):
        self.store=fixtures.Memory()
        self.frozen=fixtures.populated(1).items('forecast')[0][1]
        self.card=dict(self.frozen['card'],**{'Fixture ID':'new-provider-id'})
        history=[{'GAME_DATE':'2026-11-01','GAME_ID':'past','MIN':30,'PTS':25}]
        self.frozen['result']['games']={'source_ref':'original-games'}
        self.store.put('source','original-games',{'data':json.dumps(history)})
        self.store.put('forecast',self.frozen['id'],self.frozen)
        self.st=Mock();self.st.session_state={}
        self.env=dict(fixtures.env,workflow_store=self.store,st=self.st,pd=pd,
                      ui_number=lambda value:float(value),ui_stat=lambda card:'Points',
                      resolve_player=Mock(side_effect=AssertionError('Must use frozen evidence')),
                      current_season=lambda:'2026-27',
                      line_freshness=Mock(return_value=(True,'')),pick_side_note=lambda *args:'',
                      saved_prediction=Mock(return_value=self.frozen),now=lambda:'now')
        names={'ui_matched_forecast','ui_frozen_history','ui_history','ui_save_pick','ui_recent_chart','recent_board_passes'}
        exec(compile(ast.Module(body=[n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[]),'app.py','exec'),self.env)
    def test_history_and_chart_use_original_forecast_after_provider_id_changes(self):
        self.assertNotEqual(fixtures.env['identity'](self.card),self.frozen['id'])
        self.assertEqual(len(self.env['ui_history'](self.card,self.frozen)),1)
        self.env['resolve_player'].assert_not_called()
        self.st.segmented_control.side_effect=['L15','All']
        self.env['pick_history_sample']=lambda *args,**kwargs:pd.DataFrame({'VALUE':[25.]})
        self.env['pick_chart_spec']=lambda *args:{}
        self.env['ui_recent_chart'](self.card,None,self.frozen)
        self.st.vega_lite_chart.assert_called_once()
        self.assertEqual(self.st.segmented_control.call_args_list[0].kwargs['key'],
                         'chart_window_buttons_'+self.frozen['id'])
    def test_save_toggle_uses_canonical_id_and_preserves_frozen_record(self):
        before=self.store.get('forecast',self.frozen['id'])
        self.assertTrue(self.env['ui_save_pick'](self.card,self.frozen))
        records=self.store.items('watchlist')
        self.assertEqual(records[0][0],self.frozen['id'])
        self.assertEqual(records[0][1]['forecast_id'],self.frozen['id'])
        self.assertFalse(self.env['ui_save_pick'](self.card,self.frozen))
        self.assertFalse(self.store.get('watchlist',self.frozen['id'])['active'])
        self.assertEqual(before,self.store.get('forecast',self.frozen['id']))
    def test_detail_lookup_resolves_semantic_match_before_hydrating(self):
        self.env['saved_prediction']=fixtures.env['saved_prediction']
        lookup=fixtures.env['forecast_index']([(self.frozen['id'],self.frozen)])
        self.assertEqual(self.env['ui_matched_forecast'](self.card,forecasts=lookup)['id'],self.frozen['id'])
    def test_manual_history_query_is_limited_on_server(self):
        query=Mock(return_value=[(json.dumps({'day':'2026-11-18'}),)])
        store=SimpleNamespace(query=query,items=Mock(side_effect=AssertionError('No namespace download')))
        self.env['recent_board_passes'](store,{'day':'2026-11-18','started_at':'start'})
        self.assertIn('LIMIT 5',query.call_args.args[0])
        store.items.assert_not_called()
    def test_explicit_no_match_does_not_scan_forecast_history_again(self):
        self.env['saved_forecast_records']=Mock(side_effect=AssertionError('No full forecast read'))
        self.assertEqual(self.env['ui_matched_forecast'](self.card,{}),{})
        self.env['saved_forecast_records'].assert_not_called()
    def test_extended_chart_windows_retain_full_history_support(self):
        from contextlib import nullcontext
        full=pd.DataFrame({'GAME_ID':['one','two']})
        self.env['fetch_player_history']=Mock(return_value=full)
        self.st.spinner=Mock(return_value=nullcontext())
        self.st.session_state['chart_window_buttons_'+self.frozen['id']]='Season'
        result=self.env['ui_history'](self.card,self.frozen)
        self.assertEqual(len(result),2)
        self.env['fetch_player_history'].assert_called_once_with('0',self.card['Player'],'2026-27')
