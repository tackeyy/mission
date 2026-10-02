from boundary import normalise
from store import Aggregate

def execute(request):
    store = Aggregate()
    for event in normalise(request): store.apply(event)
    return store.snapshot()
