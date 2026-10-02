from service import execute

assert execute({"candidates": [{"id": "x", "rank": 1}, {"id": "y", "rank": 2}], "arrival_order": ["x", "y"]})["threads_completed"] == 2
