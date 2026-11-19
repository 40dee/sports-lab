"""Normalize saved side metadata without turning unknown availability into a pass."""
def projection_sides(row):
    selections=row.get('selections')
    if isinstance(selections,list) and selections:
        return selections,True
    offered=row.get('sides');tier=row.get('tier');result=[];known=False
    for side in ('more','less'):
        raw=offered.get(side) if isinstance(offered,dict) else None
        side_tier=tier.get(side) if isinstance(tier,dict) else tier
        if isinstance(raw,dict) and type(raw.get('available')) is bool:
            result.append(dict(raw,side=side,tier=raw.get('tier') or side_tier));known=True
        elif type(raw) is bool:
            result.append({'side':side,'available':raw,'tier':side_tier});known=True
        elif isinstance(offered,list) and side in [str(v).lower() for v in offered if isinstance(v,str)]:
            result.append({'side':side,'available':True,'tier':side_tier});known=True
        elif type(row.get(side+'_available')) is bool:
            result.append({'side':side,'available':row[side+'_available'],'tier':row.get(side+'_tier') or side_tier});known=True
    # Compact empty arrays omit offer metadata. Standard/Regular uses the
    # existing two-sided regular-line rule; explicit offers/flags win above.
    empty_metadata=not selections and (offered is None or offered==[] or offered=={})
    has_flags=any(k in row for k in ('more_available','less_available'))
    if not known and empty_metadata and not has_flags and str(tier).strip().lower() in ('standard','regular'):
        return [{'side':s,'tier':'standard','available':True} for s in ('more','less')],True
    return result,known
