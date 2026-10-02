from boundary import normalise
from store import TransactionStore

def execute(request):
    request = normalise(request); store = TransactionStore(request['existing']); committed = store.apply(request['writes'], request['fail_after']); return {'committed': committed, 'state': store.snapshot()}
