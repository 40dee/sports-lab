"""Daily candidate fitting, separate from promotion into production forecasts."""
import hashlib
import json
import numpy as np
import pandas as pd


def evaluate_daily(frame, incumbent_alpha=1.0):
    frame=frame.copy()
    for column in ('baseline','adjusted','actual','line'):
        frame[column]=pd.to_numeric(frame[column],errors='coerce')
    valid=np.isfinite(frame[['baseline','adjusted','actual','line']]).all(axis=1)
    start=pd.to_datetime(frame['start'],utc=True,errors='coerce',format='ISO8601')
    created=pd.to_datetime(frame['created'],utc=True,errors='coerce',format='ISO8601')
    frame=frame[valid & start.notna() & created.notna() & (created<start)].copy()
    frame['day']=pd.to_datetime(frame['start'],utc=True,format='ISO8601').dt.tz_convert('America/Los_Angeles').dt.strftime('%Y-%m-%d')
    days=sorted(frame['day'].unique())
    report={'status':'waiting_for_two_graded_dates','activation':'unchanged',
            'graded_dates':len(days),'examples':len(frame),'incumbent_alpha':incumbent_alpha}
    if len(days)<2:return report
    test_day=days[-1];train=frame[frame.day<test_day].copy();test=frame[frame.day==test_day].copy()
    def metrics(data,alpha):
        prediction=(data.baseline+alpha*(data.adjusted-data.baseline)).clip(lower=0)
        # A player's many alternate thresholds share one player-game's weight.
        weights=1/data.groupby(['event','player'])['line'].transform('count')
        correct=((prediction>data.line)==(data.actual>data.line)).astype(float)
        return {'accuracy':float(np.average(correct,weights=weights)),
                'mae':float(np.average(abs(prediction-data.actual),weights=weights))}
    choices=[0,.25,.5,.75,1,1.25,1.5]
    scores={alpha:metrics(train,alpha) for alpha in choices}
    alpha=max(choices,key=lambda a:(scores[a]['accuracy'],-scores[a]['mae'],-abs(a-incumbent_alpha)))
    candidate=metrics(test,alpha);incumbent=metrics(test,incumbent_alpha)
    report.update(status='evaluated',candidate_alpha=alpha,train_examples=len(train),
        train_dates=len(days)-1,holdout_date=test_day,holdout_examples=len(test),
        train_player_games=int(train.groupby(['event','player']).ngroups),
        holdout_player_games=int(test.groupby(['event','player']).ngroups),
        candidate_holdout=candidate,incumbent_holdout=incumbent,
        accuracy_change=candidate['accuracy']-incumbent['accuracy'],
        method='Fit earlier graded dates; evaluate latest graded date. Equal player-game weights. No automatic activation.')
    payload=frame[['start','created','event','player','baseline','adjusted','actual','line']].astype(str).sort_values(['start','event','player','line']).to_dict('records')
    report['input_digest']=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()[:16]
    return report
