from pathlib import Path
import unittest
from streamlit.testing.v1 import AppTest

class NavigationTests(unittest.TestCase):
    def test_all_pages_render_without_provider_credentials(self):
        app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=60).run()
        self.assertFalse(app.exception,list(app.exception))
        for page in ('Plays','Fantasy','Combos','Saved','Results','Settings'):
            with self.subTest(page=page):
                app.session_state['sports_lab_page_buttons']=page
                app.run()
                self.assertFalse(app.exception,list(app.exception))

    def test_stale_saved_forecasts_are_visible_but_not_entry_ready(self):
        import pandas as pd
        import test_combo_regression as fixtures
        store=fixtures.populated(3)
        current=pd.Timestamp.now(tz='UTC')
        start=current.tz_convert('America/Los_Angeles').normalize()+pd.DateOffset(days=1)-pd.Timedelta(microseconds=1)
        board=store.get('board','board');health={}
        for card in board['v4_rows']:
            key=fixtures.env['identity'](card)
            frozen=store.get('forecast',key)
            card['Start (UTC)']=start.tz_convert('UTC').isoformat()
            card['Period']='result'
            key=fixtures.env['identity'](card)
            card.pop('Odds checked (UTC)',None)
            frozen.update(id=key,card=dict(card),created_at=(current-pd.Timedelta(hours=1)).isoformat())
            store.put('forecast',key,frozen)
            health[fixtures.env['player_health_key'](card)]={'player_id':frozen['result']['player_id'],
                'team_id':frozen['result']['player_id'],'checked_at':current.isoformat(),
                'injuries_available':True,'blocked':False,'statuses':[]}
        day=fixtures.env['slate_day'](current)
        store.put('board','board',board)
        store.put('health',day,{'players':health,'checked_at':current.isoformat(),
            'roster_matched':3,'injury_checked':3,'blocked':0})
        app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=60).run()
        app.session_state['_sports_lab_offline_records_v1']=store.records
        app.session_state['v4_rows']=board['v4_rows']
        app.session_state['sports_lab_page_buttons']='Plays'
        app.run()
        self.assertFalse(app.exception,list(app.exception))
        self.assertTrue(any('Showing saved pregame projections' in item.value for item in app.warning))
        self.assertTrue(any('Player 0' in item.value and 'VERIFY LINE' in item.value for item in app.markdown))
        save_buttons=[button for button in app.button if button.label=='☆ Save']
        self.assertEqual(len(save_buttons),3)
        self.assertTrue(all(button.disabled for button in save_buttons))
        self.assertFalse(any('No matching upcoming picks' in item.value for item in app.info))
        # Changing view must also keep these saved research cards visible.
        app.session_state['mobile_view_buttons']='All props'
        app.run()
        self.assertFalse(app.exception,list(app.exception))
        self.assertTrue(any('Player 0' in item.value for item in app.markdown))

    def test_stale_upcoming_fantasy_remains_visible_for_review(self):
        import pandas as pd
        import test_combo_regression as fixtures
        store=fixtures.populated(2)
        current=pd.Timestamp.now(tz='UTC')
        start=current.tz_convert('America/Los_Angeles').normalize()+pd.DateOffset(days=1)-pd.Timedelta(microseconds=1)
        board=store.get('board','board')
        for card in board['v4_rows']:
            frozen=store.get('forecast',fixtures.env['identity'](card))
            card.update({'Start (UTC)':start.tz_convert('UTC').isoformat(),
                         'Period':'result','Stat':'Fantasy Score','Stat Key':'fantasy_score'})
            card.pop('Odds checked (UTC)',None)
            key=fixtures.env['identity'](card)
            frozen.update(id=key,card=dict(card),created_at=(current-pd.Timedelta(hours=1)).isoformat())
            store.put('forecast',key,frozen)
        store.put('board','board',board)
        app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=60).run()
        app.session_state['_sports_lab_offline_records_v1']=store.records
        app.session_state['v4_rows']=board['v4_rows']
        app.session_state['sports_lab_page_buttons']='Fantasy'
        app.run()
        self.assertFalse(app.exception,list(app.exception))
        self.assertTrue(any('Showing saved pregame projections for review' in item.value for item in app.warning))
        cards=[item.value for item in app.markdown if 'fc-card' in item.value and 'Player 0' in item.value]
        self.assertTrue(cards)
        self.assertTrue(any('REVIEW' in card and 'Saved model direction: MORE' in card for card in cards))
        self.assertFalse(any('No selectable upcoming Fantasy plays' in item.value for item in app.info))

if __name__=='__main__':unittest.main()
