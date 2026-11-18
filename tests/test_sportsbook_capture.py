import copy
import unittest
from unittest.mock import Mock
import odds_fallback as f
from sportsbook_capture import REQUEST_BOOKS
from market_compare import compare_no_vig
from teammate_context import from_availability
from test_odds_fallback import Store,event,response,NOW


class CaptureTests(unittest.TestCase):
    def game(self):
        game=event()
        for name,prices in (('draftkings',(-120,100)),('fanduel',(110,-130))):
            market=copy.deepcopy(game['bookmakers'][0]['markets'][0])
            for outcome,price in zip(market['outcomes'],prices):outcome['price']=price
            game['bookmakers'].append({'key':name,'markets':[market]})
        return game
    def compare(self,card,rows):
        clean=lambda x:''.join(c for c in str(x).lower() if c.isalnum())
        return compare_no_vig(card,rows,clean,clean,lambda c:[c['Stat Key']],lambda c:None)
    def test_same_requests_same_reservations_and_cached_reuse(self):
        game=self.game();get=Mock(side_effect=[response([game]),response(game)])
        store=Store()
        cards,report=f.download(store,'key',NOW,get,markets=f.BOARD_MARKETS)
        self.assertEqual(get.call_count,2) # existing free event list + existing event odds request
        self.assertEqual(get.call_args.kwargs['params']['bookmakers'],REQUEST_BOOKS)
        self.assertLessEqual(len(REQUEST_BOOKS.split(',')),10)
        self.assertEqual(report['budget']['reserved_today'],len(f.BOARD_MARKETS))
        self.assertTrue(all(c['Platform']=='prizepicks' for c in cards))
        result=self.compare(cards[0],cards[0]['Sportsbook Quotes'])
        self.assertEqual(result['book_count'],2)
        self.assertAlmostEqual(result['more']+result['less'],1)
        self.assertAlmostEqual(result['more'],sum(b['more'] for b in result['books'])/2)
        f.download(store,'key',NOW,get,markets=f.BOARD_MARKETS)
        self.assertEqual(get.call_count,2)
    def test_pairs_never_mix_books_lines_or_snapshots(self):
        card=f.decode(self.game(),NOW.isoformat(),NOW)[0]
        quotes=card['Sportsbook Quotes']
        self.assertIsNone(self.compare(card,[quotes[0],quotes[3]]))
        changed=[dict(q,Line=11.5) for q in quotes]
        self.assertIsNone(self.compare(card,changed))
        changed=[dict(q,**{'Provider fetched at':'2026-11-10T03:00:00Z'}) for q in quotes]
        self.assertIsNone(self.compare(card,changed))
    def test_missing_or_stale_sportsbooks_leave_standard_cards_intact(self):
        game=self.game()
        for book in game['bookmakers'][1:]:book['markets'][0]['last_update']='2026-11-09T01:00:00Z'
        card=f.decode(game,NOW.isoformat(),NOW)[0]
        self.assertNotIn('Sportsbook Quotes',card)
        self.assertEqual(card['Line Type'],'Standard')
    def test_legacy_snapshot_recovers_exact_saved_evidence_without_refetch(self):
        game=self.game();get=Mock(side_effect=[response([game]),response(game)])
        store=Store()
        cards,_=f.download(store,'key',NOW,get,markets=f.BOARD_MARKETS)
        saved=store.query('SELECT key,payload FROM sports_records WHERE namespace=? AND key LIKE ?',
                          ('source','the_odds_api_daily_v2:%'),True)
        import json
        key,payload=saved[0];snapshot=json.loads(payload)
        for card in snapshot['rows']:card.pop('Sportsbook Quotes',None)
        store.put('source',key,snapshot)
        restored,_=f.download(store,'key',NOW,get,markets=f.BOARD_MARKETS)
        self.assertEqual(get.call_count,2)
        self.assertEqual(len(restored[0]['Sportsbook Quotes']),4)
        self.assertEqual(restored[0]['Provider fetched at'],cards[0]['Provider fetched at'])
        # A later game response cannot retrofit new prices into the earlier capture.
        source=store.query('SELECT key,payload FROM sports_records WHERE namespace=? AND key LIKE ?',
                           ('source','the_odds_api_game_v2:%'),True)[0]
        body=json.loads(source[1]);body['fetched_at']='2026-11-10T00:31:00Z';store.put('source',source[0],body)
        for card in snapshot['rows']:card.pop('Sportsbook Quotes',None)
        store.put('source',key,snapshot)
        restored,_=f.download(store,'key',NOW,get,markets=f.BOARD_MARKETS)
        self.assertNotIn('Sportsbook Quotes',restored[0])
    def test_teammate_absence_does_not_invent_minutes_or_return(self):
        report=from_availability({'available':True,'teammates':[
            {'player_id':'1','status':'Out'},{'player_id':'2','status':'Out'},
            {'player_id':'3','status':'Questionable'}]},'1')
        self.assertEqual([p['player_id'] for p in report['out_teammates']],['2'])
        self.assertIsNone(report['high_minute_teammates'])
        self.assertIsNone(report['returning_teammates'])
        self.assertIsNone(report['usage_adjustment'])
        self.assertFalse(from_availability({'available':True},'1')['available'])
