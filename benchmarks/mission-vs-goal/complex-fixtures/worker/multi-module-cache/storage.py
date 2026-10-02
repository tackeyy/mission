class Storage:
    def __init__(self, records): self.records = dict(records)
    def read(self, key): return self.records.get(key)
    def write(self, key, value): self.records[key] = value
    def snapshot(self): return dict(self.records)
