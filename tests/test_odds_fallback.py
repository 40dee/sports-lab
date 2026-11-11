import ast
import json
from pathlib import Path
import sqlite3
from datetime import datetime,timezone
from unittest import TestCase
from unittest.mock import Mock
from types import SimpleNamespace
import odds_fallback as f

NOW=datetime(2026,11,10,0,30,tzinfo=timezone.utc)
class Store:
    remote=True
    def __init__(self):
        self.db=sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE sports_records(namespace TEXT,key TEXT,payload TEXT,updated TEXT,PRIMARY KEY(namespace,key))')
    def query(self,sql,values=(),fetch=False):
        cur=self.db.execute(sql,values);rows=cur.fetchall() if fetch else [];self.db.commit();return rows
    def get(self,ns,key,default=None):
        row=self.db.execute('SELECT payload FROM sports_records WHERE namespace=? AND key=?',(ns,key)).fetchone()
        return json.loads(row[0]) if row else default
    def put(self,ns,key,value):
        self.db.execute('INSERT INTO sports_records VALUES(?,?,?,?) ON CONFLICT(namespace,key) DO UPDATE SET payload=excluded.payload',(ns,key,json.dumps(value),NOW.isoformat()));self.db.commit()
def response(body,remaining=498):return SimpleNamespace(status_code=200,headers={'x-requests-remaining':str(remaining)},json=lambda:body)
def event():
    return {'id':'abc123','commence_time':'2026-11-10T02:00:00Z','home_team':'Home','away_team':'Away','bookmakers':[
        {'key':'prizepicks','markets':[{'key':'player_points','last_update':NOW.isoformat(),'outcomes':[
            {'description':'Player','point':10.5,'name':'Over'},{'description':'Player','point':10.5,'name':'Under'}]}]}]}
