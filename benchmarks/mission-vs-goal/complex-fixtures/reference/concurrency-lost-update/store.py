from threading import Lock


class Counter:
    def __init__(self, initial):
        self._value = initial
        self._lock = Lock()

    def update(self, delta, ready):
        ready.wait()
        with self._lock:
            self._value += delta

    def snapshot(self):
        with self._lock:
            return self._value
