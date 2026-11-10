import copy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock
from the_odds_api import run_probe, sample_rows, MARKETS

NOW=datetime(2026,11,10,3,30,tzinfo=timezone.utc)
EVENT={'id':'abc123','home_team':'Home','away_team':'Away','commence_time':'2026-11-11T01:00:00Z'}
class Store:
    remote=True
    def __init__(self): self.records={}; self.claimed=False
    def get(self,ns,key):return copy.deepcopy(self.records.get((ns,key)))
    def put(self,ns,key,value):self.records[ns,key]=copy.deepcopy(value)
    def query(self,*args,**kwargs):
        if self.claimed:return []
        self.claimed=True;return [('reserved',)]
def response(data,status=200,remaining=500,cost=0):
    return SimpleNamespace(status_code=status,headers={'x-requests-remaining':str(remaining),'x-requests-last':str(cost)},json=lambda:data)
def game():
    return dict(EVENT,bookmakers=[{'key':'prizepicks','markets':[
        {'key':'player_points','last_update':'2026-11-10T03:29Z','outcomes':[
            {'description':'Player A','point':20.5,'name':'Over'},
            {'description':'Player A','point':20.5,'name':'Under'}]},
        {'key':'player_fantasy_points_alternate','outcomes':[
            {'description':'Player A','point':55.5,'name':'Over','price':100}]}]}])
class ProbeTests(TestCase):
    def test_one_event_four_market_cap_and_no_board_changes(self):
        store=Store();get=Mock(side_effect=[response([EVENT,dict(EVENT,id='def456')]),response(game(),remaining=498,cost=2)])
        result=run_probe(store,'secret',NOW,get)
        self.assertEqual(get.call_count,2)
        params=get.call_args.kwargs['params']
        self.assertEqual(set(params['markets'].split(',')),set(MARKETS))
        self.assertEqual(params['bookmakers'],'prizepicks');self.assertEqual(params['regions'],'us_dfs')
        self.assertEqual(result['credits_remaining'],498);self.assertEqual(result['last_request_credits'],2)
        self.assertEqual(result['state'],'sample_received');self.assertFalse(result['board_changed'])
        self.assertTrue(all(ns=='source' for ns,key in store.records))
        self.assertNotIn('secret',str(store.records))
        self.assertEqual(run_probe(store,'secret',NOW,get),result);self.assertEqual(get.call_count,2)
    def test_alternate_not_guessed_and_only_returned_direction(self):
        rows=sample_rows(game())
        self.assertEqual(set(rows[0]['Directions']),{'MORE','LESS'})
        self.assertEqual(rows[1]['Directions'],['MORE'])
        self.assertEqual(rows[1]['Line type'],'Alternate — tier unverified')
    def test_stale_or_started_game_not_used(self):
        get=Mock(return_value=response([dict(EVENT,commence_time='2026-11-10T03:00Z')]))
        result=run_probe(Store(),'secret',NOW,get)
        self.assertEqual(result['state'],'no_upcoming_sample');self.assertEqual(get.call_count,1)
    def test_small_remaining_quota_stops_before_props(self):
        get=Mock(return_value=response([EVENT],remaining=3))
        self.assertEqual(run_probe(Store(),'secret',NOW,get)['state'],'insufficient_quota')
        self.assertEqual(get.call_count,1)
    def test_auth_failure_sanitized_and_not_retried(self):
        get=Mock(return_value=response({'message':'secret'},status=401))
        result=run_probe(Store(),'secret',NOW,get)
        self.assertEqual(result['state'],'unavailable');self.assertNotIn('secret',str(result));self.assertEqual(get.call_count,1)
    def test_empty_book_sample_does_not_claim_coverage(self):
        get=Mock(side_effect=[response([EVENT]),response(dict(EVENT,bookmakers=[]),cost=0)])
        self.assertEqual(run_probe(Store(),'secret',NOW,get)['state'],'no_prizepicks_sample')
    def test_different_event_rejected(self):
        get=Mock(side_effect=[response([EVENT]),response(dict(game(),id='wrong'))])
        result=run_probe(Store(),'secret',NOW,get)
        self.assertEqual(result['state'],'unavailable');self.assertEqual(result['rows'],[])
    def test_concurrent_reserved_probe_cannot_send_request(self):
        store=Store();store.claimed=True;get=Mock()
        self.assertEqual(run_probe(store,'secret',NOW,get)['state'],'already_reserved');get.assert_not_called()
