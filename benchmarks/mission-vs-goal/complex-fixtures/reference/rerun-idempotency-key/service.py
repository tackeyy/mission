from boundary import normalise
from store import EffectStore

def execute(request):
    request = normalise(request); store = EffectStore(request['state'])
    for call in request['calls']:
        store.apply(call['event'])
        if call.get('reload'): store = EffectStore(store.snapshot())
    return {'effects': store.snapshot(), 'total': sum(store.snapshot().values())}
