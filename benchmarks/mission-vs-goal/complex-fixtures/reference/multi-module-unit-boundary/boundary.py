def to_minor(payload):
    amount = payload['amount']
    return int(round(amount * 100)) if payload['unit'] == 'major' else int(amount)
