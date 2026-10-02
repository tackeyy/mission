from threading import Barrier, Thread

from boundary import normalise
from store import Counter


def execute(request):
    initial, deltas = normalise(request)
    counter = Counter(initial)
    ready = Barrier(2)
    threads = [Thread(target=counter.update, args=(delta, ready)) for delta in deltas]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)
    return {"final_state": counter.snapshot(), "threads_completed": sum(not thread.is_alive() for thread in threads)}
