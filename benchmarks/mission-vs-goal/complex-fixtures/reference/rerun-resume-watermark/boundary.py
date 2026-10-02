def normalise(request): return {'state': dict(request.get('state', {})), 'runs': list(request.get('runs', []))}
