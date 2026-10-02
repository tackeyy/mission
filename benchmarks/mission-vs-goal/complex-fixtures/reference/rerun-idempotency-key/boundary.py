def normalise(request): return {'state': dict(request.get('state', {})), 'calls': list(request.get('calls', []))}
