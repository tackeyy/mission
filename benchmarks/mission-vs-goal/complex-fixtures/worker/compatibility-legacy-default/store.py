class StateStore:
    def __init__(self): self.value = None
    def save(self, value): self.value = dict(value)
    def snapshot(self): return dict(self.value)
