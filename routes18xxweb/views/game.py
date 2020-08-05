import math
import json
import os

from flask import g, jsonify, redirect, render_template, request, session, url_for

from routes18xx import boardstate, boardtile, placedtile, railroads, tiles, trains as trains_mod

from routes18xxweb.views import GAME_APP_ROOT, LOG, migrate
from routes18xxweb.routes18xxweb import app, game_app
from routes18xxweb.calculator import redis_conn
from routes18xxweb.games import (get_board, get_board_layout, get_game, \
    get_private_offsets, get_railroad_info, get_station_offsets, \
    get_supported_game_info, get_termini_boundaries, get_train_info)


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

@app.route("/")
def game_picker():
    return render_template("game-selection.html", game_root=GAME_APP_ROOT, games=get_supported_game_info())

@app.route("/game/")
def incomplete_game_url_handler():
    return redirect(url_for("game_picker"))

@game_app.route("/")
def main():
    migration_data = migrate.process_migrate_data()

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
            private_company_default_token_coords=private_companies.PRIVATE_COMPANY_DEFAULT_COORDS if private_companies else {},
            private_company_rownames=private_company_names,
            placed_tiles_colnames=PLACED_TILES_COLUMN_NAMES,
            tile_coords=get_tile_coords(board),
            stop_names=stop_names,
            termini_boundaries=termini_boundaries,
            removable_railroads=_get_removable_railroads(),
            closable_railroads=_get_closable_railroads(game),
            board_layout=_get_board_layout_info(),
            migration_data=migration_data)

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
        elif space.is_city != tile.is_city or space.is_town != tile.is_town or tile.upgrade_attrs not in space.upgrade_attrs:
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

    removable_railroads = _get_removable_railroads()

    LOG.info(f"Removable railroads response: {removable_railroads}")

    railroads_info = get_railroad_info(g.game_name)
    return jsonify({
        "railroads": list(sorted(removable_railroads)),
        "home-cities": {railroad: railroads_info[railroad]["home"] for railroad in removable_railroads}
    })

def _get_closable_railroads(game):
    return set(get_railroad_info(game).keys()) if game.rules.railroads_can_close else set()
 
@game_app.route("/railroads/closable-railroads")
def closable_railroads():
    LOG.info("Closable railroads request.")

    game = get_game(g.game_name)
    closable_railroads = _get_closable_railroads(game)

    LOG.info(f"Closable railroads response: {closable_railroads}")

    railroads_info = get_railroad_info(game)
    return jsonify({
        "railroads": list(sorted(closable_railroads)),
        "home-cities": {railroad: railroads_info[railroad]["home"] for railroad in closable_railroads}
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