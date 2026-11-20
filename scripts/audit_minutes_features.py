"""Offline frozen minutes-feature diagnostics; no provider calls, writes or model fit.

Run from any directory: python scripts/audit_minutes_features.py
Uses TURSO_DATABASE_URL/TURSO_AUTH_TOKEN, DATABASE_URL, or --database (SQLite).
Readiness counts are a sample-size gate, not proof that v3 will improve predictions.
"""
from __future__ import annotations
import argparse
import ast
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import urlparse
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from result_scopes import normalized, standard_playable_rows, timestamp


def load_helpers():
    """Same AST cutoff as settled_consensus_day, without app startup side effects."""
    source = (ROOT / 'app.py').read_text()
    names = {'pick_offered_sides', 'pick_is_selectable', 'market_columns', 'fantasy_weights'}
    env = {'re': re}
    for node in ast.parse(source).body:
        if isinstance(node, ast.If) and "'--rebuild-compact-fantasy'" in (ast.get_source_segment(source, node.test) or ''):
            break
        if isinstance(node, ast.FunctionDef) and node.name in names:
            # Preserve the base mapping before the Fantasy wrapper replaces it.
            if node.name == 'market_columns' and 'market_columns' in env:
                env['_base_market_columns'] = env['market_columns']
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(ROOT / 'app.py'), 'exec'), env)
    return env


