import ast
import copy
from pathlib import Path
import unittest
from unittest.mock import MagicMock
import pandas as pd
from audit_display import format_table, format_value
from projection_v2 import render_backtest_panel

ROOT=Path(__file__).resolve().parents[1]

class AuditDisplayTests(unittest.TestCase):
    def test_percent_error_weight_and_missing_formats_do_not_mutate(self):
        rows=[{'v1_hit':.5423,'v2_hit':None,'v1_mae':4.414,'v2_brier':.26291,
               'effective_examples':12.345,'weighted_greens':6.234,'weighted_reds':6.111,
               'historical_accuracy':.5423,'shrunk_accuracy':float('nan'),
               'candidate_brier':.25091,'baseline_brier':None,
               'candidate_top_accuracy':.6,'gap_top_accuracy':.4999}]
        original=copy.deepcopy(rows)
        frame=format_table(rows)
        expected={'v1 Hit %':'54.2%','v2 Hit %':'—','v1 MAE':'4.41','v2 Brier':'0.2629',
                  'Samples':'12.3','Wins':'6.2','Losses':'6.1','Raw Hit %':'54.2%',
                  'Smoothed Hit %':'—','Candidate Brier':'0.2509','Baseline Brier':'—',
                  'Candidate Top Hit %':'60.0%','Gap Top Hit %':'50.0%'}
        self.assertEqual(frame.iloc[0].to_dict(),expected)
        self.assertEqual(rows[0]['v1_hit'],original[0]['v1_hit'])
        self.assertTrue(pd.isna(rows[0]['shrunk_accuracy']))
        self.assertEqual(format_value(float('inf'),'percent'),'—')
        cutoff=format_table([{'metric':'prob','cutoff':.58},{'metric':'gap','cutoff':.4}])
        self.assertEqual(list(cutoff['Cutoff']),['58.0%','0.40'])

    def test_backtest_metrics_and_all_tables_are_readable(self):
        summary={'picks':10,'player_game_markets':8.,'v1_hit':.542,'v2_hit':.556,
                 'v1_mae':4.414,'v2_mae':4.305,'v1_brier':.2629,'v2_brier':.2509}
        result=dict(summary,limitation='Saved data only',prior_history_rows=5,vegas_rows=4,
                    excluded={'missing_minutes':2})
        keys=('by_platform_tier','by_stat','by_direction','by_confidence_cutoff','by_edge_tier','by_sportsbook_agreement','by_day')
        for key in keys:result[key]=[summary.copy()]
        original=copy.deepcopy(result)
        st=MagicMock();st.session_state={'_v2_backtest_result':result};st.button.return_value=False
        st.selectbox.side_effect=lambda label,options,**kwargs:options[0]
        cols=[MagicMock() for _ in range(4)];st.columns.return_value=cols
        render_backtest_panel(None,st)
        metrics=[call.args for col in cols for call in col.metric.call_args_list]
        self.assertIn(('v1 vs v2 Brier','0.2629 → 0.2509'),metrics)
        self.assertIn(('Picks','10'),metrics)
        self.assertEqual(len(metrics),8)
        for call in st.dataframe.call_args_list[:7]:
            self.assertEqual(call.args[0].iloc[0]['v1 Hit %'],'54.2%')
        self.assertEqual(st.dataframe.call_args_list[-1].args[0].iloc[0].to_dict(),{'Reason':'Missing minutes','Count':2})
        st.write.assert_not_called();self.assertEqual(result,original)

    def test_saved_research_uses_components_and_notes_without_json(self):
        tree=ast.parse((ROOT/'app.py').read_text())
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='ui_frozen_sources')
        st=MagicMock();env={'st':st,'pd':pd}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),env)
        result={'minutes':32.123,'components':[{'Stat':'PTS','Projection':19.12345,'Pace factor':1.0234}],
                'learning_model':{'version':'v1','alpha':1.12345},'availability':{'status':'Questionable','notes':'Saved injury report'},
                'warnings':['Minutes restriction possible'],'probability_method':'Saved empirical'}
        original=copy.deepcopy(result)
        env['ui_frozen_sources'](result)
        st.metric.assert_called_once_with('Projected minutes','32.12')
        st.json.assert_not_called()
        table=st.dataframe.call_args_list[0].args[0]
        self.assertEqual(table.iloc[0]['Projection'],'19.12')
        self.assertEqual(table.iloc[0]['Pace factor'],'1.02')
        st.warning.assert_called_once_with('Minutes restriction possible')
        self.assertTrue(any(c.args[0]=='Model version: v1' for c in st.caption.call_args_list))
        self.assertEqual(result,original)


