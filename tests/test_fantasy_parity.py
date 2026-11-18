import ast
import copy
from pathlib import Path
import unittest
import pandas as pd
from streamlit.testing.v1 import AppTest
import test_combo_regression as fixtures

class FantasyParityTests(unittest.TestCase):
    def test_six_stat_history_scoring_requires_complete_components(self):
        source=Path(fixtures.SOURCE).read_text();tree=ast.parse(source)
        env={'pd':pd}
        for name in ('fantasy_weights','market_columns','score_frame'):
            node=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name][-1]
            exec(compile(ast.Module(body=[node],type_ignores=[]),str(fixtures.SOURCE),'exec'),env)
        card={'Platform':'prizepicks','Period':'result','Stat':'Fantasy Score'}
        frame=pd.DataFrame([{'PTS':20,'REB':10,'AST':5,'STL':2,'BLK':1,'TOV':3},
                            {'PTS':20,'REB':10,'AST':5,'STL':None,'BLK':1,'TOV':3}])
        result=env['score_frame'](frame,card)
        self.assertAlmostEqual(result.iloc[0],45.5)
        self.assertTrue(pd.isna(result.iloc[1]))

    def test_fantasy_results_have_date_dropdown_separate_record_and_research_link(self):
        store=fixtures.populated(1);board=store.get('board','board');card=board['v4_rows'][0]
        old=store.get('forecast',fixtures.env['identity'](card))
        card.update({'Stat':'Fantasy Score','Stat Key':'fantasy_score','Period':'result'})
        key=fixtures.env['identity'](card)
        old.update(id=key,card=copy.deepcopy(card))
        store.put('forecast',key,old)
        store.put('grade',key,{'status':'FINAL','outcome':'WIN','actual':45.5})
        store.put('board','board',board)
        app=AppTest.from_string(Path(fixtures.SOURCE).read_text().replace('\nrender_app_pages()\n','\nrender_fantasy_plays(workflow,st)\n'),default_timeout=60).run()
        app.session_state['_sports_lab_offline_records_v1']=store.records
        app.session_state['sports_lab_page_buttons']='Fantasy'
        app.session_state['fantasy_view_buttons_v2']='Results'
        app.run()
        self.assertFalse(app.exception,list(app.exception))
        self.assertTrue(any(x.label=='Game day · Pacific' for x in app.selectbox))
        self.assertFalse(any(x.label=='Game day · Pacific' for x in app.get('pills')))
        self.assertTrue(any('1 green · 0 red' in x.value for x in app.caption))
        self.assertTrue(any(x.key=='fantasy_research_'+key and x.label.strip()=='Player 0' for x in app.button))
        self.assertTrue(any(x.label=='Fantasy directional hit rate' and x.value=='100.0%' for x in app.metric))

