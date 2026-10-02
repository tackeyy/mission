from threading import Lock


class WinnerStore:
    def __init__(self):
        self._winner = None
        self._lock = Lock()

    def register(self, candidate):
        with self._lock:
            if self._winner is None or (candidate["rank"], candidate["id"]) < (self._winner["rank"], self._winner["id"]):
                self._winner = candidate

    def snapshot(self):
        with self._lock:
            return dict(self._winner) if self._winner is not None else None
