import base64
import collections
import math
import json
import os
import uuid

import redis
from flask import abort, Blueprint, g, jsonify, redirect, render_template, request, session, url_for
from rq import Queue
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Attachment

from routes18xx import (boardstate, boardtile, find_best_routes, railroads, \
    placedtile, tiles, trains as trains_mod, LOG as LIB_LOG)

from routes18xxweb.routes18xxweb import app, csrf
from routes18xxweb.calculator import redis_conn
from routes18xxweb.games import (get_board, get_board_layout, get_game, \
    get_private_offsets, get_railroad_info, get_station_offsets, \
    get_supported_game_info, get_termini_boundaries, get_train_info, \
    _get_placement_info)

from routes18xxweb.logger import get_logger, init_logger, set_log_format

game_app = Blueprint('game_app', __name__)

GAME_APP_ROOT = "/game"
UNSUPPORTED_GAME_MESSAGE = "unsupported game"

LOG = get_logger("routes18xxweb")
init_logger(LOG, "APP_LOG_LEVEL")
set_log_format(LOG)

init_logger(LIB_LOG, "LIB_LOG_LEVEL", 0)
set_log_format(LIB_LOG)

CALCULATOR_QUEUE = Queue(connection=redis_conn)

MESSAGE_BODY_FORMAT = "User: {user}\nComments:\n{comments}\nPhase: {phase}"
TILE_MESSAGE_BODY_FORMAT = MESSAGE_BODY_FORMAT + "\nSelected:\n\tcoordinate: {coord}\n\ttile: {tile_id}\n\torientation: {orientation}"

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

RAILROADS_COLUMN_NAMES = [RAILROADS_COLUMN_MAP[colname] for colname in railroads.FIELDNAMES]
PLACED_TILES_COLUMN_NAMES = [PLACED_TILES_COLUMN_MAP[colname] for colname in boardstate.FIELDNAMES]


@app.errorhandler(404)
def page_not_found(exc):
    if exc.description == UNSUPPORTED_GAME_MESSAGE:
        return render_template("errors/game-unsupported.html"), 404
    return render_template("errors/generic-404.html"), 404

@game_app.url_defaults
def add_game_name(endpoint, values):
    if "game_name" not in values:
        values["game_name"] = g.game_name

@game_app.url_value_preprocessor
def pull_game_name(endpoint, values):
    g.game_name = values.pop('game_name')
    if g.game_name not in get_supported_game_info():
        abort(404, description=UNSUPPORTED_GAME_MESSAGE)


def get_tile_coords(board):
    tile_coords = []
    for cell in board.cells:
        space = board.get_space(cell)
        if not space or space.is_city or space.upgrade_level is not None:
            tile_coords.append(str(cell))
    return tile_coords

def _get_board_layout_info():
    board = get_board(g.game_name)
    board_layout = get_board_layout(g.game_name)

    first_coord = str(min(list(board.cells)))
    row, col = first_coord[0], int(first_coord[1:])
    board_layout.update({
        # Determine the parity of the first row's columns. 0 for even, 1 for odd.
        # If the first cell's row is not A, adjust the parity to what it would be
        # if it was A.
        "parity": (col + (0 if ord(row) % 2 == 1 else 1)) % 2,
        # Convert the max row, expressed as a letter, into an int.
        "max-row": ord(board_layout["max-row"].lower()) - 97
    })
    return board_layout

