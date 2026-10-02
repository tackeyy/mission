from boundary import normalise
from store import StateStore

def execute(request):
    store = StateStore(); store.save(normalise(request)); return store.snapshot()
