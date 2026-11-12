import ast,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
TREE=ast.parse(Path(__file__).resolve().parents[1].joinpath('app.py').read_text())
class NavigationSpeedTests(unittest.TestCase):
 def env(self,ui=True,scan='scan1',checked='2026-11-12T02:50Z'):
  run={'stage':'finished','import_complete':True,'board_scan_at':'scan1','finished_at':'2026-11-12T02:40Z'}
  coverage={'checked_at':checked,'board_scan_at':scan,'ready':10,'imported':10,'upcoming_unavailable':0}
  values={('board_run','day'):run,('board','board'):{'v4_scan_at':'scan1'},('coverage','day'):coverage}
  store=SimpleNamespace(ui_read_projection=ui,get=lambda ns,key:values.get((ns,key)))
  verify=Mock(return_value=coverage)
  env={'combo_clock':lambda _:0,'slate_day':lambda _:'day','utc_timestamp':lambda v:v,
       'pd':SimpleNamespace(isna=lambda v:v is None),'board_coverage':verify}
  node=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name=='combo_board_ready')
  exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),env)
  return env,store,verify
 def test_unchanged_verified_ui_board_avoids_full_forecast_read(self):
  env,store,verify=self.env();self.assertTrue(env['combo_board_ready'](store)[0]);verify.assert_not_called()
 def test_changed_board_or_old_validation_rechecks(self):
  for kwargs in ({'scan':'old'},{'checked':'2026-11-12T02:30Z'}):
   env,store,verify=self.env(**kwargs);env['combo_board_ready'](store);verify.assert_called_once()
 def test_worker_still_performs_full_validation(self):
  env,store,verify=self.env(ui=False);env['combo_board_ready'](store);verify.assert_called_once()
 def test_navigation_has_no_periodic_whole_page_timer(self):
  node=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name=='render_app_pages')
  self.assertTrue(all(not isinstance(d,ast.Call) or not any(k.arg=='run_every' for k in d.keywords) for d in node.decorator_list))
 def test_small_poll_does_not_download_board_or_forecasts(self):
  node=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name=='poll_saved_board_updates')
  calls={n.func.attr for n in ast.walk(node) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)}
  self.assertFalse(calls & {'items','forecast_items','query','put'})
if __name__=='__main__':unittest.main()
