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

if __name__=='__main__':unittest.main()
