from boundary import normalise
from store import WatermarkStore

def execute(request):
    request = normalise(request); store = WatermarkStore(request['state'])
    for run in request['runs']:
        for event in run.get('events', []): store.apply(event)
        if run.get('reload'): store = WatermarkStore(store.snapshot())
    return store.snapshot()
