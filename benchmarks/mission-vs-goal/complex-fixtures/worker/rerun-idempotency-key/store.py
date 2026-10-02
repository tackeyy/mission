class EffectStore:
    def __init__(self, state): self.effects = dict(state)
    def apply(self, event): self.effects[event['id']] = self.effects.get(event['id'], 0) + event['amount']
    def snapshot(self): return dict(self.effects)
