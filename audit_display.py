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


DISPLAY_NAMES = {
    'load_position_defense':'Position defense', 'load_team_advanced':'Team pace & ratings',
    'news':'NBA news', 'player_points':'Points', 'player_points_alternate':'Alternate points',
    'player_fantasy_points':'Fantasy Score', 'player_fantasy_points_alternate':'Alternate Fantasy Score',
    'http_requests':'HTTP requests', 'key_accepted':'Key accepted',
    'scheduled_downloads_enabled':'Automatic downloads enabled', 'start_utc':'Tipoff (Pacific)',
    'checked_at':'Checked (Pacific)', 'credits_remaining':'Credits remaining',
    'credits_used':'Credits used', 'max_test_credits':'Maximum test credits',
}


def display_name(value):
    return DISPLAY_NAMES.get(str(value),str(value).replace('_',' ').capitalize())


def display_time(value):
    try:
        stamp = pd.to_datetime(value,utc=True,errors='coerce')
        if pd.isna(stamp):return '—'
        return stamp.tz_convert('America/Los_Angeles').strftime('%b %d, %Y · %I:%M %p PT')
    except (ValueError,TypeError,OverflowError):
        return '—'


def render_metric_strip(st, metrics):
    """A real CSS grid: Streamlit columns otherwise stack into tall mobile tiles."""
    from html import escape
    cells = ''.join('<div style="min-width:0"><span style="display:block;font-size:11px;overflow-wrap:anywhere;color:#91acbc">'+
                    escape(str(label))+'</span><strong style="font-size:20px">'+escape(str(value))+'</strong></div>'
                    for label,value in metrics)
    st.markdown('<div class="sl-audit-metric-strip" style="display:grid;grid-template-columns:repeat('+str(max(1,len(metrics)))+
        ',minmax(0,1fr));gap:8px;padding:12px;border:1px solid #1d3242;border-radius:12px">'+cells+'</div>',unsafe_allow_html=True)


def render_fact_cards(st, rows):
    """Wrap all values, including long headlines/IDs, instead of a wide scroller."""
    from html import escape
    cards=[]
    for row in rows:
        fields=''.join('<div style="display:grid;grid-template-columns:minmax(75px,32%) minmax(0,1fr);gap:8px;margin:5px 0">'+
            '<span style="color:#91acbc">'+escape(display_name(key))+'</span><span style="overflow-wrap:anywhere">'+
            escape(format_value(value,'integer') if isinstance(value,int) and not isinstance(value,bool) else readable(value))+'</span></div>' for key,value in row.items())
        cards.append('<div style="border:1px solid #1d3242;border-radius:12px;padding:10px;margin:8px 0">'+fields+'</div>')
    st.markdown('<div class="sl-audit-fact-cards">'+''.join(cards)+'</div>',unsafe_allow_html=True)


def render_saved_report(st, report):
    rows=[]
    for key,value in report.items():
        if key=='rows':continue
        if key in ('note','notes'):
            st.caption(readable(value))
        elif isinstance(value,dict):
            st.markdown('**'+display_name(key)+'**')
            render_fact_cards(st,[{display_name(k):v for k,v in value.items()}])
        else:
            is_time=key.endswith(('_at','_utc'))
            rows.append({display_name(key):display_time(value) if is_time else
                         display_name(value) if key in ('state','status') else value})
    if rows:render_fact_cards(st,[dict(pair for row in rows for pair in row.items())])
