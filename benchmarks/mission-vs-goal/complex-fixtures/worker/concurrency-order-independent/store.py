class WinnerStore:
    def __init__(self):
        self._winner = None

    def register(self, candidate):
        self._winner = candidate

    def snapshot(self):
        return dict(self._winner) if self._winner is not None else None
