import ast,copy,json,os,sqlite3,tempfile,time,unittest,hashlib,math
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime,timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
import pandas as pd
import numpy as np
TREE=ast.parse(Path(__file__).resolve().parents[1].joinpath('app.py').read_text())
env=dict(json=json,os=os,sqlite3=sqlite3,time=time,contextmanager=contextmanager,math=math,
 datetime=datetime,timezone=timezone,Decimal=Decimal,pd=pd,np=np,hashlib=hashlib,
 market_columns=lambda c:[c['Stat']],fantasy_weights=lambda c:None,SLATE_TIMEZONE='America/Los_Angeles')
names={'now','encode','json_safe','utc_timestamp','identity','market_signature','unique_forecasts','saved_forecast_records'}
nodes=[n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name in names or isinstance(n,ast.ClassDef) and n.name=='Store']
exec(compile(ast.Module(body=nodes,type_ignores=[]),'app.py','exec'),env)
workflow=next(n for n in TREE.body if isinstance(n,ast.ClassDef) and n.name=='Workflow')
research=next(n for n in workflow.body if isinstance(n,ast.FunctionDef) and n.name=='research_rows')
exec(compile(ast.Module(body=[research],type_ignores=[]),'app.py','exec'),env)
class SmoothReadTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.store=env['Store'](str(Path(self.tmp.name)/'test.sqlite'))
  self.card={'Fixture ID':'game','Platform':'prizepicks','Player':'Player One','Stat Key':'points',
   'Period':'result','Line':12.5,'Line Type':'Standard','Game':'Team A vs Team B',
   'Start (UTC)':'2026-11-13T00:10:00Z','Stat':'PTS','unused_provider_evidence':'x'*20000}
  self.frozen={'id':'first','card':self.card,'created_at':'2026-11-12T18:00:00Z','event_id':'123',
   'result':{'direction':'MORE','projection':14.5,'history_evidence':'x'*20000},'snapshot':{'raw':'x'*50000}}
  self.store.put('forecast','first',self.frozen)
  self.store.put('grade','first',{'outcome':'WIN','actual':15})
 def test_results_transfer_only_needed_fields_and_keep_exact_results(self):
  compact=self.store.research_forecast_items()
  self.assertLess(len(json.dumps(compact)),len(json.dumps(self.frozen))/50)
  self.assertEqual(compact[0][1]['result'],{'direction':'MORE','projection':14.5})
  self.assertEqual(self.store.get('forecast','first'),self.frozen)
  actual=env['research_rows'](SimpleNamespace(store=self.store))
  full=SimpleNamespace(items=self.store.items,forecast_items=lambda:self.store.forecast_items())
  expected=env['research_rows'](SimpleNamespace(store=full))
  self.assertEqual(actual,expected)
 def test_research_pagination_does_not_drop_records(self):
  with self.store.connection() as con:
   f=copy.deepcopy(self.frozen);f['snapshot']={};f['result'].pop('history_evidence');f['card'].pop('unused_provider_evidence')
   con.executemany('INSERT INTO sports_records VALUES (?,?,?,?)',
    [('forecast',f'page-{i:04}',json.dumps(dict(f,id=f'page-{i:04}')),'stamp') for i in range(1001)])
  self.assertEqual(len(self.store.research_forecast_items()),1002)
 def test_forecast_insert_invalidates_research_cache(self):
  self.store._read_cache={};self.store.research_forecast_items()
  self.store.put('forecast','second',dict(self.frozen,id='second'))
  self.assertEqual(len(self.store.research_forecast_items()),2)
 def poll(self,stage,verified=True):
  run={'started_at':'start','stage':stage,'finished_at':'finish' if stage=='finished' else None}
  values={('board_run','day'):run,('preparation','last'):{'ready':3,'unavailable':0,'state':'running'}}
  store=SimpleNamespace(get=lambda ns,key:values.get((ns,key)),_read_cache={
   ('items','source',None):'keep',('items','paper_forecast',None):'keep',('get','board','board'):'replace'})
  state={'_ui_polled_board_token':('start','preparing',None),'_ui_polled_progress':(2,0,'running')}
  if verified:state['_ui_verified_board_day']='day'
  st=SimpleNamespace(session_state=state,rerun=Mock())
  node=copy.deepcopy(next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name=='poll_saved_board_updates'));node.decorator_list=[]
  e={'st':st,'workflow':SimpleNamespace(store=store),'slate_day':lambda:'day'}
  exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),e);e['poll_saved_board_updates']()
  return st,store
 def test_background_progress_does_not_interrupt_verified_page(self):
  st,store=self.poll('preparing');st.rerun.assert_not_called()
  self.assertEqual(store._read_cache[('get','board','board')],'replace')
 def test_publication_refreshes_page_without_destroying_unrelated_caches(self):
  st,store=self.poll('finished');st.rerun.assert_called_once()
  self.assertNotIn(('get','board','board'),store._read_cache)
  self.assertEqual(store._read_cache[('items','source',None)],'keep')
  self.assertEqual(store._read_cache[('items','paper_forecast',None)],'keep')
if __name__=='__main__':unittest.main()
