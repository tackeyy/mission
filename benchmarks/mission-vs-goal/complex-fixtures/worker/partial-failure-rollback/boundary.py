def normalise(request): return {'existing': dict(request.get('existing', {})), 'writes': list(request.get('writes', [])), 'fail_after': request.get('fail_after')}
