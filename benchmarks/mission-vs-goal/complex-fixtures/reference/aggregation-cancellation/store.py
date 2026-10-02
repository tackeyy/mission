class Aggregate:
    def __init__(self): self.entries = {}
    def apply(self, event):
        if event['kind'] == 'add': self.entries[event['id']] = event['amount']
        elif event['kind'] == 'cancel': self.entries.pop(event['id'], None)
    def snapshot(self): return {'entries': dict(self.entries), 'total': sum(self.entries.values())}
