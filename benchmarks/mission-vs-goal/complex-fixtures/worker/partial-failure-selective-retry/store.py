class DeliveryStore:
    def __init__(self, accepted, fail_once): self.accepted = set(accepted); self.fail_once = set(fail_once); self.attempts = []
    def send(self, identifier):
        self.attempts.append(identifier)
        if identifier not in self.fail_once: self.accepted.add(identifier)
        self.fail_once.discard(identifier)
