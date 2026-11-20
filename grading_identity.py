"""Conservative same-game identity recovery; missing players never become zeroes."""
import re
import unicodedata


def name_key(value):
    value=str(value or '').strip()
    if ',' in value:
        last,first=value.split(',',1)
        value=first.strip()+' '+last.strip()
    value=unicodedata.normalize('NFKD',value).encode('ascii','ignore').decode().lower()
    return re.sub('[^a-z0-9]','',value)


def lookup_actual(summary, card, result, event_id, columns, extract):
    original=str(result.get('player_id') or '')
    groups=summary.get('boxscore',{}).get('players',[])
    athletes=[a.get('athlete',{}) for g in groups for s in g.get('statistics',[]) for a in s.get('athletes',[])]
    present=any(str(a.get('id',''))==original for a in athletes)
    try:
        actual=extract(summary,original,columns)
    except ValueError:
        if present:raise
        actual=None
    if actual is not None or present:return actual
    header=summary.get('header') or {}
    if str(header.get('id',''))!=str(event_id):return None
    competitions=header.get('competitions') or []
    if len(competitions)!=1 or not competitions[0].get('status',{}).get('type',{}).get('completed'):return None
    team=name_key(result.get('team'))
    player=name_key(card.get('Player'))
    if not team or not player:return None
    teams={str(c.get('team',{}).get('id')) for c in competitions[0].get('competitors',[])
           if team in {name_key(c.get('team',{}).get(k)) for k in ('displayName','name','shortDisplayName')}}
    if len(teams)!=1:return None
    candidates={str(a.get('id')) for g in groups for s in g.get('statistics',[]) for row in s.get('athletes',[])
                for a in [row.get('athlete',{})] if a.get('id') and
                player in {name_key(a.get('displayName')),name_key(a.get('fullName'))}}
    if len(candidates)!=1:return None
    resolved=next(iter(candidates))
    matched_teams={str(g.get('team',{}).get('id')) for g in groups for s in g.get('statistics',[])
                   for row in s.get('athletes',[]) if str(row.get('athlete',{}).get('id'))==resolved}
    if matched_teams!=teams:return None
    recovered=extract(summary,resolved,columns)
    if recovered is None:return None
    return dict(recovered,identity_verification={'method':'unique-full-name-verified-game-team',
        'frozen_player_id':original,'resolved_player_id':resolved,'event_id':str(event_id),
        'team_id':next(iter(teams))})
