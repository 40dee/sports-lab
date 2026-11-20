"""Presentation-only formatting for saved audit values; never modifies input rows."""
import math
import pandas as pd

LABELS = {
    'v1_hit': 'v1 Hit %', 'v2_hit': 'v2 Hit %', 'hit_rate': 'Hit %',
    'v1_mae': 'v1 MAE', 'v2_mae': 'v2 MAE',
    'v1_brier': 'v1 Brier', 'v2_brier': 'v2 Brier',
    'historical_accuracy': 'Raw Hit %', 'shrunk_accuracy': 'Smoothed Hit %',
    'effective_examples': 'Samples', 'weighted_greens': 'Wins', 'weighted_reds': 'Losses',
    'candidate_top_accuracy': 'Candidate Top Hit %', 'gap_top_accuracy': 'Gap Top Hit %',
    'candidate_brier': 'Candidate Brier', 'baseline_brier': 'Baseline Brier',
    'player_game_markets': 'Player-game markets', 'calibration_n': 'Calibration samples',
    'trained_through': 'Trained through', 'top_count': 'Top picks',
}


def format_value(value, kind='number'):
    if value is None:
        return '—'
    try:
        if pd.isna(value):
            return '—'
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return readable(value)
    if not math.isfinite(number):
        return '—'
    return format(number, {'percent':'.1%', 'brier':'.4f', 'decimal':'.2f',
                           'weight':'.1f', 'integer':',.0f'}.get(kind, '.2f'))


def readable(value):
    if value is None:
        return '—'
    if isinstance(value, dict):
        return ' · '.join(str(k).replace('_', ' ').capitalize()+': '+readable(v) for k,v in value.items()) or '—'
    if isinstance(value, (list, tuple)):
        return ' · '.join(readable(v) for v in value) or '—'
    if isinstance(value, (float, int)):
        return format_value(value)
    return str(value)


def format_table(rows, components=False):
    frame = pd.DataFrame(rows).copy(deep=True)
    for column in frame:
        key = str(column).lower().replace(' ', '_')
        kind = ('percent' if key.endswith(('_accuracy','_hit')) or key in
                ('hit_rate','candidate_accuracy','current_model_accuracy') else
                'brier' if 'brier' in key else
                'weight' if key in ('effective_examples','weighted_greens','weighted_reds','player_game_markets') else
                'integer' if key in ('picks','wins','losses','count','calibration_n','top_count','samples') else
                'decimal' if components or 'mae' in key else None)
        if key == 'cutoff':
            frame[column] = [format_value(v, 'percent' if str(m)=='prob' else 'decimal')
                             for v,m in zip(frame[column],frame.get('metric',['']*len(frame)))]
        elif kind:
            frame[column] = frame[column].map(lambda v: format_value(v,kind))
        else:
            frame[column] = frame[column].map(lambda v: format_value(v) if not isinstance(v,str) else v)
    return frame.rename(columns={c: LABELS.get(c,str(c).replace('_',' ').capitalize()) for c in frame})
