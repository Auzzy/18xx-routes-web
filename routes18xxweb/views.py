import collections
import json
import os

from flask import Blueprint, g, jsonify, render_template, request, url_for
from flask_mail import Message
from rq import Queue

from routes18xx import board, boardstate, boardtile, find_best_routes, game, railroads, tiles, LOG as LIB_LOG
from routes18xx.cell import _CELL_DB, Cell, board_cells, initialize_cells
from routes18xx.games.routes1846 import private_companies

from routes18xxweb.routes18xxweb import app, get_data_file, mail
from routes18xxweb.calculator import redis_conn
from routes18xxweb.logger import get_logger, init_logger, set_log_format

game_app = Blueprint('game_app', __name__)

LOG = get_logger("routes18xxweb")
init_logger(LOG, "APP_LOG_LEVEL")
set_log_format(LOG)

init_logger(LIB_LOG, "LIB_LOG_LEVEL", 0)
set_log_format(LIB_LOG)

CHICAGO_CELL = Cell.from_coord("D6")

CALCULATOR_QUEUE = Queue(connection=redis_conn)

MESSAGE_BODY_FORMAT = "User: {user}\nComments:\n{comments}"
TILE_MESSAGE_BODY_FORMAT = MESSAGE_BODY_FORMAT + "\nSelected:\n\tcoordinate: {coord}\n\ttile: {tile_id}\n\torientation: {orientation}"

CHICAGO_STATION_SIDES = (0, 3, 4, 5)
CHICAGO_STATION_COORDS = collections.OrderedDict([(str(CHICAGO_CELL.neighbors[side]), side) for side in CHICAGO_STATION_SIDES])
RAILROAD_NAMES = {
    "Baltimore & Ohio",
    "Illinois Central",
    "New York Central",
    "Chesapeake & Ohio",
    "Erie",
    "Grand Trunk",
    "Pennsylvania"
}

RAILROADS_COLUMN_MAP = {
    "name": "name",
    "trains": "trains",
    "stations": "stations"
}

PRIVATE_COMPANY_COLUMN_MAP = {
    "name": "name",
    "owner": "owner",
    "coord": "token coordinate"
}

PLACED_TILES_COLUMN_MAP = {
    "coord": "coordinate",
    "tile_id": "tile",
    "orientation": "orientation"
}

PRIVATE_COMPANIES = (
    "Steamboat Company",
    "Meat Packing Company",
    "Mail Contract",
    "Big 4",
    "Michigan Southern"
)

RAILROADS_COLUMN_NAMES = [RAILROADS_COLUMN_MAP[colname] for colname in railroads.FIELDNAMES]
PLACED_TILES_COLUMN_NAMES = [PLACED_TILES_COLUMN_MAP[colname] for colname in boardstate.FIELDNAMES]
PRIVATE_COMPANY_COLUMN_NAMES = [PRIVATE_COMPANY_COLUMN_MAP[colname] for colname in private_companies.FIELDNAMES]

PRIVATE_COMPANY_COORDS = {
    "Steamboat Company": private_companies.STEAMBOAT_COORDS,
    "Meat Packing Company": private_companies.MEAT_PACKING_COORDS,
    "Mail Contract": [],
    "Big 4": [private_companies.HOME_CITIES["Big 4"]],
    "Michigan Southern": [private_companies.HOME_CITIES["Michigan Southern"]]
}

with open(get_data_file("stations.json")) as stations_file:
    STATION_DATA = json.load(stations_file)

with open(get_data_file("private-companies.json")) as private_company_file:
    PRIVATE_COMPANY_DATA = json.load(private_company_file)

with open(get_data_file("terminal-cities.json")) as terminal_cities_file:
    TERMINAL_CITY_DATA = json.load(terminal_cities_file)

_BASE_BOARD = board.Board.load()
_BOARD_TILES = boardtile.load()
_TILE_DICT = tiles._load_all()

_TILE_COORDS = []

@game_app.url_defaults
def add_game_name(endpoint, values):
    if "game_name" not in values:
        values["game_name"] = g.game_name