def _validate_migration_data(migration_data):
    if not isinstance(migration_data, dict):
        LOG.debug(f"Migration validation failure: migration_data is not a dict")
        return {"error": "Failed to load migration data."}

    game = get_game(g.game_name)
    railroad_info = get_railroad_info("1846")
    private_companies = game.get_game_submodule("private_companies")
    for key, value in migration_data.items():
        # Make sure no control characters or non-ASCII characters are present.
        if not key.isascii() or not key.isprintable() or not value.isascii() or not value.isprintable():
            LOG.debug(f"Migration failure: key or value is not printable ASCII")
            return False
        
        try:
            migration_data[key] = json.dumps(json.loads(value))
        except Exception as exc:
            LOG.debug(f"Migration validation failure[{key}]: value not valid JSON: {value}")
            return False

        if key == "placedTilesTable":
            tiles_json = json.loads(value)
            if not all(len(row) == 3 for row in tiles_json) or not all(col.isalnum() for row in tiles_json for col in row):
                LOG.debug(f"Migration validation failure[{key}]: {value}")
                return False
        elif key == "railroadsTable":
            railroads_json = json.loads(value)
            for row in railroads_json:
                if len(row) not in (3, 4) or row[0].strip() not in railroad_info:
                    LOG.debug(f"Migration validation failure[{key}]: row wrong length, or railroad name invalid: {value}")
                    return False
                if row[1] and row[1].strip():
                    train_strs = [train_str.strip().split("/", 1) for train_str in row[1].strip().split(",")]
                    if not all(val.strip().isdigit() for train_str in train_strs for val in train_str):
                        LOG.debug(f"Migration validation failure[{key}]: trains malformed: {value}")
                        return False
                if row[2] and row[2].strip():
                    if not all(station_str.strip().isalnum() for station_str in row[2].strip().split(",")):
                        LOG.debug(f"Migration validation failure[{key}]: stations malformed: {value}")
                        return False
        elif key == "removedRailroadsTable":
            removed_railroads = json.loads(value)
            for railroad in removed_railroads:
                if railroad.strip() not in railroad_info:
                    LOG.debug(f"Migration validation failure[{key}]: railroad name invalid: {value}")
                    return False
        elif key == "privateCompaniesTable":
            private_companies_json = json.loads(value)
            for row in private_companies_json:
                if len(row) != 3 \
                        or row[0].strip() not in private_companies.COMPANIES \
                        or (row[1] and row[1].strip() not in railroad_info) \
                        or (row[2] and not row[2].strip().isalnum()):
                    LOG.debug(f"Migration validation failure[{key}]: invalid row: {row}")
                    return False
        elif key == "hideCityPaths":
            if value not in ("true", "false"):
                LOG.debug(f"Migration validation failure[{key}]: {value}")
                return False
        else:
            LOG.debug(f"Migration validation failure: invalid key: {key}")
            return False

    return True

def _convert_migration_data(migration_data):
    converted_data = {
        "removedRailroadsTable": migration_data.get("removedRailroadsTable", ""),
        "privateCompaniesTable": migration_data.get("privateCompaniesTable", ""),
        "hideStopPaths": migration_data.get("hideCityPaths", "")
    }

    if "placedTilesTable" in migration_data:
        rotation_map = _get_placement_info(get_game("1846"), "rotation-map.json")
        converted_data["placedTilesTable"] = []
        for tile_row in json.loads(migration_data["placedTilesTable"]):
            converted_data["placedTilesTable"].append([
                tile_row[0],
                tile_row[1],
                str((int(tile_row[2]) - rotation_map[tile_row[1]]) % 6)
            ])
        converted_data["placedTilesTable"] = json.dumps(converted_data["placedTilesTable"])

    if "railroadsTable" in migration_data:
        converted_data["railroadsTable"] = []
        for railroad_row in json.loads(migration_data["railroadsTable"]):
            new_stations_coords = []
            for station_coord in railroad_row[2].split(','):
                new_stations_coords.append(f"{station_coord}:{railroad_row[3]}" if station_coord == "D6" else station_coord)
            converted_data["railroadsTable"].append([
                railroad_row[0],
                railroad_row[1],
                ",".join(new_stations_coords)
            ])
        converted_data["railroadsTable"] = json.dumps(converted_data["railroadsTable"])

    return converted_data

