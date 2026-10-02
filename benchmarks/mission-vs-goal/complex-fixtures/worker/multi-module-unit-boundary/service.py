from boundary import to_minor
from consumer import present
from store import Ledger

def execute(request):
    ledger = Ledger(); ledger.post(to_minor(request['payload']))
    return present(ledger.minor, int(request.get('fee_minor', 0)))
