class Cache:
    def __init__(self): self.values = {}
    def read(self, storage, key):
        if key not in self.values: self.values[key] = storage.read(key)
        return self.values[key]
    def invalidate(self, key): self.values.pop(key, None)