@game_app.route("/migrate/start", methods=["POST"])
@csrf.exempt
def start_migration():
    # The processing of migration_data is meant to disrupt an attacker trying
    # to poison the redis instance. As such, the error message should not give
    # them any details as to what went wrong.
    error_message = {"error": "Failed to start the migration."}

    LOG.info(f"Migration requested")

    migration_data = request.data
    if not migration_data:
        LOG.debug(f"Migration failure: no migration data provided")
        return jsonify(error_message), 400

    migration_data = migration_data.decode("utf-8")
    if not migration_data.strip().isprintable() or not migration_data.strip().isascii():
        LOG.debug(f"Migration failure: migration data not all printable ASCII")
        return jsonify(error_message), 400

    try:
        migration_data_json = json.loads(migration_data)
    except Exception as exc:
        LOG.debug(f"Migration failure: migration data is not valid JSON: {exc}")
        return jsonify(error_message), 400

    try:
        validation_error = _validate_migration_data(migration_data_json)
    except Exception as exc:
        LOG.debug(f"Migration failure: an exception occurred during validation: {exc}")
        return jsonify(error_message), 400

    if not validation_error:
        return jsonify(error_message), 400

    redis_id = str(uuid.uuid4())
    redis_key = f"migrate-{redis_id}"
    redis_conn.hmset(redis_key, migration_data_json)
    redis_conn.expire(redis_key, 60)

    LOG.info(f"Migration initiated: {redis_id}")

    return jsonify({"id": redis_id}), 201

@game_app.route("/migrate/complete")
def complete_migration():
    LOG.info(f"Migration continued")

    id = request.args.get("id")
    if not id:
        LOG.debug(f"Migration failure: no ID provided")
        return redirect(url_for('.main'))

    try:
        uuid.UUID(id)
    except:
        LOG.debug(f"Migration failure: invalid ID provided; expected UUID")
        return redirect(url_for('.main'))

    LOG.info(f"Migration continuing for {id}")

    migration_data = redis_conn.hgetall(f"migrate-{id}")
    if not migration_data:
        LOG.debug(f"Migration failure: no data to load")
        return redirect(url_for('.main'))

    # Once we get the data, we no longer need it, regardless of validity.
    redis_conn.delete(id)

    try:
        migration_data = {key.decode("ascii"): value.decode("ascii") for key, value in migration_data.items()}
    except UnicodeEncodeError as exc:
        LOG.debug(f"Migration failure: loaded data not all ASCII: {exc}")
        return redirect(url_for('.main'))

    if not _validate_migration_data(migration_data):
        return redirect(url_for('.main'))

    LOG.debug(f"Migration continuing: storing data in session")
    session["migration_data"] = json.dumps(migration_data)

    return redirect(url_for('.main'))

@app.route("/")
def game_picker():
    return render_template("game-selection.html", game_root=GAME_APP_ROOT, games=get_supported_game_info())

@app.route("/game/")
def incomplete_game_url_handler():
    return redirect(url_for("game_picker"))

@game_app.route("/")
def main():
    migration_data = json.loads(session.pop("migration_data", "{}"))
    if migration_data:
        LOG.debug(f"Migration finalizing: found session data.")
        if _validate_migration_data(migration_data):
            LOG.debug(f"Migration data pre-conversion: {migration_data}")
            migration_data = _convert_migration_data(migration_data)
            LOG.debug(f"Migration data post-conversion: {migration_data}")
        else:
            LOG.debug(f"Migration validation failed: continuing without migration")
            migration_data = {}

    game = get_game(g.game_name)
    board = get_board(game)

    stop_names = {}
    for cell in board.cells:
        space = board.get_space(cell)
        if space and space.is_stop:
            stop_names[str(cell)] = space.nickname

    termini_boundaries = {name: info["boundaries"] for name, info in get_termini_boundaries(game).items()}

    private_companies = game.get_game_submodule("private_companies")
    private_company_names = private_companies.COMPANIES.keys() if private_companies else []
    private_company_column_names = [PRIVATE_COMPANY_COLUMN_MAP[colname] for colname in private_companies.FIELDNAMES] if private_companies else []

    return render_template("index.html",
            railroads_colnames=RAILROADS_COLUMN_NAMES,
            private_company_default_token_coords=private_companies.PRIVATE_COMPANY_DEFAULT_COORDS if private_companies else {},
            private_company_rownames=private_company_names,
            private_company_colnames=private_company_column_names,
            placed_tiles_colnames=PLACED_TILES_COLUMN_NAMES,
            tile_coords=get_tile_coords(board),
            stop_names=stop_names,
            termini_boundaries=termini_boundaries,
            removable_railroads=_get_removable_railroads(),
            board_layout=_get_board_layout_info(),
            migration_data=migration_data)

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

