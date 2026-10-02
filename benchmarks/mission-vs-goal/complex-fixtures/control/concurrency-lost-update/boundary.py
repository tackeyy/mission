def normalise(request):
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    deltas = request.get("deltas")
    if not isinstance(deltas, list) or len(deltas) != 2:
        raise ValueError("two deltas are required")
    return int(request.get("initial", 0)), tuple(int(delta) for delta in deltas)