@game_app.url_value_preprocessor
def pull_game_name(endpoint, values):
    g.game_name = values.pop('game_name')


def get_tile_coords():
    global _TILE_COORDS

    if not _TILE_COORDS:
        tile_coords = []
        for row, cols in sorted(_CELL_DB.items()):
            for col in sorted(cols):
                coord = f"{row}{col}"
                space = _get_space(coord)
                # Explicitly allow I5 in order to allow placing stations from the map. Allowing all built-in phase 4
                # tiles to be clickable would require some more special casing, so I determined this is "better"...
                if not space or space.phase is not None or coord == "I5":
                    tile_coords.append(coord)
        _TILE_COORDS = tile_coords
    return _TILE_COORDS

@app.route("/")
def game_picker():
    return "placeholder"

@game_app.route("/")
def main():
    city_names = {}
    for cell in board_cells():
        space = _BASE_BOARD.get_space(cell)
        if space and space.name != str(cell):
            # Its such a long name that it makes layout trickier, and looks
            # worse in comparison to others city names.
            name = "Chicago Conn." if space.name == "Chicago Connections" else space.name
            city_names[str(cell)] = name

    terminal_city_boundaries = {name: info["boundaries"] for name, info in TERMINAL_CITY_DATA.items()}

    return render_template("index.html",
            railroads_colnames=RAILROADS_COLUMN_NAMES,
            independent_railroad_home_cities=private_companies.HOME_CITIES,
            private_company_rownames=PRIVATE_COMPANIES,
            private_company_colnames=PRIVATE_COMPANY_COLUMN_NAMES,
            placed_tiles_colnames=PLACED_TILES_COLUMN_NAMES,
            tile_coords=get_tile_coords(),
            city_names=city_names,
            terminal_city_boundaries=terminal_city_boundaries)

@game_app.route("/calculate", methods=["POST"])
def calculate():
    railroads_state_rows = json.loads(request.form.get("railroads-json"))
    removed_railroads = json.loads(request.form.get("removed-railroads-json"))
    private_companies_rows = json.loads(request.form.get("private-companies-json"))
    board_state_rows = json.loads(request.form.get("board-state-json"))
    railroad_name = request.form["railroad-name"]

    for row in railroads_state_rows:
        if row[3]:
            split_branch_map = json.loads(row[3])
            stations_strs = []
            for coord in row[2].split(","):
                if coord not in split_branch_map:
                    stations_strs.append(coord)
                else:
                    if isinstance(split_branch_map[coord], list):
                        stations_strs.append(f"{coord}: [{' '.join(split_branch_map[coord])}]")
                    else:
                        stations_strs.append(f"{coord}: {split_branch_map[coord]}")
            row[2] = ','.join(stations_strs)
            del row[3]

    LOG.info("Calculate request.")
    LOG.info(f"Target railroad: {railroad_name}")
    LOG.info(f"Private companies: {private_companies_rows}")
    LOG.info(f"Railroad input: {railroads_state_rows}")
    LOG.info(f"Removed railroads: {removed_railroads}")
    LOG.info(f"Board input: {board_state_rows}")

    railroads_state_rows += [[name, "removed"] for name in removed_railroads]

    job = CALCULATOR_QUEUE.enqueue(calculate_worker, g.game_name, railroads_state_rows, private_companies_rows, board_state_rows, railroad_name, timeout="5m")

    return jsonify({"jobId": job.id})

@game_app.route("/calculate/result")
def calculate_result():
    routes_json = _get_calculate_result(request.args.get("jobId"))

    LOG.info(f"Calculate response: {routes_json}")

    return jsonify(routes_json)

def _get_calculate_result(job_id):
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

@game_app.route("/calculate/cancel", methods=["POST"])
def cancel_calculate_request():
    job_id = request.form.get("jobId")
    job = CALCULATOR_QUEUE.fetch_job(job_id)
    if job:
        job.delete()
    return jsonify({})

