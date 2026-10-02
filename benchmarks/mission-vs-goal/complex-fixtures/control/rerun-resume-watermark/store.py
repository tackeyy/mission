class WatermarkStore:
    def __init__(self, state): self.watermark = state.get('watermark', -1); self.processed = list(state.get('processed', []))
    def apply(self, event):
        if event['offset'] > self.watermark: self.processed.append(event['id']); self.watermark = event['offset']
    def snapshot(self): return {'watermark': self.watermark, 'processed': list(self.processed)}
