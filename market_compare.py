"""Cross-provider sportsbook comparison using exact semantic market identity."""
from datetime import datetime
from sportsbook_capture import BOOKS
import math

def compare_no_vig(card, rows, normalize_player, team_key, columns, fantasy_weights):
    def player(value):
        value=str(value or "")
        if "," in value:
            last,first=value.split(",",1);value=first+" "+last
        return normalize_player(value)
    def stamp(value):
        try:
            result=datetime.fromisoformat(str(value).replace("Z","+00:00"))
            return result if result.tzinfo else None
        except (TypeError,ValueError):
            return None
    def teams(value):
        names=str(value or "").split(" vs ")
        keys=tuple(sorted(team_key(name) for name in names))
        return keys if len(keys)==2 and all(keys) and keys[0]!=keys[1] else None
    def period(value):
        value=normalize_player(value or "fullgame")
        return "fullgame" if value in ("result","fullgame","game","full","match") else value
    start,game=stamp(card.get("Start (UTC)")),teams(card.get("Game"))
    own_columns=tuple(sorted(columns(card) or []))
    scoring=tuple(sorted((fantasy_weights(card) or {}).items()))
    if not start or not game or not own_columns or not player(card.get("Player")):return None
    pairs={}
    for row in rows:
        book=str(row.get("Platform","")).lower()
        if book not in BOOKS:continue
        other=stamp(row.get("Start (UTC)"))
        if not other or abs((other-start).total_seconds())>900:continue
        observed=stamp(row.get("Odds checked (UTC)"))
        captured=stamp(row.get("Provider fetched at"))
        if observed and observed>=start:continue
        if captured and (captured>=start or (observed and not 0<=(captured-observed).total_seconds()<=1800)):continue
        if teams(row.get("Game"))!=game or player(row.get("Player"))!=player(card.get("Player")):continue
        if tuple(sorted(columns(row) or []))!=own_columns:continue
        if tuple(sorted((fantasy_weights(row) or {}).items()))!=scoring:continue
        if period(row.get("Period"))!=period(card.get("Period")):continue
        try:
            line,other_line,price=float(card["Line"]),float(row["Line"]),float(row["Odds"])
            if not all(math.isfinite(v) for v in (line,other_line,price)):continue
            if line!=other_line or abs(price)<100:continue
        except (KeyError,TypeError,ValueError):continue
        direction=str(row.get("Direction","")).upper()
        if direction not in ("MORE","LESS"):continue
        probability=100/(price+100) if price>0 else -price/(-price+100)
        # Pair within a sportsbook's own market, never across two snapshots.
        key=(book,str(row.get("Fixture ID") or other.isoformat()),str(row.get("Market ID") or own_columns),
             str(row.get("Odds checked (UTC)") or ""),str(row.get("Provider fetched at") or ""))
        pairs.setdefault(key,{}).setdefault(direction,set()).add((price,probability))
    valid={}
    for pair_key,sides in pairs.items():
        if set(sides)!={"MORE","LESS"} or any(len(v)!=1 for v in sides.values()):continue
        over,under=next(iter(sides["MORE"])),next(iter(sides["LESS"]))
        total=over[1]+under[1]
        valid.setdefault(pair_key[0],{})[(over[0],under[0])]={"book":pair_key[0],"over_price":over[0],"under_price":under[0],
            "more":over[1]/total,"less":under[1]/total,"observed_at":pair_key[3] or None,"captured_at":pair_key[4] or None,"match_method":"teams-time-player-stat-period-line"}
    books=[next(iter(values.values())) for values in valid.values() if len(values)==1]
    if not books:return None
    if len(books)==1:
        return dict(books[0],books=[books[0]],book_count=1,consensus_method="equal-weight-no-vig")
    more=sum(book["more"] for book in books)/len(books)
    return {"book":"consensus","more":more,"less":1-more,"books":books,"book_count":len(books),
            "match_method":"teams-time-player-stat-period-line","consensus_method":"equal-weight-no-vig"}

