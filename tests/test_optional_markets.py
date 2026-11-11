import json
import copy
import unittest
from unittest.mock import Mock
import odds_fallback as f
from test_odds_fallback import Store, response, event, NOW


class OptionalMarketTests(unittest.TestCase):
    def test_absent_market_creates_no_cards_and_removes_only_its_superseded_quotes(self):
        _,_,rows,report=self.capture(event())
        self.assertFalse(any(c['Market ID']=='theodds:player_threes' for c in rows))
        old=dict(rows[0],**{'Market ID':'theodds:player_threes','Stat':'3 Point FG'})
        alternate=dict(old,**{'Market ID':'theodds:player_threes_alternate'})
        another_game=dict(old,**{'Provider Event ID':'different'})
        historical=dict(old,**{'Start (UTC)':'2025-01-01T00:00:00Z'})
        other_platform=dict(old,Platform='draftkings')
        kept=f.prune_unavailable_lines([old,alternate,another_game,historical,other_platform]+rows,report)
        self.assertNotIn(old,kept)
        self.assertNotIn(alternate,kept)
        self.assertIn(another_game,kept)
        self.assertIn(historical,kept)
        self.assertIn(other_platform,kept)
        self.assertTrue(all(c in kept for c in rows))

    def test_unresolved_market_does_not_authorize_removing_saved_quotes(self):
        _,_,rows,report=self.capture(event())
        old=dict(rows[0],**{'Market ID':'theodds:player_threes'})
        report['game_coverage'][0]['market_status']['player_threes']='unusable_returned_market'
        self.assertEqual(f.prune_unavailable_lines([old],report),[old])

    def capture(self,game,markets=None):
        store=Store();get=Mock(side_effect=[response([game]),response(game)])
        rows,report=f.download(store,'key',NOW,get,markets=markets or f.BOARD_MARKETS)
        return store,get,rows,report

    def test_absent_market_is_logged_and_does_not_hold_valid_response(self):
        _,_,rows,report=self.capture(event())
        self.assertTrue(rows)
        self.assertTrue(report['complete'])
        self.assertEqual(report['game_coverage'][0]['market_status']['player_points'],'available')
        self.assertEqual(report['game_coverage'][0]['market_status']['player_threes'],'not_supplied_at_capture')
        self.assertGreater(report['markets_skipped'],0)
        self.assertEqual(report['markets_unresolved'],0)

    def test_explicit_empty_market_can_be_skipped(self):
        game=event();game['bookmakers'][0]['markets'].append({'key':'player_threes','outcomes':[]})
        _,_,_,report=self.capture(game)
        self.assertTrue(report['complete'])
        self.assertEqual(report['game_coverage'][0]['market_status']['player_threes'],'no_outcomes_at_capture')

    def test_returned_but_unusable_market_is_not_misclassified_as_absent(self):
        for bad in ({'key':'player_threes','outcomes':None},
                    {'key':'player_threes','last_update':NOW.isoformat(),'outcomes':[{'point':'bad'}]},
                    {'key':'player_threes','last_update':NOW.isoformat(),'outcomes':[None]},
                    {'key':'player_threes','last_update':'2020-01-01T00:00:00Z','outcomes':[{'name':'Over','description':'Player','point':3.5}]}):
            game=event();game['bookmakers'][0]['markets'].append(bad)
            _,_,_,report=self.capture(game)
            self.assertFalse(report['complete'])
            self.assertEqual(report['game_coverage'][0]['market_status']['player_threes'],'unusable_returned_market')

    def test_missing_prizepicks_bookmaker_cannot_be_treated_as_no_markets_offered(self):
        for books in ([],[{'key':'draftkings','markets':[]}],[{'key':'prizepicks','markets':None}]):
            game=event();game['bookmakers']=books
            _,_,_,report=self.capture(game)
            self.assertFalse(report['complete'])
            self.assertTrue(all(v=='unverified_response' for v in report['game_coverage'][0]['market_status'].values()))

    def test_failed_request_never_becomes_a_skip(self):
        store=Store();game=event()
        bad=response({});bad.status_code=503
        _,report=f.download(store,'key',NOW,Mock(side_effect=[response([game]),bad]),markets=f.BOARD_MARKETS)
        self.assertFalse(report['complete'])
        self.assertEqual(report['game_coverage'],[])
        self.assertEqual(report['markets_skipped'],0)

    def test_unrequested_game_stays_unresolved(self):
        first=event();second=event();second['id']='second'
        store=Store();bad=response({});bad.status_code=503
        _,report=f.download(store,'key',NOW,Mock(side_effect=[response([first,second]),response(first),bad]),markets=f.BOARD_MARKETS)
        self.assertFalse(report['complete'])
        self.assertEqual(len(report['game_coverage']),1)
        self.assertEqual(report['games_available'],2)

    def test_old_cached_partial_is_reclassified_without_redownload_or_retiming(self):
        store,get,rows,report=self.capture(event())
        key,payload=store.query("SELECT key,payload FROM sports_records WHERE namespace='source' AND key LIKE 'the_odds_api_daily_v2:%'",fetch=True)[0]
        snapshot=json.loads(payload)
        snapshot['complete']=snapshot['report']['complete']=False
        snapshot['report'].pop('coverage_version',None)
        for game in snapshot['report']['game_coverage']:game.pop('market_status',None)
        store.put('source',key,snapshot)
        never=Mock(side_effect=AssertionError('Paid retry forbidden'))
        again,updated=f.download(store,'key',NOW,never,markets=f.BOARD_MARKETS)
        never.assert_not_called()
        self.assertEqual(rows,again)
        self.assertTrue(updated['complete'])
        self.assertEqual(updated['http_requests'],0)
        self.assertEqual(updated['checked_at'],report['checked_at'])
        self.assertEqual(updated['credits_remaining'],report['credits_remaining'])

    def test_cache_without_saved_raw_evidence_stays_blocked(self):
        store,_,_,_=self.capture(event())
        store.query("DELETE FROM sports_records WHERE namespace='source' AND key LIKE 'the_odds_api_game_v2:%'")
        never=Mock(side_effect=AssertionError('Paid retry forbidden'))
        _,report=f.download(store,'key',NOW,never,markets=f.BOARD_MARKETS)
        never.assert_not_called()
        self.assertFalse(report['complete'])

