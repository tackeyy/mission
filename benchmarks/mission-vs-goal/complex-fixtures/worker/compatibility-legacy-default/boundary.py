def normalise(payload):
    value = dict(payload); return {'state': value.get('state', 'unknown'), 'version': value.get('version', 1)}
