"""Conservative flags from an already-fetched ESPN team injury response."""


def from_availability(availability, own_player_id):
    if not availability.get('available') or 'teammates' not in availability:
        return {'available': False, 'out_teammates': [], 'reason': 'Saved team injury context unavailable.'}
    teammates=[]
    for item in availability['teammates']:
        if str(item.get('player_id'))==str(own_player_id):
            continue
        if str(item.get('status','')).lower() in ('out','suspended'):
            teammates.append(item)
    return {'available': True, 'checked_at': availability.get('checked_at'),
            'fetched_at': availability.get('fetched_at'), 'out_teammates': teammates,
            'high_minute_teammates': None, 'returning_teammates': None,
            'usage_adjustment': None,
            'note': 'Statuses are source context. Teammate minutes, confirmed returns and usage effects are not established.'}
