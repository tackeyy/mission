def normalise(payload):
    value = dict(payload)
    return {'priority': value.get('priority', value.get('urgency', 'normal'))}
