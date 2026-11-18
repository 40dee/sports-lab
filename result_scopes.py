"""Display-only cohorts. Select before outcome filtering; never rewrite grades."""
from datetime import datetime,timezone
import re,unicodedata

def normalized(value):
    value=unicodedata.normalize("NFKD",str(value or "")).encode("ascii","ignore").decode().lower()
    return re.sub("[^a-z0-9]","",value)

def timestamp(value):
    try:
        stamp=datetime.fromisoformat(str(value).replace("Z","+00:00"))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo else None
    except (ValueError,TypeError):
        return None

def standard_playable_rows(rows):
    candidates=[]
    for row in rows:
        if str(row.get("Platform","")).lower()!="prizepicks":continue
        if str(row.get("Line type","")).lower() not in ("standard","regular"):continue
        if row.get("Selectable") is not True:continue
        created,start=timestamp(row.get("Created (UTC)")),timestamp(row.get("Start (UTC)"))
        if not created or not start or created>=start:continue
        if row.get("Outcome") in ("WIN","LOSS") and row.get("Grade status")!="FINAL":continue
        if not row.get("Event") or not row.get("Stat columns") or not row.get("Player"):continue
        candidates.append(row)
    candidates.sort(key=lambda r:(r["Created (UTC)"],str(r["ID"])))
    kept={}
    for row in candidates:
        signature=(str(row["Event"]),normalized(row["Player"]),tuple(sorted(row["Stat columns"])),
                   str(row.get("Period") or "fullgame").lower(),"prizepicks")
        kept.setdefault(signature,row)
    return list(kept.values())
