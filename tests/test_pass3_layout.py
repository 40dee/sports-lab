import unittest
from pathlib import Path
from streamlit.testing.v1 import AppTest
SOURCE=Path(__file__).resolve().parents[1]/'app.py'
class Pass3LayoutTests(unittest.TestCase):
 def test_settings_sections_keep_actions_and_hide_developer_noise(self):
  app=AppTest.from_file(str(SOURCE),default_timeout=60).run()
  app.session_state['sports_lab_page_buttons']='Settings';app.run()
  self.assertFalse(app.exception,list(app.exception))
  sections={e.label:e for e in app.expander}
  board=sections["Today's Board & Manual Lines"]
  feeds=sections['API Quotas & Data Feeds']
  diagnostics=sections['Backups, v2 Backtest & Diagnostics']
  self.assertTrue(any(b.label=="Prepare today's board" for b in board.button))
  self.assertTrue(any(e.label=='Enter current PrizePicks lines — no odds requests' for e in board.expander))
  self.assertTrue(any(b.label=='Check account usage — no quota cost' for b in feeds.button))
  self.assertTrue(any(b.label=='Recheck all completed games' for b in diagnostics.button))
  self.assertTrue(any(b.label=='Prepare full workspace backup' for b in diagnostics.button))
  self.assertNotIn('Optional NBA historical discovery preview',sections)
  self.assertFalse(any('App build:' in c.value for c in app.caption))
if __name__=='__main__':unittest.main()
