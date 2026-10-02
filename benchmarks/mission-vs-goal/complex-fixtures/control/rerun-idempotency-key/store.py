class EffectStore:
    def __init__(self, state): self.effects = dict(state)
    def apply(self, event):
        self.effects.setdefault(event['id'], event['amount'])
    def snapshot(self): return dict(self.effects)