class FallbackTests(TestCase):
    def test_only_monthly_exhaustion_switches(self):
        self.assertTrue(f.primary_exhausted({'used':250},{'remaining':0}))
        self.assertFalse(f.primary_exhausted({'used':200},{'remaining':50,'daily_cap':0}))
        self.assertFalse(f.primary_exhausted(None,{'remaining':0}))
    def test_atomic_daily_and_monthly_limits(self):
        store=Store()
        for i in range(3):report=f.reserve(store,'key',NOW,498)
        self.assertEqual(report['reserved_today'],18)
        with self.assertRaisesRegex(ValueError,'allowance'):f.reserve(store,'key',NOW,498)
        self.assertLessEqual(report['reserved_month'],500)
    def test_insufficient_remaining_blocks_before_call(self):
        with self.assertRaisesRegex(ValueError,'allowance'):f.reserve(Store(),'key',NOW,1)
    def test_decode_does_not_infer_sides_and_ignores_other_dates_stale_markets(self):
        game=event();rows=f.decode(game,NOW.isoformat(),NOW)
        self.assertEqual(rows[0]['Available Sides'],['MORE','LESS']);self.assertEqual(rows[0]['Line Type'],'Standard')
        game['bookmakers'][0]['markets'][0]['outcomes'].pop()
        self.assertEqual(f.decode(game,NOW.isoformat(),NOW)[0]['Available Sides'],['MORE'])
        game['commence_time']='2026-11-11T02:00:00Z';self.assertEqual(f.decode(game,NOW.isoformat(),NOW),[])
        game=event();game['bookmakers'][0]['markets'][0]['last_update']='2026-11-09T22:00:00Z'
        self.assertEqual(f.decode(game,NOW.isoformat(),NOW),[])
    def test_cache_hits_make_zero_additional_requests(self):
        store=Store();game=event();get=Mock(side_effect=[response([game]),response(game,492)])
        rows,report=f.download(store,'key',NOW,get)
        self.assertEqual(len(rows),1);self.assertEqual(report['games_requested'],1)
        self.assertEqual(set(get.call_args.kwargs['params']['markets'].split(',')),set(f.FANTASY_MARKETS))
        rows,report=f.download(store,'key',NOW,get)
        self.assertEqual(report['http_requests'],0);self.assertEqual(get.call_count,2)
        self.assertNotIn('apiKey',str(store.query('SELECT payload FROM sports_records',fetch=True)))
    def test_no_background_key_is_visible_without_calls(self):
        get=Mock();rows,report=f.download(Store(),'',NOW,get)
        get.assert_not_called();self.assertFalse(report['configured']);self.assertEqual(rows,[])
    def test_wrapper_keeps_primary_until_exhausted(self):
        source=Path(__file__).resolve().parents[1].joinpath('app.py').read_text()
        node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='automatic_board_run_with_fallback')
        primary=Mock(return_value={'errors':[]});fallback=Mock(return_value={'fallback':True})
        check=Mock(return_value=False)
        env={'primary_odds_quota_exhausted':check,'automatic_board_run':primary,'run_quota_fallback':fallback}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),env)
        wf=SimpleNamespace(store=Store())
        self.assertEqual(env['automatic_board_run_with_fallback'](wf)['active_odds_provider'],'OddsPAPI');fallback.assert_not_called()
        check.return_value=True
        self.assertEqual(env['automatic_board_run_with_fallback'](wf),{'fallback':True});self.assertEqual(primary.call_count,1)

    def test_fantasy_requests_cover_all_six_games(self):
        games=[]
        for i in range(6):
            game=event();game['id']='abc'+str(i)
            game['bookmakers'][0]['markets'][0]['key']='player_fantasy_points'
            games.append(game)
        get=Mock(side_effect=[response(games)]+[response(game,498-i) for i,game in enumerate(games)])
        rows,report=f.download(Store(),'key',NOW,get)
        self.assertTrue(report['complete'])
        self.assertEqual(report['games_requested'],6)
        self.assertEqual(report['fantasy_lines'],6)
        self.assertEqual(len(report['game_coverage']),6)
        for call in get.call_args_list[1:]:
            self.assertEqual(set(call.kwargs['params']['markets'].split(',')),set(f.FANTASY_MARKETS))

    def test_tier_labels_use_provider_prices_and_preserve_sides(self):
        game=event()
        regular=game['bookmakers'][0]['markets'][0]
        regular['key']='player_fantasy_points'
        for outcome in regular['outcomes']:outcome['price']=-137
        alternate={'key':'player_fantasy_points_alternate','last_update':NOW.isoformat(),'outcomes':[
            {'description':'Player','point':50,'name':'Over','price':100},
            {'description':'Player','point':37,'name':'Over','price':-137},
            {'description':'Player','point':99,'name':'Over','price':-137},
            {'description':'Player','point':5,'name':'Under','price':100},
            {'description':'Player','point':20,'name':'Over','price':-200},
            {'description':'Player','point':25,'name':'Over'}]}
        game['bookmakers'][0]['markets'].append(alternate)
        rows=f.decode(game,NOW.isoformat(),NOW)
        self.assertEqual([r['Line Type'] for r in rows],['Standard','Demon','Goblin','Goblin','Demon'])
        self.assertEqual(rows[-1]['Available Sides'],['LESS'])
        self.assertTrue(all(r['Odds'] is None and r['Payout Multiplier'] is None for r in rows))
        game['bookmakers'][0]['markets'].remove(regular)
        rows=f.decode(game,NOW.isoformat(),NOW)
        self.assertTrue(all(r['Line Type']=='Demon' for r in rows))

    def test_all_regular_prop_tiers_and_request_budget(self):
        game=event();book=game['bookmakers'][0];book['markets']=[]
        for base in f.MARKETS:
            book['markets'].append({'key':base,'last_update':NOW.isoformat(),'outcomes':[
                {'description':'Player','point':20,'name':'Over','price':-137}]})
            book['markets'].append({'key':base+'_alternate','last_update':NOW.isoformat(),'outcomes':[
                {'description':'Player','point':25,'name':'Over','price':100},
                {'description':'Player','point':15,'name':'Over','price':-137}]})
        store=Store();get=Mock(side_effect=[response([game]),response(game,488)])
        rows,report=f.download(store,'key',NOW,get,markets=f.BOARD_MARKETS)
        self.assertEqual(set(get.call_args.kwargs['params']['markets'].split(',')),set(f.BOARD_MARKETS))
        self.assertEqual(report['budget']['reserved_today'],len(f.BOARD_MARKETS))
        self.assertTrue(report['complete'])
        for name,key in f.MARKETS.values():
            self.assertEqual({r['Line Type'] for r in rows if r['Stat Key']==key},{'Standard','Goblin','Demon'})
        again,report=f.download(store,'key',NOW,get,markets=f.BOARD_MARKETS)
        self.assertEqual(get.call_count,2)
        self.assertEqual(again,rows)
        self.assertEqual(report['http_requests'],0)

    def test_partial_capture_is_reused_after_hours_without_paid_retry(self):
        game=event();game['bookmakers']=[]
        store=Store();get=Mock(side_effect=[response([game]),response(game)])
        rows,report=f.download(store,'key',NOW,get)
        self.assertFalse(report['complete'])
        self.assertEqual(get.call_count,2)
        later=NOW.replace(hour=1,minute=30)
        rows,report=f.download(store,'key',later,get)
        self.assertTrue(report['reused_snapshot'])
        self.assertEqual(report['http_requests'],0)
        self.assertEqual(get.call_count,2)
        self.assertEqual(report['checked_at'],NOW.isoformat())

    def test_reserved_capture_makes_no_provider_requests(self):
        import hashlib
        store=Store();key='key'
        fingerprint=hashlib.sha256(key.encode()).hexdigest()[:20]
        snapshot='the_odds_api_daily_v2:'+fingerprint+':2026-11-09'
        store.put('daily_board_capture',snapshot,{})
        get=Mock(side_effect=AssertionError('No network'))
        rows,report=f.download(store,key,NOW,get)
        self.assertEqual(report['state'],'capture_already_started')
        get.assert_not_called()

    def test_next_pacific_date_gets_its_own_capture_and_preserves_previous(self):
        store=Store();game=event()
        get=Mock(side_effect=[response([game]),response(game),response([])])
        f.download(store,'key',NOW,get)
        next_day=NOW.replace(day=11)
        f.download(store,'key',next_day,get)
        self.assertEqual(get.call_count,3)
        captures=store.query("SELECT key FROM sports_records WHERE namespace='source' AND key LIKE 'the_odds_api_daily_v2:%'",fetch=True)
        self.assertEqual(len(captures),2)

    def test_daily_slate_allowance_does_not_bypass_monthly_limit(self):
        store=Store()
        r=f.reserve(store,'key',NOW,500,cost=120,daily_allowance=120)
        self.assertEqual(r['reserved_today'],120)
        with self.assertRaises(ValueError):f.reserve(store,'key',NOW,380,cost=12,daily_allowance=120)
        with self.assertRaises(ValueError):f.reserve(Store(),'other',NOW,5,cost=12,daily_allowance=180)
