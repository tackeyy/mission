class Store:
    def __init__(self): self.items = {}; self.sent = []; self.total = 0
    def snapshot(self): return {'items': self.items, 'sent': self.sent, 'total': self.total}