def _legal_tile_ids_by_coord(game, coord):
    board = get_board(game)
    space = board.get_space(board.cell(coord))
    # If the coord is a built-in upgrade level 4 tile
    if space and space.upgrade_level is None:
        return []

    legal_tile_ids = []
    for tile in game.tiles.values():
        if not space:
            if tile.is_stop:
                continue
        elif tile.upgrade_level <= space.upgrade_level:
            continue
        elif space.is_city != tile.is_city or space.is_town != tile.is_town or space.upgrade_attrs != tile.upgrade_attrs:
            continue

        legal_orientations, _ = _get_orientations(game, coord, tile.id)
        if legal_orientations:
            legal_tile_ids.append(tile.id)

    return legal_tile_ids

def _get_orientations(game, coord, tile_id):
    if not coord or not tile_id:
        return None, None

    board = get_board(game)

    try:
        cell = board.cell(coord)
    except ValueError:
        return None, None

    tile = game.tiles.get(tile_id)
    if not tile:
        return None, None

    all_paths = {}
    orientations = []
    translations = {}
    for orientation in range(0, 6):
        try:
            board._validate_place_tile_neighbors(cell, tile, orientation)
            board._validate_place_tile_upgrade(board.get_space(cell), cell, tile, orientation)
        except ValueError:
            continue

        tile_paths = tuple(sorted((key, tuple(sorted(val))) for key, val in placedtile.PlacedTile.get_paths(cell, tile, orientation).items()))
        if tile_paths in all_paths:
            translations[orientation] = all_paths[tile_paths]
            continue

        orientations.append(orientation)
        all_paths[tile_paths] = orientation

    return orientations, translations

@game_app.route("/board/tile-coords")
def legal_tile_coords():
    LOG.info("Legal tile coordinates request.")

    current_coord = request.args.get("coord")
    existing_tile_coords = {coord for coord in json.loads(request.args.get("tile_coords")) if coord}

    legal_tile_coordinates = set(get_tile_coords(get_board(g.game_name))) - existing_tile_coords
    if current_coord:
        legal_tile_coordinates.add(current_coord)

    LOG.info(f"Legal tile coordinates response: {legal_tile_coordinates}")

    return jsonify({"tile-coords": list(sorted(legal_tile_coordinates))})

@game_app.route("/board/tile-image")
def board_tile_image():
    tile_id = request.args.get("tileId")
    return url_for('static', filename='images/tiles/{:03}'.format(tile_id))

@game_app.route("/board/legal-tiles")
def legal_tiles():
    game = get_game(g.game_name)

    coord = request.args.get("coord")

    LOG.info(f"Legal tiles request for {coord}.")

    legal_tile_ids = _legal_tile_ids_by_coord(get_game(g.game_name), coord)
    legal_tile_ids.sort(key=lambda tile_id: f"{game.tiles[tile_id].upgrade_level}-{tile_id:0>3}")

    LOG.info(f"Legal tiles response for {coord}: {legal_tile_ids}")

    return jsonify({"legal-tile-ids": legal_tile_ids})

@game_app.route("/board/legal-orientations")
def legal_orientations():
    coord = request.args.get("coord")
    tile_id = request.args.get("tileId")

    LOG.info(f"Legal orientations request for {tile_id} at {coord}.")

    orientations, translations = _get_orientations(get_game(g.game_name), coord, tile_id)

    LOG.info(f"Legal orientations response for {tile_id} at {coord}: {orientations}")

    return jsonify({
        "legal-orientations": list(sorted(orientations)) if orientations is not None else orientations,
        "translations": translations
    })

def _get_station_offset(offset_data, key):
    default_offset = {"x": 0, "y": 0, "rotation": 0}
    space_offset_data = offset_data.get(key, {})

    if isinstance(space_offset_data, str):
        return _get_station_offset(offset_data, space_offset_data)
    else:
        return space_offset_data.get("offset", default_offset).copy()

