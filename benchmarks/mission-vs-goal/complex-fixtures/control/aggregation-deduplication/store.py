class Aggregate:
    def __init__(self): self.entries = {}
    def apply(self, event): self.entries.setdefault(event['id'], event['amount'])
    def snapshot(self): return {'entries': dict(self.entries), 'total': sum(self.entries.values())}