def finite(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def feature_buckets(result, created):
    features = result.get('predictive_context') or {}
    as_of = timestamp(features.get('as_of'))
    if as_of and as_of > created:
        features = {}
    days = finite(features.get('days_since_last_appearance'))
    previous = features.get('played_previous_day')
    rest = ('Back-to-back' if days == 1 or (days is None and previous is True)
            else '1+ days rest' if (days is not None and days >= 2) or (days is None and previous is False)
            else 'Unavailable')
    shift = finite(features.get('minutes_shift'))
    if shift is None:
        last, prior = finite(features.get('last5_minutes')), finite(features.get('previous5_minutes'))
        if last is not None and prior is not None:
            shift = last - prior
    trend = ('Unavailable' if shift is None else 'Falling (≤ -3 min)' if shift <= -3
             else 'Rising (≥ +3 min)' if shift >= 3 else 'Stable (-3 to +3)')
    context = result.get('teammate_context') or {}
    observed = timestamp(context.get('checked_at') or context.get('fetched_at'))
    teammates = context.get('out_teammates')
    out = ('1+ out' if teammates else '0 out') if (
        context.get('available') is True and isinstance(teammates, (list, tuple))
        and (observed is None or observed <= created)) else 'Unavailable'
    return {'rest': rest, 'minutes_trend': trend, 'teammate_out': out}


def audit(records, grades, helpers=None):
    """Select the first playable Standard line before filtering grades/minutes."""
    helpers = helpers or load_helpers()
    lookup = dict(records)
    rows = []
    for key, frozen in lookup.items():
        card, result = frozen.get('card') or {}, frozen.get('result') or {}
        grade = grades.get(key) or {}
        rows.append({'ID': key, 'Platform': card.get('Platform'),
            'Line type': card.get('Line Type'),
            'Selectable': result.get('selection_allowed') is not False and helpers['pick_is_selectable'](card, result),
            'Created (UTC)': frozen.get('created_at'), 'Start (UTC)': card.get('Start (UTC)'),
            'Outcome': grade.get('outcome'), 'Grade status': grade.get('status'),
            'Event': frozen.get('event_id'), 'Stat columns': helpers['market_columns'](card),
            'Player': card.get('Player'), 'Period': card.get('Period')})
    selected = []
    for row in standard_playable_rows(rows):
        if row['Grade status'] != 'FINAL' or row['Outcome'] not in ('WIN', 'LOSS'):
            continue
        frozen = lookup[row['ID']]
        result, grade = frozen.get('result') or {}, grades[row['ID']]
        actual, projected = finite(grade.get('minutes')), finite(result.get('minutes'))
        if actual is None or projected is None or actual < 0 or projected < 0:
            continue
        columns = set(row['Stat columns'])
        selected.append({'game': (str(row['Event']), normalized(row['Player'])),
            'day': pd.Timestamp(row['Start (UTC)']).tz_convert('America/Los_Angeles').date().isoformat(),
            'bias': actual - projected, 'win': row['Outcome'] == 'WIN',
            'features': feature_buckets(result, timestamp(row['Created (UTC)'])),
            'pra_fantasy': columns == {'PTS', 'REB', 'AST'} or bool(helpers['fantasy_weights'](frozen['card']))})

    def breakdown(entries):
        groups = {name: defaultdict(list) for name in ('rest', 'minutes_trend', 'teammate_out')}
        for entry in entries:
            for name, bucket in entry['features'].items():
                groups[name][bucket].append(entry)
        return {name: {bucket: {'picks': len(values),
            'player_games': len({v['game'] for v in values}),
            'avg_minutes_bias': sum(v['bias'] for v in values) / len(values),
            'minutes_mae': sum(abs(v['bias']) for v in values) / len(values),
            'v1_hit_rate': sum(v['win'] for v in values) / len(values)}
            for bucket, values in sorted(buckets.items())} for name, buckets in groups.items()}

    games = len({v['game'] for v in selected})
    dates = len({v['day'] for v in selected})
    return {'readiness': {'settled_picks': len(selected), 'distinct_player_games': games,
                         'graded_dates': dates, 'ready_for_v3_fit': dates >= 10 and games >= 300},
            'by_feature': breakdown(selected),
            'pra_and_fantasy_only': breakdown([v for v in selected if v['pra_fantasy']]),
            'notes': 'Frozen features only; unavailable inputs are not reconstructed. Rest is player-appearance rest, not verified team B2B. Metrics weight picks; shared player-games are counted separately. Readiness is a sample-size gate only. No fit, forecast changes, provider calls or database writes.'}


@contextmanager
def readonly_query(database):
    """Only SELECT statements; skip all app/store constructors and schema setup."""
    turso = os.getenv('TURSO_DATABASE_URL', '')
    postgres = os.getenv('DATABASE_URL', '')
    close = lambda: None
    if database is None and turso:
        import requests
        parsed = urlparse(turso.replace('libsql://', 'https://', 1))
        token = os.getenv('TURSO_AUTH_TOKEN', '')
        if (parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.turso.io')
                or parsed.username or parsed.password or parsed.query or not token):
            raise ValueError('Valid Turso URL and token required')
        def read(sql, values):
            response = requests.post('https://' + parsed.netloc + '/v2/pipeline',
                headers={'Authorization': 'Bearer ' + token}, timeout=30,
                json={'requests': [{'type': 'execute', 'stmt': {'sql': sql, 'want_rows': True,
                    'args': [{'type': 'text', 'value': str(v)} for v in values]}}, {'type': 'close'}]})
            response.raise_for_status()
            item = response.json()['results'][0]
            if item.get('type') != 'ok':
                raise RuntimeError('Read-only database query failed')
            return [[cell.get('value') for cell in row] for row in item['response']['result']['rows']]
        remote = False  # libSQL uses SQLite JSON syntax.
    elif database is None and postgres:
        import psycopg
        connection = psycopg.connect(postgres, options='-c default_transaction_read_only=on', connect_timeout=5)
        close = connection.close
        read = lambda sql, values: connection.execute(sql.replace('?', '%s'), values).fetchall()
        remote = True
    else:
        path = Path(database or ROOT / 'sports_lab_workflow.sqlite3').resolve()
        connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        connection.execute('PRAGMA query_only=ON')
        close = connection.close
        read = lambda sql, values: connection.execute(sql, values).fetchall()
        remote = False
    def query(sql, values=()):
        if not sql.lstrip().upper().startswith('SELECT '):
            raise ValueError('Only read-only SELECT queries are permitted')
        return read(sql, values)
    try:
        yield query, remote
    finally:
        close()


def read_records(query, postgres=False):
    # Strip bulky game/roster/snapshot evidence before transfer, retaining features.
    expression = ("(payload::jsonb - 'snapshot' #- '{result,games}' #- '{result,roster}' #- '{result,advanced}' #- '{result,position_data}')::text"
                  if postgres else "json_remove(payload,'$.snapshot','$.result.games','$.result.roster','$.result.advanced','$.result.position_data')")
    records, grades = [], {}
    for namespace in ('forecast', 'grade'):
        after = ''
        while True:
            rows = query('SELECT key,' + (expression if namespace == 'forecast' else 'payload') +
                ' FROM sports_records WHERE namespace=? AND key>? ORDER BY key LIMIT 500', (namespace, after))
            for key, payload in rows:
                if namespace == 'forecast':
                    records.append((key, json.loads(payload)))
                else:
                    grades[key] = json.loads(payload)
            if len(rows) < 500:
                break
            after = rows[-1][0]
    return records, grades


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', help='Existing SQLite database; overrides remote configuration')
    args = parser.parse_args(argv)
    with readonly_query(args.database) as (query, postgres):
        records, grades = read_records(query, postgres)
    print('MINUTES_FEATURE_AUDIT_JSON ' + json.dumps(audit(records, grades), allow_nan=False))


if __name__ == '__main__':
    main()
