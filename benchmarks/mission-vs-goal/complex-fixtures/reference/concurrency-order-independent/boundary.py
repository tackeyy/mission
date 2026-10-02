def normalise(request):
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    candidates = request.get("candidates")
    arrival_order = request.get("arrival_order")
    if not isinstance(candidates, list) or len(candidates) != 2 or not isinstance(arrival_order, list):
        raise ValueError("two candidates and an arrival order are required")
    parsed = [{"id": str(candidate["id"]), "rank": int(candidate["rank"])} for candidate in candidates]
    by_id = {candidate["id"]: candidate for candidate in parsed}
    if len(by_id) != 2 or set(arrival_order) != set(by_id):
        raise ValueError("arrival order must name each candidate once")
    return tuple(by_id[candidate_id] for candidate_id in arrival_order)
