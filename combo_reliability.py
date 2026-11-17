"""Bounded experimental influence for combo ranking; frozen projections are unchanged."""
import hashlib
import json
import math
from pick_reliability import features, score

VERSION='reliability-blend-v1'
MAX_ADJUSTMENT=.05
STRENGTH=.20

def context(model, day, mode='learned_blend'):
    if mode!='learned_blend' or not model or not model.get('cells'):
        return None
    trained=model.get('trained_through')
    if not trained or trained>=day or model.get('effective_examples',0)<50:
        return None
    digest=hashlib.sha256(json.dumps(model,sort_keys=True).encode()).hexdigest()[:16]
    return {'version':VERSION,'model_digest':digest,'trained_through':trained,
            'max_adjustment':MAX_ADJUSTMENT,'strength':STRENGTH,'experimental':True}

def adjust(raw,card,result,model,ranking_context):
    if not ranking_context or str(card.get('Line Type','')).lower() not in ('standard','regular'):
        return raw,0.
    reliability=score(model,features(card,result))
    delta=max(-MAX_ADJUSTMENT,min(MAX_ADJUSTMENT,STRENGTH*(reliability-model.get('prior',.5))))
    if not math.isfinite(delta):return raw,0.
    return max(0.,raw+delta),delta
