import ast
from pathlib import Path
import unittest
import test_combo_regression as fixtures
from streamlit.testing.v1 import AppTest

node=next(n for n in fixtures.tree.body if isinstance(n,ast.FunctionDef) and n.name=='mark_combo_played')
env=dict(fixtures.env)
exec(compile(ast.Module(body=[node],type_ignores=[]),str(fixtures.SOURCE),'exec'),env)


def populated():
    store=fixtures.populated(2)
    pool,_=fixtures.env['combo_pool'](store,fixtures.REF)
    combo=fixtures.env['combo_snapshot'](pool,'balanced-v1',fixtures.REF)
    store.put('combo',combo['id'],combo)
    return store,combo


class PlayedTests(unittest.TestCase):
    def test_mark_is_persistent_reversible_and_separate_from_money_and_paper_grades(self):
        store,combo=populated()
        before=store.get('combo',combo['id'])
        first=env['mark_combo_played'](store,combo['id'],True,'2026-11-11T18:00Z')
        second=env['mark_combo_played'](store,combo['id'],True,'2026-11-11T18:01Z')
        self.assertEqual(first['marked_at'],second['marked_at'])
        self.assertEqual(len(store.items('combo_played')),1)
        self.assertEqual(store.get('combo',combo['id']),before)
        self.assertEqual(store.items('combo_actual'),[])
        self.assertEqual(store.items('combo_grade'),[])
        env['mark_combo_played'](store,combo['id'],False)
        self.assertFalse(store.get('combo_played',combo['id'])['active'])
        with self.assertRaises(ValueError):env['mark_combo_played'](store,'missing')

    def test_played_history_shows_exact_legs_and_undo_without_payout_form(self):
        store,combo=populated()
        env['mark_combo_played'](store,combo['id'])
        app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=60).run()
        app.session_state['_sports_lab_offline_records_v1']=store.records
        app.session_state['sports_lab_page_buttons']='Combos'
        app.session_state['combo_mode']='Played entries'
        app.run()
        self.assertFalse(app.exception,list(app.exception))
        self.assertTrue(any('players · Played' in x.value for x in app.markdown))
        frames=[x.value for x in app.dataframe]
        self.assertTrue(any('Player 0' in frame.get('Player',[]).tolist() for frame in frames if 'Player' in frame))
        self.assertFalse(any(b.label=='Confirm platform return' for b in app.button))
        app.button(key='quick_played_played_'+combo['id']).click().run()
        self.assertFalse(app.exception,list(app.exception))
        self.assertTrue(any('Nothing marked as played yet' in x.value for x in app.info))