@game_app.route("/board/space-info")
def board_space_info():
    coord = request.args["coord"].strip()
    tile_id = request.args.get("tileId", "").strip()
    orientation = request.args.get("orientation", "").strip()

    game = get_game(g.game_name)
    board = get_board(game)
    cell = board.cell(coord)

    station_offsets = get_station_offsets(game)
    if tile_id and orientation:
        offset_data = station_offsets.get("tile", {})
        offset = _get_station_offset(offset_data, tile_id)

        space = placedtile.PlacedTile.place(cell, game.tiles[tile_id], orientation, board.get_space(cell))
        if isinstance(space, placedtile.SplitCity):
            # In the offset file, branches are indicated by side. To translate
            # into cells, add the orientation, and modulo by 6 to retrieve its
            # neighbor given the orientation.
            offset = {str(cell.neighbors[(int(exit) + int(orientation)) % 6]): offsetCoords for exit, offsetCoords in offset.items()}

        if "rotation" in offset:
            offset["rotation"] = math.radians(offset["rotation"])
    else:
        offset_data = station_offsets.get("board", {})
        offset = _get_station_offset(offset_data, coord)
        space = board.get_space(cell)

        if "rotation" in offset:
            offset["rotation"] = math.radians(offset["rotation"])

    if hasattr(space, "capacity"):
        capacity = sum(space.capacity.values()) if isinstance(space.capacity, dict) else space.capacity
    else:
        capacity = 0

    info = {
        # Stop-gap. I need to figure out what to actually do with capacity keys.
        "capacity": capacity,
        "offset": offset,
        "phase": space.upgrade_level,
        "is-split-city": isinstance(space, (boardtile.SplitCity, placedtile.SplitCity))
    }

    return jsonify({"info": info})

@game_app.route("/board/private-company-info")
def board_private_company_info():
    coord = request.args.get("coord")
    company = request.args.get("company")
    phase = request.args.get("phase")

    default_offset = {"x": 0, "y": 0}
    private_company_offsets = get_private_offsets(get_game(g.game_name))
    offset_data = private_company_offsets.get(company, {})
    coord_offset = offset_data.get(coord, {}).get("offset", default_offset)

    if set(coord_offset.keys()) == {"x", "y"}:
        offset = coord_offset
    else:
        game = get_game(g.game_name)
        for offset_phase in sorted(coord_offset, key=game.phases.index, reverse=True):
            if game.compare_phases(offset_phase, phase) >= 0:
                offset = coord_offset[offset_phase]
                break
        else:
            # This indicates a special offset will be needed in later phases,
            # but we can use the default offset for now.
            offset = default_offset

    info = {
        "offset": offset
    }

    return jsonify({"info": info})

@game_app.route("/board/phase")
def board_phase():
    LOG.info("Phase request")

    phase = _phase_from_trains(request.args.get("trains"))

    LOG.info(f"Phase: {phase}")

    return jsonify({"phase": phase})

def _phase_from_trains(train_strs_json):
    train_strs = json.loads(train_strs_json)
    if train_strs:
        train_info = get_train_info(g.game_name)
        return max(train.phase for train in trains_mod.convert(train_info, ",".join(train_strs)))
    else:
        return get_game(g.game_name).phases[0]

@game_app.route("/railroads/legal-railroads")
def legal_railroads():
    LOG.info("Legal railroads request.")

    existing_railroads = {railroad for railroad in json.loads(request.args.get("railroads", "{}")) if railroad}

    railroads_info = get_railroad_info(g.game_name)
    legal_railroads = set(railroads_info.keys()) - existing_railroads

    LOG.info(f"Legal railroads response: {legal_railroads}")

    return jsonify({
        "railroads": list(sorted(legal_railroads)),
        "home-cities": {railroad: railroads_info[railroad]["home"] for railroad in legal_railroads}
    })

def _get_removable_railroads():
    railroads_info = get_railroad_info(g.game_name)
    return {name for name, attribs in railroads_info.items() if attribs.get("is_removable")}

