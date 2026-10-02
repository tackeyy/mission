from service import execute
assert execute({'payload': {'amount': 1, 'unit': 'minor'}})['minor_total'] == 1
