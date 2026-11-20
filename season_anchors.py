"""Compact, dated pregame anchors; never fetch or reconstruct unavailable history."""
import math
import pandas as pd

STATS=('PTS','REB','AST','FG3M','STL','BLK','TOV')

def pregame_season_totals(history, season, cutoff):
    cutoff=pd.to_datetime(cutoff,utc=True,errors='coerce')
    if pd.isna(cutoff) or not {'GAME_DATE','GAME_ID','SEASON','MIN'}.issubset(history.columns):return {}
    frame=history.copy()
    frame['GAME_DATE']=pd.to_datetime(frame.GAME_DATE,utc=True,errors='coerce')
    frame['MIN']=pd.to_numeric(frame.MIN,errors='coerce')
    frame=frame[frame.GAME_DATE.lt(cutoff)&frame.MIN.between(.01,70)].drop_duplicates('GAME_ID').sort_values('GAME_DATE',ascending=False)
    year=int(str(season).split('-')[0]);prior=f'{year-1}-{str(year)[-2:]}'
    output={}
    for key,label in (('current_season_totals',season),('prior_season_totals',prior)):
        sample=frame[frame.SEASON.eq(label)];stats={}
        for stat in STATS:
            if stat not in sample:continue
            numeric=pd.to_numeric(sample[stat],errors='coerce')
            valid=sample[numeric.ge(0)&numeric.notna()&numeric.map(lambda x:math.isfinite(x) if pd.notna(x) else False)]
            stats[stat]={'total':float(pd.to_numeric(valid[stat]).sum()),'minutes':float(valid.MIN.sum()),'games':len(valid),
                         'recent_game_ids':valid.head(15).GAME_ID.astype(str).tolist()}
        output[key]={'version':1,'season':label,'as_of':cutoff.isoformat(),'games':len(sample),'stats':stats,
                     'coverage':'Available fetched pregame rows; missing prior history is not inferred.'}
    return output

def summary_stat(summary, season, stat, cutoff, recent=None):
    """Reject future/wrong-season totals and incompatible recent-window subtraction."""
    if not isinstance(summary,dict) or summary.get('version')!=1 or summary.get('season')!=season:return None
    observed=pd.to_datetime(summary.get('as_of'),utc=True,errors='coerce')
    cutoff=pd.to_datetime(cutoff,utc=True,errors='coerce')
    if pd.isna(observed) or pd.isna(cutoff) or observed>cutoff:return None
    data=(summary.get('stats') or {}).get(stat)
    if not isinstance(data,dict):return None
    try:
        minutes,total,games=float(data['minutes']),float(data['total']),int(data['games'])
        if not all(math.isfinite(v) for v in (minutes,total)) or minutes<=0 or total<0 or games<1:return None
        if recent is not None:
            if 'GAME_ID' not in recent or recent.GAME_ID.astype(str).tolist()!=data.get('recent_game_ids'):return None
            if games<len(recent) or minutes<float(recent.MIN.sum())-1e-6 or total<float(recent[stat].sum())-1e-6:return None
        return {'minutes':minutes,'total':total,'games':games}
    except (KeyError,TypeError,ValueError,OverflowError):return None