def calculate_worker(game_name, railroads_state_rows, private_companies_rows, board_state_rows, railroad_name):
    game = game.Game.load(game_name)
    board_state = boardstate.load(game, [dict(zip(boardstate.FIELDNAMES, row)) for row in board_state_rows if any(val for val in row)])
    railroad_dict = railroads.load(game, board_state, [dict(zip(railroads.FIELDNAMES, row)) for row in railroads_state_rows if any(val for val in row)])
    game.capture_phase(railroad_dict)

    private_companies.load(game, board_state, railroad_dict, [dict(zip(private_companies.FIELDNAMES, row)) for row in private_companies_rows if any(val for val in row)])
    board_state.validate()

    if railroad_name not in railroad_dict:
        valid_railroads = ", ".join(railroad_dict.keys())
        raise ValueError(f"Railroad chosen: \"{railroad_name}\". Valid railroads: {valid_railroads}")

    return find_best_routes(game, board_state, railroad_dict, railroad_dict[railroad_name])

def _get_space(coord):
    for tile in _BOARD_TILES:
        if str(tile.cell) == coord:
            return tile

def _legal_tile_ids_by_coord(coord):
    space = _get_space(coord)
    # If the coord is a built-in phase 4 tile
    if space and space.phase is None:
        return []

    legal_tile_ids = []
    for tile in _TILE_DICT.values():
        if not space:
            if tile.is_city or tile.is_z or tile.is_chicago:
                continue
        elif tile.phase <= space.phase:
            continue
        elif space.is_city != tile.is_city or space.is_z != tile.is_z or space.is_chicago != tile.is_chicago:
            continue

        if _get_orientations(coord, tile.id):
            legal_tile_ids.append(tile.id)

    return legal_tile_ids

def _get_orientations(coord, tile_id):
    if not coord or not tile_id:
        return None

    try:
        cell = Cell.from_coord(coord)
    except ValueError:
        return None

    tile = tiles.get_tile(tile_id)
    if not tile:
        return None

    orientations = []
    for orientation in range(0, 6):
        try:
            _BASE_BOARD._validate_place_tile_neighbors(cell, tile, orientation)
            _BASE_BOARD._validate_place_tile_upgrade(_get_space(coord), cell, tile, orientation)
        except ValueError:
            continue

        orientations.append(orientation)

    return orientations

@game_app.route("/board/tile-coords")
def legal_tile_coords():
    LOG.info("Legal tile coordinates request.")

    current_coord = request.args.get("coord")
    existing_tile_coords = {coord for coord in json.loads(request.args.get("tile_coords")) if coord}

    legal_tile_coordinates = set(get_tile_coords()) - existing_tile_coords
    if current_coord:
        legal_tile_coordinates.add(current_coord)

    LOG.info(f"Legal tile coordinates response: {legal_tile_coordinates}")

    return jsonify({"tile-coords": list(sorted(legal_tile_coordinates))})

@game_app.route("/board/tile-image")
def board_tile_image():
    tile_id = request.args.get("tileId")
    return url_for('static', filename='images/tiles/{:03}'.format(int(tile_id)))

@game_app.route("/board/legal-tiles")
def legal_tiles():
    coord = request.args.get("coord")

    LOG.info(f"Legal tiles request for {coord}.")

    legal_tile_ids = _legal_tile_ids_by_coord(coord)
    legal_tile_ids.sort()

    LOG.info(f"Legal tiles response for {coord}: {legal_tile_ids}")

    return jsonify({"legal-tile-ids": legal_tile_ids})

@game_app.route("/board/legal-orientations")
def legal_orientations():
    coord = request.args.get("coord")
    tile_id = request.args.get("tileId")

    LOG.info(f"Legal orientations request for {tile_id} at {coord}.")

    orientations = _get_orientations(coord, tile_id)

    LOG.info(f"Legal orientations response for {tile_id} at {coord}: {orientations}")

    return jsonify({"legal-orientations": list(sorted(orientations)) if orientations is not None else orientations})

