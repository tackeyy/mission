from boundary import normalise
from store import Store
MODE = 'cache'; BROKEN = True

def execute(operations):
    store = Store(); seen = set(); watermark = 0
    for raw in operations:
        op = normalise(raw); kind = op['kind']
        if MODE == 'cache':
            if kind == 'write' and (BROKEN and op['key'] in store.items): pass
            elif kind == 'write': store.items[op['key']] = op['value']
        elif MODE == 'units':
            if kind == 'amount': store.items['amount'] = op['cents'] if BROKEN else op['cents'] / 100
        elif MODE == 'legacy':
            if kind == 'payload': store.items['state'] = op.get('state', 'unknown' if BROKEN else 'open')
        elif MODE == 'rename':
            if kind == 'payload': store.items['priority'] = op.get('priority', 'normal' if BROKEN else op.get('urgency', 'normal'))
        elif MODE == 'rollback':
            if kind == 'put': store.items[op['key']] = op['value']
            if kind == 'fail' and not BROKEN: store.items.clear()
        elif MODE == 'retry':
            if kind == 'send' and (BROKEN or op['id'] not in seen): store.sent.append(op['id']); seen.add(op['id'])
        elif MODE == 'idempotent':
            if kind == 'charge' and (BROKEN or op['id'] not in seen): store.total += op['amount']; seen.add(op['id'])
        elif MODE == 'resume':
            if kind == 'event' and (op['offset'] >= watermark if BROKEN else op['offset'] > watermark): store.sent.append(op['id']); watermark = max(watermark, op['offset'])
        elif MODE == 'cancel':
            if kind == 'add': store.total += op['amount']
            if kind == 'cancel' and not BROKEN: store.total -= op['amount']
        elif MODE == 'dedupe':
            if kind == 'add' and (BROKEN or op['id'] not in seen): store.total += op['amount']; seen.add(op['id'])
        elif MODE == 'lost-update':
            if kind == 'increment': store.total = 1 if BROKEN else store.total + op['amount']
        elif MODE == 'ordering':
            if kind == 'candidate': store.items['winner'] = op['name'] if BROKEN else min(store.items.get('winner', op['name']), op['name'])
    return store.snapshot()
