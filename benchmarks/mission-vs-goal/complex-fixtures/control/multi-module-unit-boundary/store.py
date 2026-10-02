class Ledger:
    def __init__(self): self.minor = 0
    def post(self, value): self.minor += value
