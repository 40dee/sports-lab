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
