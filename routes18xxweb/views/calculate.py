import json

from flask import g, jsonify, request
from rq import Queue

from routes18xx import boardstate, find_best_routes, railroads

from routes18xxweb.calculator import redis_conn
from routes18xxweb.games import get_game
from routes18xxweb.routes18xxweb import game_app
from routes18xxweb.views import LOG

CALCULATOR_QUEUE = Queue(connection=redis_conn)

@game_app.route("/calculate", methods=["POST"])
def calculate():
    railroads_state_rows = json.loads(request.form.get("railroads-json"))
    removed_railroads = json.loads(request.form.get("removed-railroads-json"))
    private_companies_rows = json.loads(request.form.get("private-companies-json"))
    board_state_rows = json.loads(request.form.get("board-state-json"))
    railroad_name = request.form["railroad-name"]

    LOG.info("Calculate request.")
    LOG.info(f"Target railroad: {railroad_name}")
    LOG.info(f"Private companies: {private_companies_rows}")
    LOG.info(f"Railroad input: {railroads_state_rows}")
    LOG.info(f"Removed railroads: {removed_railroads}")
    LOG.info(f"Board input: {board_state_rows}")

    railroads_state_rows += [[name, "removed"] for name in removed_railroads]

    job = CALCULATOR_QUEUE.enqueue(calculate_worker, g.game_name, railroads_state_rows, private_companies_rows, board_state_rows, railroad_name, job_timeout="5m")

    return jsonify({"jobId": job.id})

@game_app.route("/calculate/result")
def calculate_result():
    routes_json = get_calculate_result(request.args.get("jobId"))

    LOG.info(f"Calculate response: {routes_json}")

    return jsonify(routes_json)

@game_app.route("/calculate/cancel", methods=["POST"])
def cancel_calculate_request():
    job_id = request.form.get("jobId")

    job = CALCULATOR_QUEUE.fetch_job(job_id)
    if job:
        job.delete()
    return jsonify({})

def get_calculate_result(job_id):
    routes_json = {}

    job = CALCULATOR_QUEUE.fetch_job(job_id)
    # If job is None, it means the job ID couldn't be found, either because it's invalid, or the job was cancelled.
    if job:
        if job.is_failed:
            # The job experienced an error
            if not job.exc_info:
                # The error info hasn't propagated yet, so act as if the job is still in progress
                routes_json["jobId"] = job_id
            else:
                exc_info = json.loads(job.exc_info)
                routes_json["error"] = {
                    "message": f"An error occurred during route calculation: {exc_info['message']}",
                    "traceback": exc_info["traceback"]
                }

        elif job.is_finished:
            routes_json["routes"] = []
            for route in job.result:
                routes_json["routes"].append([
                    str(route.train),
                    [str(space.cell) for space in route],
                    route.value,
                    [(stop.name, route.stop_values[stop]) for stop in route.visited_stops]
                ])
        else:
            # The job is in progress
            routes_json["jobId"] = job_id

    return routes_json

def calculate_worker(game_name, railroads_state_rows, private_companies_rows, board_state_rows, railroad_name):
    game = get_game(game_name)
    board_state = boardstate.load(game, [dict(zip(boardstate.FIELDNAMES, row)) for row in board_state_rows if any(val for val in row)])
    railroad_dict = railroads.load(game, board_state, [dict(zip(railroads.FIELDNAMES, row)) for row in railroads_state_rows if any(val for val in row)])
    game.capture_phase(railroad_dict)

    private_companies = game.get_game_submodule("private_companies")
    if private_companies:
        private_companies.load(game, board_state, railroad_dict, [dict(zip(private_companies.FIELDNAMES, row)) for row in private_companies_rows if any(val for val in row)])
    board_state.validate()

    if railroad_name not in railroad_dict:
        valid_railroads = ", ".join(railroad_dict.keys())
        raise ValueError(f"Railroad chosen: \"{railroad_name}\". Valid railroads: {valid_railroads}")

    return find_best_routes(game, board_state, railroad_dict, railroad_dict[railroad_name])