class SettingsReadabilityTests(unittest.TestCase):
    def test_wrapped_cards_and_strip_escape_and_do_not_stack_columns(self):
        from audit_display import render_fact_cards, render_metric_strip, display_time
        st=MagicMock()
        render_fact_cards(st,[{'Headline':'<script>long headline</script>','Rows':None,'Count':472}])
        html=st.markdown.call_args.args[0]
        self.assertIn('&lt;script&gt;',html);self.assertNotIn('<script>',html)
        self.assertIn('overflow-wrap:anywhere',html);self.assertNotIn('472.00',html)
        self.assertIn('—',html)
        render_metric_strip(st,[(name,'0') for name in ('Imported','Ready','Pending','Unavailable','Missed Start')])
        html=st.markdown.call_args.args[0]
        self.assertIn('repeat(5,minmax(0,1fr))',html)
        self.assertFalse(any(line.startswith('    <') for line in html.splitlines()))
        st.columns.assert_not_called()
        self.assertEqual(display_time('2026-11-20T17:08:01.333Z'),'Nov 20, 2026 · 09:08 AM PT')
        self.assertEqual(display_time(None),'—')

    def test_saved_probe_is_readable_without_running_provider_or_writing(self):
        import the_odds_api
        from unittest.mock import patch
        result={'state':'sample_received','checked_at':'2026-11-11T04:12:13.474304Z',
            'credits_remaining':472,'credits_used':28,'game':'Boston at Toronto','start_utc':'2026-11-12T00:10Z',
            'market_counts':{'player_points':5,'player_points_alternate':19},
            'scheduled_downloads_enabled':False,'rows':[{'Line':23.5,'Market':'player_points'}]}
        before=copy.deepcopy(result)
        store=MagicMock();store.get.return_value=result
        st=MagicMock();st.button.return_value=False;st.session_state={}
        with patch.object(the_odds_api,'run_probe',side_effect=AssertionError('No provider request')):
            the_odds_api.render_probe(st,store,'configured')
        st.json.assert_not_called();store.put.assert_not_called()
        rendered=' '.join(str(c.args) for c in st.markdown.call_args_list+st.caption.call_args_list)
        self.assertNotIn('player_points',rendered);self.assertNotIn('474304',rendered)
        self.assertIn('Credits remaining',rendered);self.assertIn('Alternate points',rendered)
        self.assertIn('Nov 11, 2026 · 04:10 PM PT',rendered)
        self.assertEqual(result,before)

    def test_preparation_input_checks_use_named_wrapped_cards_and_compact_counts(self):
        tree=ast.parse((ROOT/'app.py').read_text())
        node=copy.deepcopy(next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='preparation_panel'))
        # Run the real saved-input section, without the separate manual preparation controls.
        index=next(i for i,n in enumerate(node.body) if isinstance(n,ast.With))
        node.body=node.body[:index+1]
        st=MagicMock();store=MagicMock()
        inputs={'checked_at':'2026-11-20T17:08:01Z','sources':{'load_position_defense':{'available':True,'rows':150},
                'news':{'available':True,'articles':[{'headline':'Long headline','published':'2026-11-20T17:00Z'}]}},
                'limitations':['Confirmed starting lineups are not connected.']}
        coverage={'imported':10,'ready':8,'upcoming_pending':1,'upcoming_unavailable':1,'missed_start':0}
        store.get.side_effect=lambda ns,key:inputs if ns=='input_health' else coverage
        from types import SimpleNamespace
        env={'st':st,'pd':pd,'slate_day':lambda:'2026-11-20'}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),env)
        env['preparation_panel'](SimpleNamespace(store=store),st,[])
        rendered=' '.join(str(c.args) for c in st.markdown.call_args_list)
        self.assertIn('Position defense',rendered);self.assertNotIn('load_position_defense',rendered)
        self.assertIn('NBA news',rendered);self.assertIn('Long headline',rendered)
        self.assertIn('sl-audit-metric-strip',rendered)
        st.dataframe.assert_not_called();st.columns.assert_not_called();store.put.assert_not_called()


class WarmSettingsDisplayTests(unittest.TestCase):
    def test_old_display_modules_refresh_without_provider_actions(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import patch
        tree=ast.parse((ROOT/'app.py').read_text())
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_refresh_settings_display')
        env={};exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),env)
        display,provider=SimpleNamespace(),SimpleNamespace()
        def reload(module):
            if module is display:module.render_fact_cards=True
            else:module.READABLE_PROBE_UI=True
            return module
        with patch.dict(sys.modules,{'audit_display':display,'the_odds_api':provider}),patch('importlib.reload',side_effect=reload) as refresh:
            env['_refresh_settings_display']();env['_refresh_settings_display']()
            self.assertEqual([c.args[0] for c in refresh.call_args_list],[display,provider])
