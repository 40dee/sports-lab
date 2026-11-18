import unittest
import pandas as pd
from recovered_market_backtest import recovered_market_report
from sportsbook_capture import credit_ceiling,REQUEST_BOOKS
import test_sportsbook_capture as capture_fixtures
from test_odds_fallback import Store,NOW


class RecoveredTests(unittest.TestCase):
    def setup_data(self):
        game=capture_fixtures.CaptureTests().game()
        store=Store();store.put('source','the_odds_api_game_v2:fixture',{'event':game,'fetched_at':NOW.isoformat()})
        card={'Player':'Player','Game':'Home vs Away','Start (UTC)':game['commence_time'],
              'Line':10.5,'Period':'result','Stat Key':'points'}
        frame=pd.DataFrame([dict(card=card,created='2026-11-10T00:31:00Z',v1_dir='MORE',v2_dir='LESS',v1_win=1,v2_win=0)])
        clean=lambda x:''.join(c for c in str(x).lower() if c.isalnum())
        env={'normalize_player':clean,'engine_team_key':clean,'market_columns':lambda c:[c['Stat Key']],
             'fantasy_weights':lambda c:None}
        return store,frame,env
    def test_exact_pregame_quote_buckets_and_alignment(self):
        store,frame,env=self.setup_data()
        report=recovered_market_report(store,frame,env)
        self.assertEqual(report['matched_standard_picks'],1)
        self.assertEqual(report['odds_api_calls'],0)
        agree=next(r for r in report['by_alignment'] if r['version']=='v1' and r['alignment']=='Agrees')
        disagree=next(r for r in report['by_alignment'] if r['version']=='v2' and r['alignment']=='Disagrees')
        self.assertEqual((agree['picks'],agree['hit_rate']),(1,1))
        self.assertEqual((disagree['picks'],disagree['hit_rate']),(1,0))
    def test_later_and_stale_quotes_never_enter_pregame_backtest(self):
        store,frame,env=self.setup_data()
        frame.loc[0,'created']='2026-11-10T00:29:00Z'
        report=recovered_market_report(store,frame,env)
        self.assertEqual(report['matched_standard_picks'],0)
        self.assertEqual(report['excluded'],{'later_than_forecast':1})
        self.assertEqual(report['later_pregame_filter']['matched_standard_picks'],1)
        source=store.get('source','the_odds_api_game_v2:fixture')
        for outcome in source['event']['bookmakers'][0]['markets'][0]['outcomes']:outcome['point']=11.5
        store.put('source','the_odds_api_game_v2:fixture',source)
        report=recovered_market_report(store,frame,env)
        self.assertEqual(report['later_pregame_filter']['matched_standard_picks'],0)
        self.assertEqual(report['later_pregame_filter']['excluded'],{'no_simultaneous_standard_line':1})
        frame.loc[0,'created']='2026-11-10T01:01:00Z'
        report=recovered_market_report(store,frame,env)
        self.assertEqual(report['excluded'],{'stale_at_forecast':1})
        self.assertEqual(report['later_pregame_filter']['matched_standard_picks'],0)
        # A capture after tipoff is invalid for either decision policy.
        source=store.get('source','the_odds_api_game_v2:fixture')
        source['fetched_at']='2026-11-10T03:00:00Z'
        for book in source['event']['bookmakers']:
            for market in book['markets']:market['last_update']=source['fetched_at']
        store.put('source','the_odds_api_game_v2:fixture',source)
        report=recovered_market_report(store,frame,env)
        self.assertEqual(report['matched_standard_picks'],0)
        self.assertEqual(report['later_pregame_filter']['matched_standard_picks'],0)
    def test_pinnacle_stays_in_same_billing_group(self):
        markets=tuple('market'+str(i) for i in range(12))
        self.assertEqual(credit_ceiling(markets),12)
        self.assertEqual(credit_ceiling(markets,REQUEST_BOOKS.replace(',pinnacle','')),12)
        self.assertEqual(credit_ceiling(markets,','.join('b'+str(i) for i in range(11))),24)
