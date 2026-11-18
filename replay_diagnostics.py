"""Chronological uncertainty checks and saved-data inventory; no provider calls."""
import math
import numpy as np
import pandas as pd


def fit_sigmoid(probabilities, outcomes):
    """Regularized logistic recalibration; the prior is an uninformative 50%."""
    p=np.clip(np.asarray(probabilities,float),.01,.99)
    x=np.column_stack((np.ones(len(p)),np.log(p/(1-p))))
    y=np.asarray(outcomes,float)
    beta=np.zeros(2)
    for _ in range(30):
        q=1/(1+np.exp(-np.clip(x@beta,-30,30)))
        gradient=x.T@(q-y)+20*beta
        hessian=(x.T*(q*(1-q)))@x+20*np.eye(2)
        step=np.linalg.solve(hessian,gradient)
        beta-=step
        if np.max(abs(step))<1e-7:break
    return beta


def apply_sigmoid(probability, beta):
    p=max(.01,min(.99,float(probability)))
    z=float(beta[0]+beta[1]*math.log(p/(1-p)))
    return float(1/(1+math.exp(-max(-30,min(30,z)))))


def uncertainty_report(frame, probability_fn):
    """Fit only grades observed before the earliest forecast on each later day."""
    frame=frame.copy()
    for field in ('created','graded_at'):
        frame[field]=pd.to_datetime(frame[field],utc=True,errors='coerce')
    frame['corrected_probability']=.5
    frame['variance_probability']=frame.v2_prob
    folds=[]
    for day in sorted(frame.day.unique()):
        test=frame[frame.day.eq(day)]
        cutoff=test.created.min()
        train=frame[frame.day.lt(day)&frame.graded_at.notna()&frame.graded_at.le(cutoff)]
        trained=0
        for stat,group in test.groupby('stat'):
            local=train[train.stat.eq(stat)]
            # Residual MSE includes game noise. Add only excess variance, avoiding double counting.
            extra=0.
            if len(local)>=30:
                residual_mse=float(np.mean((local.actual-local.v2_proj)**2))
                game_variance=float(local.game_variance.mean())
                extra=max(0.,residual_mse-game_variance)*len(local)/(len(local)+30)
            for index,row in group.iterrows():
                values=row.history_values
                p,_=probability_fn(values,row.v2_proj,row.line,row.v2_dir,row.columns,row.is_fantasy,
                                    model_error_variance=extra)
                frame.at[index,'variance_probability']=p
            # Use the same variance-adjusted transform on earlier examples to fit the sigmoid.
            calibration=local if len(local)>=75 else train
            if len(calibration)>=75:
                # Calibration is fitted on stored raw probabilities; uncertainty correction can
                # only reduce their separation. Both training and evaluation are chronological.
                beta=fit_sigmoid(calibration.v2_prob,calibration.v2_win)
                for index in group.index:
                    frame.at[index,'corrected_probability']=apply_sigmoid(frame.at[index,'variance_probability'],beta)
                trained+=len(group)
        folds.append({'day':day,'picks':len(test),'training_picks':len(train),'calibrated_picks':trained,
                      'latest_training_grade':train.graded_at.max().isoformat() if len(train) else None,
                      'forecast_cutoff':cutoff.isoformat(),
                      'raw_brier':float(np.mean((test.v2_prob-test.v2_win)**2)),
                      'corrected_brier':float(np.mean((frame.loc[test.index,'corrected_probability']-test.v2_win)**2))})
    def buckets(field):
        output=[]
        for threshold in (.55,.58):
            group=frame[frame[field].ge(threshold)]
            output.append({'threshold':threshold,'picks':len(group),
                           'average_prediction':float(group[field].mean()) if len(group) else None,
                           'hit_rate':float(group.v2_win.mean()) if len(group) else None})
        return output
    return {'method':'past-observed-grades-only; excess residual variance + regularized sigmoid; 50% before sufficient evidence',
            'raw_brier':float(np.mean((frame.v2_prob-frame.v2_win)**2)),
            'variance_only_brier':float(np.mean((frame.variance_probability-frame.v2_win)**2)),
            'corrected_brier':float(np.mean((frame.corrected_probability-frame.v2_win)**2)),
            'constant_50_brier':.25,'picks':len(frame),'folds':folds,
            'raw_buckets':buckets('v2_prob'),'corrected_buckets':buckets('corrected_probability'),
            'below_025':bool(np.mean((frame.corrected_probability-frame.v2_win)**2)<.25),
            'activation':'offline_candidate_only'}


def pra_ablation_report(frame):
    group=frame[frame.stat.eq('AST+PTS+REB')]
    output=[]
    for name,column in (('v1','v1_proj'),('v2','v2_proj'),('No Vegas','no_vegas_proj'),
                        ('No blowout','no_blowout_proj'),('Neither','neither_proj')):
        if not len(group):continue
        decided=group[(group[column]-group.line).abs().gt(1e-9)]
        correct=(decided[column].gt(decided.line)==decided.actual.gt(decided.line))
        output.append({'variant':name,'picks':len(group),'decided':len(decided),'ties':len(group)-len(decided),
                       'hit_rate':float(correct.mean()) if len(decided) else None,
                       'mae':float(abs(group[column]-group.actual).mean()),
                       'changed_sides_vs_v2':int((group[column].gt(group.line)!=group.v2_proj.gt(group.line)).sum())})
    return output


def health_inventory(store):
    records=store.items('health')
    players=[p for _,r in records for p in (r.get('players') or {}).values()]
    return {'saved_reports':len(records),'player_checks':len(players),
            'team_ids':sum(bool(p.get('team_id')) for p in players),
            'verified_injury_checks':sum(p.get('injuries_available') is True for p in players),
            'out_or_suspended_checks':sum(any(str(s).lower() in ('out','suspended') for s in p.get('statuses',[])) for p in players),
            'available_fields':['player_id','team_id','checked_at','roster_checked_at','statuses','blocked'],
            'limitations':['Board players only; not a complete team injury history.',
                           'No teammate minutes, usage redistribution, confirmed return or minutes restriction is inferred.',
                           'An absent injury entry is not proof of being active.']}


def sportsbook_inventory(store):
    """Inspect only saved fallback game responses; no network or account query."""
    import json
    from collections import Counter
    from sportsbook_capture import REQUEST_BOOKS
    if callable(getattr(store,'query',None)):
        saved=store.query('SELECT payload FROM sports_records WHERE namespace=? AND key LIKE ?',
                          ('source','the_odds_api_game_v2:%'),True)
        bodies=[json.loads(row[0]) for row in saved]
    else:
        bodies=[value for key,value in store.items('source') if key.startswith('the_odds_api_game_v2:')]
    books=Counter()
    for value in bodies:
        for book in (value.get('event') or {}).get('bookmakers',[]):
            if isinstance(book,dict) and book.get('key'):
                books[book['key']]+=1
    return {'saved_game_responses':len(bodies),'books_in_saved_responses':dict(books),
            'future_request_books':REQUEST_BOOKS.split(','),'extra_requests':0,
            'billing_note':'Same markets, one group of at most 10 books, unchanged maximum reservation. More returned markets can increase actual charge within that reservation.'}
