import ast
import copy
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import test_combo_regression as f


def preparation_env():
    env=dict(f.env,time=time)
    node=next(n for n in f.tree.body if isinstance(n,ast.FunctionDef) and n.name=='prepare_saved_board')
    exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),env)
    env.update(preparation_rows=lambda rows:rows,today_pregame=lambda *_:True,
        calculation_failure_details=lambda e:{'reason':str(e),'error_type':type(e).__name__,'location':'test'})
    return env


class PreparationSpeedTests(unittest.TestCase):
    def test_compact_reader_is_preferred_without_full_history_read(self):
        compact=Mock(return_value=[('small',{'id':'small'})])
        store=SimpleNamespace(forecast_items=compact,items=Mock(side_effect=AssertionError('Full evidence fetched')))
        self.assertEqual(f.env['saved_forecast_records'](store),[('small',{'id':'small'})])
        compact.assert_called_once()

    def test_freeze_uses_event_index_and_keeps_first_forecast_immutable(self):
        env=dict(f.env,validate_forecast_numbers=lambda *_:None)
        klass=next(n for n in f.tree.body if isinstance(n,ast.ClassDef) and n.name=='Workflow')
        node=next(n for n in klass.body if isinstance(n,ast.FunctionDef) and n.name=='freeze')
        exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),env)
        env['unique_forecasts']=Mock(side_effect=AssertionError('Repeated historical scan'))
        store=f.Memory()
        wf=SimpleNamespace(store=store,_preparation_event_index={},record_paper_forecast=Mock(),
            event_for=lambda _c:({'id':'event'},{'status':{'type':{'state':'pre'}}}),
            e={'st':SimpleNamespace(session_state={})})
        card=f.populated(1).get('board','board')['v4_rows'][0]
        card['Start (UTC)']='2030-11-10T02:00:00Z'
        for line in range(1,41):
            c=dict(card,Line=float(line))
            first=env['freeze'](wf,c,{'projection':22},'2026-27')
            second=env['freeze'](wf,c,{'projection':999},'2026-27')
            self.assertEqual(first,second)
            self.assertEqual(second['result']['projection'],22)
        env['unique_forecasts'].assert_not_called()
        self.assertEqual(len(store.items('forecast')),40)

    def test_player_wide_failure_checked_once_per_pass_but_unrelated_player_runs(self):
        env=preparation_env();store=f.Memory()
        a,b=f.populated(2).get('board','board')['v4_rows']
        cards=[dict(a,Line=float(n)) for n in (10,11,12)]+[b]
        def calculate(card,*_):
            if card['Player']==a['Player']:raise ValueError('Player is listed out or suspended; no playable projection.')
            return {'id':env['identity'](card),'card':card,'created_at':f.REF,'event_id':'x','result':{}}
        wf=SimpleNamespace(store=store,e={'fetch_player_history':lambda *_:None,'current_season':lambda:'2026-27'},
            player=Mock(),event_for=Mock(),calculate=Mock(side_effect=calculate))
        report=env['prepare_saved_board'](wf,{'v4_rows':cards},max_picks=10)
        self.assertEqual(wf.calculate.call_count,2)
        self.assertEqual(report['ready'],1)
        self.assertEqual(report['unavailable'],3)
        self.assertEqual(report['newly_ready'],1)
        self.assertIsNone(wf._preparation_event_index)

    def test_market_specific_failures_are_not_shared_with_other_lines(self):
        env=preparation_env();store=f.Memory()
        card=f.populated(1).get('board','board')['v4_rows'][0]
        cards=[dict(card,Line=float(n)) for n in (10,11,12)]
        wf=SimpleNamespace(store=store,e={'fetch_player_history':lambda *_:None,'current_season':lambda:'2026-27'},
            player=Mock(),event_for=Mock(),calculate=Mock(side_effect=ValueError('Unknown market error')))
        report=env['prepare_saved_board'](wf,{'v4_rows':cards},max_picks=10)
        self.assertEqual(wf.calculate.call_count,3)
        self.assertEqual(report['unavailable'],3)