@game_app.route("/board/tile-info")
def board_tile_info():
    coord = request.args.get("coord")
    chicago_neighbor = request.args.get("chicagoNeighbor")
    tile_id = request.args.get("tileId")

    tile = tiles.get_tile(tile_id) if tile_id else _BASE_BOARD.get_space(Cell.from_coord(coord))

    default_offset = {"x": 0, "y": 0}
    offset_data = STATION_DATA["tile"] if tile_id else STATION_DATA["board"]
    offset = offset_data.get(coord, {}).get("offset", default_offset)
    if chicago_neighbor:
        offset = offset[chicago_neighbor]

    info = {
        "capacity": tile.capacity,
        "offset": offset,
        "phase": tile.phase
    }

    return jsonify({"info": info})

@game_app.route("/board/private-company-info")
def board_private_company_info():
    coord = request.args.get("coord")
    company = request.args.get("company")
    phase = request.args.get("phase")

    default_offset = {"x": 0, "y": 0}
    offset_data = PRIVATE_COMPANY_DATA[company]
    offset = offset_data.get(coord, {}).get("offset", default_offset)

    if coord == str(CHICAGO_CELL):
        offset = offset.get(phase, default_offset) if phase and phase in offset else offset.get("default", default_offset)

    info = {
        "offset": offset
    }

    return jsonify({"info": info})

@game_app.route("/board/phase")
def board_phase():
    LOG.info("Phase request")

    train_strs = json.loads(request.args.get("trains"))
    if train_strs:
        phases = []
        for train_str in train_strs:
            try:
                phases.append(railroads.Train.create(train_str).phase)
            except ValueError:
                continue
        phase = max(phases)
    else:
        phase = 1

    LOG.info(f"Phase: {phase}")

    return jsonify({"phase": phase})

@game_app.route("/railroads/legal-railroads")
def legal_railroads():
    LOG.info("Legal railroads request.")

    existing_railroads = {railroad for railroad in json.loads(request.args.get("railroads", "{}")) if railroad}

    legal_railroads = RAILROAD_NAMES - existing_railroads

    LOG.info(f"Legal railroads response: {legal_railroads}")

    return jsonify({
        "railroads": list(sorted(legal_railroads)),
        "home-cities": {railroad: railroads.RAILROAD_HOME_CITIES[railroad] for railroad in legal_railroads}
    })

@game_app.route("/railroads/removable-railroads")
def removable_railroads():
    LOG.info("Removable railroads request.")

    existing_railroads = {railroad for railroad in json.loads(request.args.get("railroads", "{}")) if railroad}

    removable_railroads = railroads.REMOVABLE_RAILROADS - existing_railroads

    LOG.info(f"Removable railroads response: {removable_railroads}")

    return jsonify({
        "railroads": list(sorted(removable_railroads)),
        "home-cities": {railroad: railroads.RAILROAD_HOME_CITIES[railroad] for railroad in removable_railroads}
    })

@game_app.route("/railroads/trains")
def trains():
    LOG.info("Train request.")

    all_trains = [railroads.Train(train_attr[0], train_attr[1], phase) for train_attr, phase in railroads.TRAIN_TO_PHASE.items()]
    train_strs = [str(train) for train in sorted(all_trains, key=lambda train: (train.collect, train.visit))]

    LOG.info(f"Train response: {all_trains}")

    return jsonify({"trains": train_strs})

@game_app.route("/railroads/cities")
def cities():
    LOG.info("Cities request.")

    all_cities = [str(tile.cell) for tile in sorted(_BOARD_TILES, key=lambda tile: tile.cell) if tile.is_city and not tile.is_terminal_city]

    LOG.info(f"Cities response: {all_cities}")

    return jsonify({"cities": all_cities})

@game_app.route("/railroads/legal-chicago-stations")
def chicago_stations():
    LOG.info("Legal Chicago stations request.")

    existing_station_coords = {coord for coord in json.loads(request.args.get("stations", "{}")) if coord}

    legal_stations = list(sorted(set(CHICAGO_STATION_COORDS.keys()) - existing_station_coords))

    LOG.info(f"Legal Chicago stations response: {legal_stations}")

    return jsonify({"chicago-stations": legal_stations})

