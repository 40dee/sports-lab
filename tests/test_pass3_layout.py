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
  board=sections['Board & Quotas']
  diagnostics=sections['Backups & Diagnostics']
  self.assertTrue(any(b.label=="Prepare today's board" for b in board.button))
  self.assertTrue(any(b.label=='Recheck all completed games' for b in diagnostics.button))
  self.assertTrue(any(b.label=='Prepare full workspace backup' for b in diagnostics.button))
  self.assertFalse(any(b.label=='🔍 SCAN AND DECODE NBA LINES' for b in app.button))
  self.assertNotIn("Today's Board & Manual Lines",sections)
  self.assertNotIn('API Quotas & Data Feeds',sections)
  self.assertNotIn('Optional NBA historical discovery preview',sections)
  self.assertFalse(any('App build:' in c.value for c in app.caption))
if __name__=='__main__':unittest.main()
