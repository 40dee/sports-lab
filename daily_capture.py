"""Persistent one-capture policy shared by all paid line providers."""
import json

def require_capture(store, day):
    # Older stores/tests remain compatible until the policy is activated.
    if not store.get('board_capture_policy','enabled'):return
    rows=store.query('SELECT payload FROM sports_records WHERE namespace=? AND key=?',
                     ('daily_board_capture',day),fetch=True)
    record=json.loads(rows[0][0]) if rows else {}
    if record.get('state')!='capturing':
        raise ValueError('Daily board capture is closed; cached board only. No paid retries or refreshes.')

def claim_capture(store, day, stamp):
    store.put('board_capture_policy','enabled',True)
    payload=json.dumps({'state':'capturing','started_at':stamp})
    rows=store.query('INSERT INTO sports_records VALUES (?,?,?,?) ON CONFLICT(namespace,key) DO NOTHING RETURNING key',
        ('daily_board_capture',day,payload,stamp),fetch=True)
    return bool(rows)

def close_capture(store, day, stamp):
    record=store.get('daily_board_capture',day) or {}
    store.put('daily_board_capture',day,dict(record,state='closed',closed_at=stamp))