@game_app.route("/railroads/legal-token-coords")
def legal_token_coords():
    company_name = request.args.get("companyName")

    LOG.info(f"Legal {company_name} token coordinate request.")

    coords = PRIVATE_COMPANY_COORDS.get(company_name)
    if coords is None:
        raise ValueError(f"Received unsupport private company name: {company_name}")

    LOG.info(f"Legal {company_name} token coordinate response: {coords}")

    return jsonify({"coords": coords})


def _build_general_message():
    railroad_headers = json.loads(request.form.get("railroadHeaders"))
    railroads_data = json.loads(request.form.get("railroadData"))
    private_companies_headers = json.loads(request.form.get("privateCompaniesHeaders"))
    private_companies_data = json.loads(request.form.get("privateCompaniesData"))
    placed_tiles_headers = json.loads(request.form.get("placedTilesHeaders"))
    placed_tiles_data = json.loads(request.form.get("placedTilesData"))
    user_email = request.form.get("email")
    user_comments = request.form.get("comments")
    email_subject = request.form.get("subject")

    railroads_json = [dict(zip(railroad_headers, row)) for row in railroads_data if any(row)]
    private_companies_json = [dict(zip(private_companies_headers, row)) for row in private_companies_data]
    placed_tiles_json = [dict(zip(placed_tiles_headers, row)) for row in placed_tiles_data if any(row)]

    msg = Message(
        body=MESSAGE_BODY_FORMAT.format(user=user_email, comments=user_comments),
        subject=email_subject,
        sender=app.config.get("MAIL_USERNAME"),
        recipients=[os.environ["BUG_REPORT_EMAIL"]])

    msg.attach("railroads.json", "application/json", json.dumps(railroads_json, indent=4, sort_keys=True))
    msg.attach("private-companies.json", "application/json", json.dumps(private_companies_json, indent=4, sort_keys=True))
    msg.attach("placed-tiles.json", "application/json", json.dumps(placed_tiles_json, indent=4, sort_keys=True))

    return msg

@game_app.route("/report/general-issue", methods=["POST"])
def report_general_issue():
    mail.send(_build_general_message())
    return ""

@game_app.route("/report/calc-issue", methods=["POST"])
def report_calc_issue():
    target_railroad = request.form.get("targetRailroad")
    job_id = request.form.get("jobId")
    result_html = request.form.get("resultHtml")
    hide_cities = request.form.get("hideCities")

    msg = _build_general_message()

    routes_json = _get_calculate_result(job_id)
    routes_json.update({
          "jobId": job_id,
          "resultHtml": result_html,
          "hideCities": hide_cities
    })

    msg.attach("routes.json", "application/json", json.dumps({target_railroad: routes_json}, indent=4, sort_keys=True))

    mail.send(msg)

    return ""

@game_app.route("/report/tile-issue", methods=["POST"])
def report_tile_issue():
    placed_tiles_headers = json.loads(request.form.get("placedTilesHeaders"))
    placed_tiles_data = json.loads(request.form.get("placedTilesData"))
    coord = request.form.get("coord")
    tile_id = request.form.get("tileId")
    orientation = request.form.get("orientation")
    tiles_json = json.loads(request.form.get("tiles"))
    orientations_json = json.loads(request.form.get("orientations"))
    user_email = request.form.get("email")
    user_comments = request.form.get("comments")
    email_subject = request.form.get("subject")

    message_body = TILE_MESSAGE_BODY_FORMAT.format(
        user=user_email, comments=user_comments, coord=coord, tile_id=tile_id, orientation=orientation)

    msg = Message(
        body=message_body,
        subject=email_subject,
        sender=app.config.get("MAIL_USERNAME"),
        recipients=[os.environ["BUG_REPORT_EMAIL"]])

    placed_tiles_json = [dict(zip(placed_tiles_headers, row)) for row in placed_tiles_data if any(row)]

    msg.attach("placed-tiles.json", "application/json", json.dumps(placed_tiles_json, indent=4, sort_keys=True))
    msg.attach("tiles.json", "application/json", json.dumps(tiles_json, indent=4, sort_keys=True))
    msg.attach("orientations.json", "application/json", json.dumps(orientations_json, indent=4, sort_keys=True))
    mail.send(msg)

    return ""

app.register_blueprint(game_app, url_prefix='/game/<game_name>')