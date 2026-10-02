def normalise(payload):
    value = dict(payload); version = value.get('version', 1)
    default = 'open' if version == 1 else 'pending'
    return {'state': value.get('state', default), 'version': version}
