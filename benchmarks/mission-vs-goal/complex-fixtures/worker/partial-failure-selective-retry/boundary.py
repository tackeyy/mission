def normalise(request): return {'accepted': list(request.get('accepted', [])), 'rounds': list(request.get('rounds', [])), 'fail_once': set(request.get('fail_once', []))}
