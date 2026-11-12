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
  self.run_check([dict(self.card,**{'Start (UTC)':(NOW-timedelta(minutes=5)).isoformat()})])
  morning=NOW.replace(hour=18,minute=0)
  f.refresh_upcoming_quotes(self.store,self.key,[dict(self.card,**{'Start (UTC)':(morning+timedelta(hours=3)).isoformat()})],morning,self.get)
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
 def test_preparation_starts_100_minutes_ahead_then_rechecks_before_tipoff(self):
  self.card['Start (UTC)']=(NOW+timedelta(minutes=100)).isoformat()
  self.body['commence_time']=self.card['Start (UTC)']
  for offset in (0,60,90):
   current=NOW+timedelta(minutes=offset)
   self.body['bookmakers'][0]['markets'][0]['last_update']=current.isoformat()
   rows,report=f.refresh_upcoming_quotes(self.store,self.key,[self.card],current,self.get)
   self.assertEqual(report['events_updated'],['abc123'])
   self.assertEqual(len(rows),1)
   f.refresh_upcoming_quotes(self.store,self.key,[self.card],current,self.get)
  self.assertEqual(self.get.call_count,3)
  self.assertEqual(len(self.store.query("SELECT key FROM sports_records WHERE namespace='quote_refresh' AND key NOT LIKE '%:latest'",fetch=True)),3)
 def test_rechecks_before_thirty_minute_expiration(self):
  self.card['Start (UTC)']=(NOW+timedelta(minutes=85)).isoformat()
  self.body['commence_time']=self.card['Start (UTC)']
  for offset in (0,10,20,30,40,50,60):
   current=NOW+timedelta(minutes=offset)
   self.body['bookmakers'][0]['markets'][0]['last_update']=current.isoformat()
   f.refresh_upcoming_quotes(self.store,self.key,[self.card],current,self.get)
  self.assertEqual(self.get.call_count,4)
 def test_fresh_early_event_does_not_block_later_games(self):
  cards=[dict(self.card,**{'Odds checked (UTC)':NOW.isoformat()})]
  bodies={}
  for i in range(4):
   event='later'+str(i)
   card=dict(self.card,**{'Provider Event ID':event,'Start (UTC)':(NOW+timedelta(minutes=35+i*10)).isoformat()})
   cards.append(card)
   body=json.loads(json.dumps(self.body))
   body.update(id=event,commence_time=card['Start (UTC)'])
   bodies[event]=body
  self.get.side_effect=lambda url,**kw:SimpleNamespace(status_code=200,headers={'x-requests-remaining':'280'},json=lambda:bodies[url.split('/')[-2]])
  rows,report=self.run_check(cards)
  self.assertEqual(self.get.call_count,4)
  self.assertEqual(set(report['events_updated']),set(bodies))
 def test_1430_sweep_includes_all_late_games_once_without_four_game_cutoff(self):
  current=datetime(2026,11,12,22,30,tzinfo=timezone.utc)
  cards=[];bodies={}
  for i in range(6):
   event='sweep'+str(i)
   card=dict(self.card,**{'Provider Event ID':event,'Start (UTC)':(current+timedelta(minutes=100+i*45)).isoformat(),
                          'Odds checked (UTC)':(current-timedelta(hours=4)).isoformat()})
   cards.append(card)
   body=json.loads(json.dumps(self.body));body.update(id=event,commence_time=card['Start (UTC)'])
   body['bookmakers'][0]['markets'][0]['last_update']=current.isoformat();bodies[event]=body
  self.get.side_effect=lambda url,**kw:SimpleNamespace(status_code=200,headers={'x-requests-remaining':'260'},json=lambda:bodies[url.split('/')[-2]])
  rows,report=f.refresh_upcoming_quotes(self.store,self.key,cards,current,self.get)
  self.assertEqual(set(report['events_updated']),set(bodies))
  self.assertEqual(self.get.call_count,6)
  f.refresh_upcoming_quotes(self.store,self.key,cards,current+timedelta(minutes=10),self.get)
  self.assertEqual(self.get.call_count,6)
 def test_fresh_quote_satisfies_afternoon_sweep_without_new_request(self):
  current=datetime(2026,11,12,22,30,tzinfo=timezone.utc)
  card=dict(self.card,**{'Start (UTC)':(current+timedelta(hours=5)).isoformat(),'Odds checked (UTC)':(current-timedelta(minutes=10)).isoformat()})
  f.refresh_upcoming_quotes(self.store,self.key,[card],current,self.get)
  f.refresh_upcoming_quotes(self.store,self.key,[card],current+timedelta(minutes=30),self.get)
  self.get.assert_not_called()
 def test_does_not_start_partial_afternoon_sweep_at_1415(self):
  current=datetime(2026,11,12,22,15,tzinfo=timezone.utc)
  card=dict(self.card,**{'Start (UTC)':(current+timedelta(minutes=115)).isoformat()})
  f.refresh_upcoming_quotes(self.store,self.key,[card],current,self.get)
  self.get.assert_not_called()
if __name__=='__main__':unittest.main()