@game_app.route("/railroads/removable-railroads")
def removable_railroads():
    LOG.info("Removable railroads request.")

    existing_railroads = {railroad for railroad in json.loads(request.args.get("railroads", "{}")) if railroad}

    railroads_info = get_railroad_info(g.game_name)
    all_removable_railroads = _get_removable_railroads()
    removable_railroads = all_removable_railroads - existing_railroads

    LOG.info(f"Removable railroads response: {removable_railroads}")

    return jsonify({
        "railroads": list(sorted(removable_railroads)),
        "home-cities": {railroad: railroads_info[railroad]["home"] for railroad in removable_railroads}
    })

@game_app.route("/railroads/trains")
def trains():
    LOG.info("Train request.")

    all_trains = get_train_info(g.game_name)
    train_strs = [str(train) for train in sorted(all_trains, key=lambda train: (train.collect, train.visit))]

    LOG.info(f"Train response: {all_trains}")

    return jsonify({"trains": train_strs})

@game_app.route("/railroads/cities")
def cities():
    LOG.info("Cities request.")

    board = get_board(g.game_name)
    # all_cities = [str(cell) for cell in sorted(board.cells) if board.get_space(cell) and board.get_space(cell).is_city]
    all_cities = []
    split_cities = []
    for cell in sorted(board.cells):
        space = board.get_space(cell)
        if space and space.is_city:
            coord = str(cell)
            all_cities.append(coord)
            if isinstance(space, (boardtile.SplitCity, placedtile.SplitCity)):
                split_cities.append(coord)

    LOG.info(f"Cities response: {all_cities}")

    return jsonify({
        "cities": all_cities,
        "split-cities": split_cities
    })

@game_app.route("/railroads/legal-split-city-stations")
def split_city_stations():
    LOG.info("Legal split city stations request.")

    existing_station_coords = {coord for coord in json.loads(request.args.get("stations", "{}")) if coord}

    split_city_station_coords = _get_split_city_stations(
        # The default values can change once the templates are generalized. And
        # they'll need to before implementing another game with split cities.
        request.args["coord"],
        request.args.get("tileId"),
        request.args.get("orientation")
    )

    legal_stations = sorted(split_city_station_coords - existing_station_coords)

    LOG.info(f"Legal split city stations response: {legal_stations}")

    return jsonify({"split-city-stations": legal_stations})

def _get_split_city_stations(coord, tile_id, orientation):
    game = get_game(g.game_name)
    board = get_board(game)
    cell = board.cell(coord)

    if tile_id and orientation:
        split_city_space = placedtile.SplitCity.place(cell, game.tiles[tile_id], orientation)
    else:
        split_city_space = board.get_space(cell)

    split_city_station_coords = set()
    for branch in split_city_space.capacity.keys():
        unique_exit_coords = [branch_key for branch_key in branch if len(branch_key) == 1]
        if unique_exit_coords:
            # A unique exit coord is a sequence of length 1, so we need to extract it
            split_city_station_coords.add(str(sorted(unique_exit_coords)[0][0]))
        else:
            split_city_station_coords.add(str(sorted(branch)[0]))

    return split_city_station_coords

@game_app.route("/railroads/legal-token-coords")
def legal_token_coords():
    game = get_game(g.game_name)
    private_companies = game.get_game_submodule("private_companies")
    if not private_companies:
        return jsonify({"coords": {}})

    LOG.info("Legal private company token coordinates request.")

    private_company_coords = private_companies.PRIVATE_COMPANY_COORDS

    LOG.info(f"Legal private company token coordinates response: {private_company_coords}")

    return jsonify({"coords": private_company_coords})

@game_app.route("/private-comapnies/open")
def private_companies_open():
    game = get_game(g.game_name)
    private_companies = game.get_game_submodule("private_companies")
    if not private_companies:
        return jsonify({"private-companies": []})

    LOG.info("Open private companies request.")

    phase = _phase_from_trains(request.args.get("trains"))
    private_companies = [private_company for private_company in private_companies.COMPANIES if not game.private_is_closed(private_company, phase)]

    LOG.info(f"Open private companies response: {private_companies}")

    return jsonify({"private-companies": private_companies})

