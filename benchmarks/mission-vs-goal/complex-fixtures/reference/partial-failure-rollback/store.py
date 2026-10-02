class TransactionStore:
    def __init__(self, records): self.records = dict(records)
    def apply(self, writes, fail_after):
        working = dict(self.records)
        try:
            for index, write in enumerate(writes):
                working[write['key']] = write['value']
                if index == fail_after: raise RuntimeError('injected')
        except RuntimeError:
            return False
        self.records = working; return True
    def snapshot(self): return dict(self.records)
