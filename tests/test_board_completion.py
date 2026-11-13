import ast
import copy
import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace
import test_combo_regression as fixture


class BoardCompletionTests(unittest.TestCase):
    def ready(self):
        return fixture.populated(3)

    def test_ready_board_survives_active_failed_and_partial_import(self):
        for stage in ('importing','preparing','finished','failed'):
            store=self.ready()
            store.put('board_run','2026-11-09',{'stage':stage,'import_complete':False})
            self.assertTrue(fixture.env['combo_board_ready'](store,fixture.REF)[0])
        store.put('board_run','2026-11-09',{})
        self.assertFalse(fixture.env['combo_board_ready'](store,fixture.REF)[0])

    def test_pending_game_does_not_enter_combos_or_hold_other_games(self):
        store=fixture.populated(6)
        missing=[k for k,v in store.items('forecast') if v['event_id']=='0']
        for key in missing:del store.records['forecast',key]
        store.put('board_run','2026-11-09',{'stage':'finished','import_complete':False})
        self.assertTrue(fixture.env['combo_board_ready'](store,fixture.REF)[0])
        report=fixture.env['update_combos'](store,True,fixture.REF)
        self.assertGreater(report['generated'],0)
        for _,combo in store.items('combo'):
            self.assertTrue(all(leg['forecast_id'] not in missing for leg in combo['legs']))
        # Completion flags cannot make an empty projection set usable.
        store.records={k:v for k,v in store.records.items() if k[0]!='forecast'}
        self.assertFalse(fixture.env['combo_board_ready'](store,fixture.REF)[0])

    def test_changed_board_revalidates_exact_rows_and_empty_board_blocks(self):
        store=self.ready();board=store.get('board','board')
        board['v4_scan_at']='new'
        board['v4_rows'][0]['Line']=99.5
        store.put('board','board',board)
        self.assertTrue(fixture.env['combo_board_ready'](store,fixture.REF)[0])
        pool,_=fixture.env['combo_pool'](store,fixture.REF)
        self.assertTrue(all(item['card']['Line']!=99.5 for item in pool))
        store.put('board','board',{'v4_rows':[]})
        self.assertFalse(fixture.env['combo_board_ready'](store,fixture.REF)[0])

    def test_missed_and_unavailable_rows_do_not_hide_ready_games(self):
        store=self.ready();board=store.get('board','board')
        skipped=dict(board['v4_rows'][0])
        skipped['Start (UTC)']='2026-11-09T17:00:00Z'
        skipped['Fixture ID']='earlier-game'
        board['v4_rows'].append(skipped);store.put('board','board',board)
        self.assertTrue(fixture.env['combo_board_ready'](store,fixture.REF)[0])
        store.put('calculation_status',fixture.env['identity'](skipped),{'Status':'Unavailable'})
        self.assertTrue(fixture.env['combo_board_ready'](store,fixture.REF)[0])
        coverage=fixture.env['board_coverage'](SimpleNamespace(store=store),board,fixture.REF)
        self.assertEqual(coverage['missed_start'],0)
        self.assertEqual(coverage['upcoming_unavailable'],1)

    def test_saved_combo_still_checks_its_own_lines_during_import(self):
        store=self.ready();pool,_=fixture.env['combo_pool'](store,fixture.REF)
        combo=fixture.env['combo_snapshot'](pool,'balanced-v1',fixture.REF)
        store.put('board_run','2026-11-09',{'stage':'importing'})
        self.assertTrue(fixture.env['combo_live_check'](store,combo,fixture.REF)[0])
        board=store.get('board','board');board['v4_rows']=[];store.put('board','board',board)
        self.assertFalse(fixture.env['combo_live_check'](store,combo,fixture.REF)[0])

    def test_stage_timestamps_persist_without_changing_source_or_spending(self):
        env=dict(fixture.env)
        node=next(n for n in fixture.tree.body if isinstance(n,ast.FunctionDef) and n.name=='board_run_stage')
        exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),env)
        env['slate_day']=lambda:'2026-11-09'
        stamps=iter(['2026-11-09T18:00:00Z','2026-11-09T18:02:00Z','2026-11-09T18:07:00Z'])
        env['now']=lambda:next(stamps)
        store=self.ready();board=store.get('board','board')
        env['board_run_stage'](store,'importing')
        env['board_run_stage'](store,'preparing',True)
        record=env['board_run_stage'](store,'finished')
        self.assertEqual(record['started_at'],'2026-11-09T18:00:00Z')
        self.assertEqual(record['import_finished_at'],'2026-11-09T18:02:00Z')
        self.assertEqual(record['finished_at'],'2026-11-09T18:07:00Z')
        self.assertEqual(len(store.items('board_run_history')),1)
        self.assertEqual(store.get('board','board'),board)

    def test_timing_ui_shows_both_durations_and_partial_hold(self):
        env=dict(fixture.env)
        node=next(n for n in fixture.tree.body if isinstance(n,ast.FunctionDef) and n.name=='render_board_run_status')
        exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),env)
        env['slate_day']=lambda:'2026-11-09'
        env['combo_board_ready']=lambda *_:(False,'incomplete')
        store=self.ready()
        store.put('board_run','2026-11-09',{'stage':'finished','started_at':'2026-11-09T18:00:00Z',
            'import_finished_at':'2026-11-09T18:02:00Z','finished_at':'2026-11-09T18:07:00Z'})
        st=Mock();st.expander.return_value.__enter__=Mock();st.expander.return_value.__exit__=Mock()
        env['render_board_run_status'](store,st)
        self.assertIn('Total 7m 0s',st.caption.call_args_list[0].args[0])
        self.assertIn('Import 2m 0s',st.caption.call_args_list[0].args[0])
        self.assertIn('Projections 5m 0s',st.caption.call_args_list[0].args[0])
        st.info.assert_not_called()

    def test_all_four_tier_buckets_stay_separate(self):
        store=fixture.populated(6);board=store.get('board','board')
        for card in list(board['v4_rows']):
            original=store.get('forecast',fixture.env['identity'](card))
            for tier,line in [('Goblin',10.5),('Demon',30.5)]:
                c=dict(card,Line=line,**{'Line Type':tier,'Available Sides':['MORE']})
                f=copy.deepcopy(original);f.update(id=fixture.env['identity'](c),card=c)
                store.put('forecast',f['id'],f);board['v4_rows'].append(c)
        store.put('board','board',board)
        fixture.env['update_combos'](store,True,fixture.REF)
        for mode in ('standard','goblin','demon','mixed'):
            snapshot=store.get('combo_suggestions','2026-11-09'+(':'+mode if mode!='mixed' else ''))
            self.assertEqual(snapshot['tier_mode'],mode)
            self.assertTrue(snapshot['sizes']['2'])
            if mode!='mixed':
                for keys in snapshot['sizes'].values():
                    for key in keys:
                        self.assertTrue(all(l['card']['Line Type'].lower()==mode for l in store.get('combo',key)['legs']))

