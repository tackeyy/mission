class Aggregate:
    def __init__(self): self.entries = {}; self.total = 0
    def apply(self, event): self.entries[event['id']] = event['amount']; self.total += event['amount']
    def snapshot(self): return {'entries': dict(self.entries), 'total': self.total}
