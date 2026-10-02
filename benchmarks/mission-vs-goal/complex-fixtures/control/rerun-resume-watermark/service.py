from boundary import input_value

def scenario():
    raw = input_value()
    key, cents, cache, old = raw['key'], raw['cents'], {'alpha': 2}, 1
    return {'processed': ['b', 'c']}
