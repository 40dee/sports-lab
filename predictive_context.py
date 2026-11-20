"""Pregame features from saved player logs and injury context; no fitted adjustments."""
import pandas as pd

def build(history,card,cutoff,teammate_context=None,availability=None):
    start=pd.to_datetime(card.get('Start (UTC)'),utc=True,errors='coerce')
    cutoff=pd.to_datetime(cutoff,utc=True,errors='coerce')
    result={'version':'pregame-context-v1','as_of':cutoff.isoformat() if pd.notna(cutoff) else None,
            'days_since_last_appearance':None,'played_previous_day':None,'last5_minutes':None,
            'previous5_minutes':None,'minutes_shift':None,'out_teammates':None,'starting_status':None,
            'usage_adjustment':None,'activation':'features_only_no_projection_adjustment'}
    if pd.isna(start) or pd.isna(cutoff):return result
    limit=min(start,cutoff)
    if history is not None and {'GAME_DATE','MIN'}.issubset(history.columns):
        frame=history.copy();frame['GAME_DATE']=pd.to_datetime(frame.GAME_DATE,utc=True,errors='coerce')
        frame['MIN']=pd.to_numeric(frame.MIN,errors='coerce')
        frame=frame[frame.GAME_DATE.lt(limit)&frame.MIN.between(.01,70)].sort_values('GAME_DATE',ascending=False)
        if 'GAME_ID' in frame:frame=frame.drop_duplicates('GAME_ID')
        if len(frame):
            days=(start.tz_convert('America/Los_Angeles').date()-frame.iloc[0].GAME_DATE.tz_convert('America/Los_Angeles').date()).days
            if days>=1:result.update(days_since_last_appearance=days,played_previous_day=days==1)
        if len(frame)>=5:result['last5_minutes']=float(frame.head(5).MIN.mean())
        if len(frame)>=10:
            result['previous5_minutes']=float(frame.iloc[5:10].MIN.mean())
            result['minutes_shift']=result['last5_minutes']-result['previous5_minutes']
    context=teammate_context or {};availability=availability or {}
    stamp=pd.to_datetime(context.get('checked_at') or context.get('fetched_at'),utc=True,errors='coerce')
    if context.get('available') and pd.notna(stamp) and stamp<=limit:
        result['out_teammates']=len(context.get('out_teammates') or [])
    stamp=pd.to_datetime(availability.get('checked_at') or availability.get('fetched_at'),utc=True,errors='coerce')
    if pd.notna(stamp) and stamp<=limit and availability.get('starting_status'):
        result['starting_status']=availability['starting_status']
    result['limitations']='Player-appearance rest, not verified team B2B. Minutes shift is a role proxy, not confirmed starting status. Injury counts do not establish usage redistribution.'
    return result
