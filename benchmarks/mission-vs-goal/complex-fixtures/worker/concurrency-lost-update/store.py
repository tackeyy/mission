class Counter:
    def __init__(self, initial):
        self._value = initial

    def update(self, delta, ready):
        observed = self._value
        ready.wait()
        self._value = observed + delta

    def snapshot(self):
        return self._value
