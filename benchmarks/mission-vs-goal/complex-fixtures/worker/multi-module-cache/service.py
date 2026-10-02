from boundary import normalise
from storage import Storage
from cache import Cache

def execute(request):
    request = normalise(request); storage = Storage(request.get('initial', {})); cache = Cache(); values = []
    for operation in request.get('operations', []):
        if operation['kind'] == 'read': values.append(cache.read(storage, operation['key']))
        else:
            storage.write(operation['key'], operation['value'])
            # retained cached value is observable through the next read
    return {'values': values, 'state': storage.snapshot()}
