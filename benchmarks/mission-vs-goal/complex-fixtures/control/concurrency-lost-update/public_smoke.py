from service import execute

assert execute({"initial": 0, "deltas": [0, 0]})["threads_completed"] == 2
