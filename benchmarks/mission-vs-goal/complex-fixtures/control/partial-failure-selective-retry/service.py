from boundary import normalise
from store import DeliveryStore

def execute(request):
    request = normalise(request); store = DeliveryStore(request['accepted'], request['fail_once'])
    for round_ids in request['rounds']:
        for identifier in round_ids: store.send(identifier)
    return {'attempts': store.attempts, 'accepted': sorted(store.accepted)}
