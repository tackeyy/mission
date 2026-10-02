from boundary import normalise
from store import PriorityStore

def execute(request):
    store = PriorityStore(); store.save(normalise(request)['priority']); return {'priority': store.priority}
