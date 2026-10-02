class TransactionStore:
    def __init__(self, records): self.records = dict(records)
    def apply(self, writes, fail_after):
        try:
            for index, write in enumerate(writes):
                self.records[write['key']] = write['value']
                if index == fail_after: raise RuntimeError('injected')
        except RuntimeError:
            self.records.clear(); return False
        return True
    def snapshot(self): return dict(self.records)