def _attach_json(msg, filename, content=None):
    if content is None:
        with open(filename) as json_file:
            content = json_file.read()

    encoded_content = base64.b64encode(json.dumps(content, indent=4, sort_keys=True).encode("utf-8")).decode()
    msg.add_attachment(
        Attachment(file_content=encoded_content, file_type="application/json", file_name=os.path.basename(filename))
    )

def _sendgrid_client():
    return SendGridAPIClient(os.environ.get('SENDGRID_API_KEY'))

def _build_general_message():
    railroad_headers = json.loads(request.form.get("railroadHeaders"))
    railroads_data = json.loads(request.form.get("railroadData"))
    private_companies_headers = json.loads(request.form.get("privateCompaniesHeaders", []))
    private_companies_data = json.loads(request.form.get("privateCompaniesData", {}))
    placed_tiles_headers = json.loads(request.form.get("placedTilesHeaders"))
    placed_tiles_data = json.loads(request.form.get("placedTilesData"))
    user_email = request.form.get("email")
    user_comments = request.form.get("comments")
    email_subject = request.form.get("subject")
    phase = request.form.get("phase")

    railroads_json = [dict(zip(railroad_headers, row)) for row in railroads_data if any(row)]
    private_companies_json = [dict(zip(private_companies_headers, row)) for row in private_companies_data]
    placed_tiles_json = [dict(zip(placed_tiles_headers, row)) for row in placed_tiles_data if any(row)]

    msg = Mail(
        from_email=app.config.get("MAIL_USERNAME"),
        to_emails=os.environ["BUG_REPORT_EMAIL"],
        subject=email_subject,
        plain_text_content=MESSAGE_BODY_FORMAT.format(user=user_email, comments=user_comments, phase=phase))

    _attach_json(msg, "railroads.json", railroads_json)
    _attach_json(msg, "private-companies.json", private_companies_json)
    _attach_json(msg, "placed-tiles.json", placed_tiles_json)

    return msg

@game_app.route("/report/general-issue", methods=["POST"])
def report_general_issue():
    response = _sendgrid_client().send(_build_general_message())
    return ""

@game_app.route("/report/calc-issue", methods=["POST"])
def report_calc_issue():
    target_railroad = request.form.get("targetRailroad")
    job_id = request.form.get("jobId")
    result_html = request.form.get("resultHtml")
    hide_stops = request.form.get("hideStops")

    msg = _build_general_message()

    routes_json = _get_calculate_result(job_id)
    routes_json.update({
          "jobId": job_id,
          "resultHtml": result_html,
          "hideStops": hide_stops
    })

    _attach_json(msg, "routes.json", {target_railroad: routes_json})

    response = _sendgrid_client().send(msg)

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
    phase = request.form.get("phase")
    stations = json.loads(request.form.get("stations"))
    private_companies = json.loads(request.form.get("privateCompanies"))

    message_body = TILE_MESSAGE_BODY_FORMAT.format(
        user=user_email, comments=user_comments, phase=phase, coord=coord, tile_id=tile_id, orientation=orientation)

    msg = Mail(
        from_email=app.config.get("MAIL_USERNAME"),
        to_emails=os.environ["BUG_REPORT_EMAIL"],
        subject=email_subject,
        plain_text_content=message_body)

    placed_tiles_json = [dict(zip(placed_tiles_headers, row)) for row in placed_tiles_data if any(row)]

    _attach_json(msg, "placed-tiles.json", placed_tiles_json)
    _attach_json(msg, "tiles.json", tiles_json)
    _attach_json(msg, "orientations.json", orientations_json)
    _attach_json(msg, "stations.json", dict(stations))
    _attach_json(msg, "private-companies.json", dict(private_companies))

    response = _sendgrid_client().send(msg)

    return ""

app.register_blueprint(game_app, url_prefix=f"{GAME_APP_ROOT}/<game_name>")
