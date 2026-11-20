"""Exact postgame error attribution; counterfactual diagnostics are not causal proof."""
import math

def split(card,result,grade):
    try:
        p,a,mp,ma,line=map(float,(result['projection'],grade['actual'],result['minutes'],grade['minutes'],card['Line']))
    except (KeyError,ValueError,TypeError):return {'status':'missing_actual_or_projected_minutes'}
    if not all(math.isfinite(v) for v in (p,a,mp,ma,line)) or mp<=0 or ma<=0:return {'status':'invalid_minutes_or_values'}
    direction=result.get('direction')
    if grade.get('status')!='FINAL' or direction not in ('MORE','LESS') or grade.get('outcome') not in ('WIN','LOSS') or a==line:return {'status':'not_decided'}
    win=(a>line)==(direction=='MORE')
    if win!=(grade['outcome']=='WIN'):return {'status':'inconsistent_grade'}
    rp,ra=p/mp,a/ma
    # Symmetric product decomposition: these contributions sum exactly to p-a.
    minutes_error=(mp-ma)*(rp+ra)/2
    rate_error=(rp-ra)*(mp+ma)/2
    def corrected_wins(value):
        return abs(value-line)>1e-9 and ((value>line)==(a>line))
    return {'status':'ok','win':win,'projected_minutes':mp,'actual_minutes':ma,'minutes_delta':mp-ma,
            'minutes_off_5':abs(mp-ma)>=5,'projection_error':p-a,'minutes_contribution':minutes_error,'rate_contribution':rate_error,
            'dominant':'minutes' if abs(minutes_error)>abs(rate_error)+1e-9 else 'rate' if abs(rate_error)>abs(minutes_error)+1e-9 else 'equal',
            'minutes_correction_rescues':not win and corrected_wins(rp*ma),
            'rate_correction_rescues':not win and corrected_wins(ra*mp)}
