class DeliveryStore:
    def __init__(self, accepted, fail_once): self.accepted = set(accepted); self.fail_once = set(fail_once); self.attempts = []
    def send(self, identifier):
        if identifier in self.accepted: return
        self.attempts.append(identifier)
        if identifier in self.fail_once: self.fail_once.remove(identifier); return
        self.accepted.add(identifier)
