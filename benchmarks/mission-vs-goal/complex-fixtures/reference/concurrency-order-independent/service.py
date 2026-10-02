from threading import Barrier, Event, Thread

from boundary import normalise
from store import WinnerStore


def execute(request):
    candidates = normalise(request)
    winner = WinnerStore()
    ready = Barrier(2)
    first_arrived = Event()

    def register(candidate, position):
        ready.wait()
        if position:
            first_arrived.wait()
        winner.register(candidate)
        if not position:
            first_arrived.set()

    threads = [Thread(target=register, args=(candidate, position)) for position, candidate in enumerate(candidates)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)
    return {"winner": winner.snapshot(), "threads_completed": sum(not thread.is_alive() for thread in threads)}
