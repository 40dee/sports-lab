import json,sqlite3,unittest,hashlib
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from unittest.mock import Mock
import odds_fallback as f
NOW=datetime(2026,11,12,2,40,tzinfo=timezone.utc)
class Store:
 remote=True
 def __init__(self):
  self.db=sqlite3.connect(':memory:');self.db.execute('CREATE TABLE sports_records(namespace TEXT,key TEXT,payload TEXT,updated TEXT,PRIMARY KEY(namespace,key))')
 def query(self,sql,values=(),fetch=False):
  cur=self.db.execute(sql,values);out=cur.fetchall() if fetch else [];self.db.commit();return out
 def get(self,ns,key,default=None):
  row=self.db.execute('SELECT payload FROM sports_records WHERE namespace=? AND key=?',(ns,key)).fetchone()
  return json.loads(row[0]) if row else default
 def put(self,ns,key,value):
  self.query('INSERT INTO sports_records VALUES(?,?,?,?) ON CONFLICT(namespace,key) DO UPDATE SET payload=excluded.payload',(ns,key,json.dumps(value),NOW.isoformat()))
class TargetedQuoteTests(unittest.TestCase):
 def setUp(self):
  self.store=Store();self.key='test-key';self.fingerprint=hashlib.sha256(self.key.encode()).hexdigest()[:20]
  self.store.put('source','the_odds_api_quota:'+self.fingerprint,{'credits_remaining':340})
  self.card={'Line Source':'The Odds API','Provider Event ID':'abc123','Game':'A vs B','Start (UTC)':(NOW+timedelta(minutes=30)).isoformat(),'Odds checked (UTC)':(NOW-timedelta(hours=4)).isoformat()}
  self.body={'id':'abc123','home_team':'A','away_team':'B','commence_time':self.card['Start (UTC)'],'bookmakers':[{'key':'prizepicks','markets':[{'key':'player_points','last_update':NOW.isoformat(),'outcomes':[{'name':'Over','description':'Player','point':20.5},{'name':'Under','description':'Player','point':20.5}]}]}]}
  self.get=Mock(return_value=SimpleNamespace(status_code=200,headers={'x-requests-remaining':'328'},json=lambda:self.body))
 def run_check(self,cards=None):return f.refresh_upcoming_quotes(self.store,self.key,cards or [self.card],NOW,self.get)
 def test_stale_near_tipoff_refreshed_once_preserving_old_card(self):
  old=dict(self.card);rows,r=self.run_check()
  self.assertEqual(r['events_updated'],['abc123']);self.assertEqual(len(rows),1)
  self.assertEqual(rows[0]['Odds checked (UTC)'],NOW.isoformat());self.assertEqual(self.card,old)
  self.run_check();self.assertEqual(self.get.call_count,1)
 def test_fresh_started_and_far_games_do_not_spend(self):
  for delta in (timedelta(minutes=-5),timedelta(hours=2)):
   self.run_check([dict(self.card,**{'Start (UTC)':(NOW+delta).isoformat()})])
  self.run_check([dict(self.card,**{'Odds checked (UTC)':NOW.isoformat()})]);self.get.assert_not_called()
 def test_exhausted_balance_blocks_before_request(self):
  self.store.put('source','the_odds_api_quota:'+self.fingerprint,{'credits_remaining':0})
  rows,r=self.run_check();self.assertEqual(rows,[]);self.get.assert_not_called();self.assertTrue(r['errors'])
 def test_event_mismatch_is_rejected(self):
  self.body['away_team']='Wrong';rows,r=self.run_check()
  self.assertEqual(rows,[]);self.assertEqual(r['events_updated'],[])
 def test_stale_provider_quote_is_not_rejuvenated(self):
  self.body['bookmakers'][0]['markets'][0]['last_update']=(NOW-timedelta(hours=4)).isoformat()
  rows,r=self.run_check();self.assertEqual(rows,[]);self.assertTrue(r['errors'])
if __name__=='__main__':unittest.main()
