"""Read-only historical sportsbook alignment. Never retrofit later quotes into forecasts."""
import json
from collections import Counter
from datetime import datetime
from market_compare import compare_no_vig
from sportsbook_capture import capture_quotes
from odds_fallback import MARKETS,decode


def stamp(value):
    try:
        value=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return value if value.tzinfo else None
    except (ValueError,TypeError):return None


def saved_draftkings(store):
    if callable(getattr(store,'query',None)):
        records=store.query('SELECT payload FROM sports_records WHERE namespace=? AND key LIKE ?',
                            ('source','the_odds_api_game_v2:%'),True)
        records=[json.loads(row[0]) for row in records]
    else:
        records=[value for key,value in store.items('source') if key.startswith('the_odds_api_game_v2:')]
    captured=[]
    for saved in records:
        body=saved.get('event') or {};when=stamp(saved.get('fetched_at'))
        if not when or not isinstance(body,dict) or not all(body.get(k) for k in ('id','home_team','away_team','commence_time')):continue
        quotes=[q for q in capture_quotes(body,saved['fetched_at'],when,MARKETS) if q['Platform']=='draftkings']
        if quotes:captured.append((when,quotes,str(body.get('id')),decode(body,saved['fetched_at'],when)))
    return captured


def recovered_market_report(store, frame, env, later_pregame=False, captures=None):
    captures=saved_draftkings(store) if captures is None else captures
    teams=lambda game:tuple(sorted(env["engine_team_key"](name) for name in str(game).split(" vs ")))
    def player(value):
        value=str(value or '')
        if ',' in value:
            last,first=value.split(',',1);value=first+' '+last
        return env['normalize_player'](value)
    def simultaneous_standard(row,cards):
        return any(str(card.get('Line Type','')).lower() in ('standard','regular')
                   and float(card['Line'])==float(row.card['Line'])
                   and player(card.get('Player'))==player(row.card.get('Player'))
                   and sorted(env['market_columns'](card) or [])==sorted(env['market_columns'](row.card) or [])
                   and all(row[version+'_dir'] in card.get('Available Sides',[]) for version in ('v1','v2'))
                   for card in cards)
    rows=[];unmatched=Counter();games=set()
    for _,row in frame.iterrows():
        created=stamp(row.created);start=stamp(row.card.get('Start (UTC)'))
        matches={};later_match=False;old_match=False;missing_standard=False
        for when,quotes,event,pp_cards in captures:
            quote_start=stamp(quotes[0].get("Start (UTC)"))
            if not start or not quote_start or abs((quote_start-start).total_seconds())>900 or teams(quotes[0].get("Game"))!=teams(row.card.get("Game")):continue
            comparison=compare_no_vig(row.card,quotes,env['normalize_player'],env['engine_team_key'],
                                      env['market_columns'],env['fantasy_weights'])
            if not comparison:continue
            observed=stamp(comparison.get('observed_at'))
            decision=max(created,when) if created and later_pregame else created
            if not decision or not start or when>=start or decision>=start or not observed or observed>decision or (not later_pregame and when>created):
                later_match=True;continue
            if (decision-observed).total_seconds()>1800:
                old_match=True;continue
            if when>created and not simultaneous_standard(row,pp_cards):
                missing_standard=True;continue
            pair=(comparison["over_price"],comparison["under_price"])
            if pair not in matches or when>matches[pair][2]:matches[pair]=(comparison,event,when)
        if matches:
            latest=max(value[2] for value in matches.values())
            matches={key:value for key,value in matches.items() if value[2]==latest}
        if len(matches)!=1:
            reason=('conflicting_saved_pairs' if len(matches)>1 else 'later_than_forecast' if later_match
                    else 'stale_at_forecast' if old_match else 'no_simultaneous_standard_line' if missing_standard else 'no_exact_saved_pair')
            unmatched[reason]+=1;continue
        market,event,when=next(iter(matches.values()));games.add(event)
        for version in ('v1','v2'):
            direction=row[version+'_dir'];p=market['more'] if direction=='MORE' else market['less']
            rows.append({'version':version,'probability':p,'win':int(row[version+'_win']),
                         'alignment':'Agrees' if p>.5+1e-9 else 'Disagrees' if p<.5-1e-9 else 'Neutral'})
    def summarize(selected,**labels):
        return dict(labels,picks=len(selected),wins=sum(r['win'] for r in selected),
                    hit_rate=sum(r['win'] for r in selected)/len(selected) if selected else None)
    buckets=[];alignment=[]
    for version in ('v1','v2'):
        data=[r for r in rows if r['version']==version]
        for label,select in (('<52%',lambda p:p<.52),('52–<54.25%',lambda p:.52<=p<.5425),
                             ('≥54.25%',lambda p:p>=.5425),('≥55.5% (subset)',lambda p:p>=.555)):
            buckets.append(summarize([r for r in data if select(r['probability'])],version=version,bucket=label))
        for label in ('Agrees','Disagrees','Neutral'):
            alignment.append(summarize([r for r in data if r['alignment']==label],version=version,alignment=label))
    report={'standard_replay_picks':len(frame),'saved_dk_responses_with_usable_quotes':len(captures),
            'unique_cached_dk_games':len({event for _,_,event,_ in captures}),
            'matched_standard_picks':len(rows)//2,'matched_games':len(games),'excluded':dict(unmatched),
            'by_dk_probability':buckets,'by_alignment':alignment,'odds_api_calls':0,
            'decision_policy':'At the later of model freeze and quote capture, both before tipoff; a later capture must also contain the matching offered PrizePicks Standard line' if later_pregame else 'At the original forecast creation time',
            'method':'Exact teams/start/player/stats/period/line; both sides from one capture; both model and quote available before the decision and tipoff; maximum quote age 30 minutes.',
            'limitation':'A sportsbook-probability filter test, not PrizePicks entry EV or ROI. Entry payouts and correlation are not supplied. ≥55.5% is a subset of ≥54.25%.'}
    if not later_pregame:
        report['later_pregame_filter']=recovered_market_report(store,frame,env,True,captures)
    return report
