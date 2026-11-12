import ast
import unittest
import time
from types import SimpleNamespace
from unittest.mock import Mock
import test_combo_regression as f
from test_odds_fallback import Store,response,event,NOW
import odds_fallback as fallback


class LiveProgressTests(unittest.TestCase):
    def test_fallback_publishes_each_game_before_next_request_and_reuses_without_credit(self):
        store=Store();first=event();second=event();second['id']='second'
        observed=[]
        replies=iter([response([first,second]),response(first),response(second)])
        def get(*args,**kw):
            observed.append(store.get('board_download','2026-11-09'))
            return next(replies)
        rows,report=fallback.download(store,'key',NOW,get)
        self.assertEqual(observed[1]['games_done'],0)
        self.assertEqual(observed[2]['games_done'],1)
        self.assertEqual(observed[2]['games_total'],2)
        progress=store.get('board_download','2026-11-09')
        self.assertEqual(progress['games_done'],2)
        never=Mock(side_effect=AssertionError('Unexpected paid request'))
        fallback.download(store,'key',NOW,never)
        never.assert_not_called()
        self.assertEqual(store.get('board_download','2026-11-09')['games_done'],2)

    def test_percentages_keep_download_and_projection_progress_separate(self):
        env=dict(f.env)
        node=next(n for n in f.tree.body if isinstance(n,ast.FunctionDef) and n.name=='render_board_run_status')
        exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),env)
        env['slate_day']=lambda:'2026-11-09'
        env['combo_board_ready']=lambda *_:(False,'held')
        store=f.Memory()
        store.put('board_run','2026-11-09',{'stage':'preparing','started_at':f.REF})
        store.put('board_download','2026-11-09',{'games_total':10,'games_done':10,'state':'partial'})
        store.put('preparation','last',{'run_at':f.REF,'total':100,'ready':25,'unavailable':5,'pending':70})
        st=Mock();st.expander.return_value.__enter__=Mock();st.expander.return_value.__exit__=Mock()
        env['render_board_run_status'](store,st)
        self.assertEqual(st.progress.call_args_list[0].args,(1.,))
        self.assertEqual(st.progress.call_args_list[1].args,(.25,))
        self.assertIn('25/100',st.progress.call_args_list[1].kwargs['text'])
        st.info.assert_called_once_with('Board worker is preparing.')

    def test_finished_progress_does_not_load_forecasts_before_page_body(self):
        env=dict(f.env)
        node=next(n for n in f.tree.body if isinstance(n,ast.FunctionDef) and n.name=='render_board_run_status')
        exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),env)
        env['slate_day']=lambda:'2026-11-09'
        env['combo_board_ready']=Mock(side_effect=AssertionError('Progress must not scan forecasts'))
        store=f.Memory()
        store.put('board_run','2026-11-09',{'stage':'finished','started_at':f.REF,'finished_at':f.REF})
        st=Mock();st.expander.return_value.__enter__=Mock();st.expander.return_value.__exit__=Mock()
        env['render_board_run_status'](store,st)
        env['combo_board_ready'].assert_not_called()

    def test_fragment_reads_new_worker_records_and_invalidates_cached_lists(self):
        env=dict(f.env)
        node=next(n for n in f.tree.body if isinstance(n,ast.FunctionDef) and n.name=='render_live_board_status')
        exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),env)
        env['slate_day']=lambda:'2026-11-09';env['render_board_run_status']=Mock()
        store=f.Memory();store._read_cache={('items','board_run',None):'stale',('get','preparation','last'):'stale'}
        st=Mock();st.session_state={}
        env['render_live_board_status'](store,st)
        self.assertFalse(store._read_cache)
        st.rerun.assert_not_called()
        store.put('board_run','2026-11-09',{'stage':'finished','finished_at':f.REF})
        env['render_live_board_status'](store,st)
        st.rerun.assert_not_called()

    def test_progress_writes_do_not_change_board_or_combo_readiness(self):
        store=f.populated(3);before=store.get('board','board')
        f.env['board_download_progress'](store,games_done=10,games_total=10)
        self.assertEqual(store.get('board','board'),before)
        self.assertIsNone(store.get('board_run',f.env['slate_day']()))

    def test_preparation_publishes_intermediate_ready_count(self):
        env=dict(f.env,time=time)
        node=next(n for n in f.tree.body if isinstance(n,ast.FunctionDef) and n.name=='prepare_saved_board')
        exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),env)
        env.update(preparation_rows=lambda rows:rows,today_pregame=lambda *_:True)
        store=f.Memory();updates=[];original=store.put
        def put(ns,key,value,**kw):
            original(ns,key,value,**kw)
            if ns=='preparation':updates.append(store.get(ns,key))
        store.put=put
        board=f.populated(6).get('board','board')
        def calculate(card,*_):
            item={'id':env['identity'](card),'card':dict(card),'created_at':f.REF,'result':{},'event_id':'x'}
            store.put('forecast',item['id'],item)
            return item
        wf=SimpleNamespace(store=store,e={'fetch_player_history':lambda *_:None,'current_season':lambda:'2026-27'},
            player=Mock(),event_for=Mock(),calculate=calculate)
        report=env['prepare_saved_board'](wf,board,max_picks=6)
        self.assertTrue(any(v.get('ready')==1 and v.get('total')==6 for v in updates))
        self.assertEqual(report['ready'],6)
        self.assertEqual(report['state'],'complete')

