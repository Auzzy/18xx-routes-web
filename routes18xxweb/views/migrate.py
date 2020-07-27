import json
import uuid

from flask import jsonify, redirect, request, session, url_for

from routes18xxweb.calculator import redis_conn
from routes18xxweb.games import get_game, get_railroad_info, _get_placement_info
from routes18xxweb.routes18xxweb import csrf, game_app
from routes18xxweb.views import LOG

REDIS_KEY_PREFIX = "migrate"
SESSION_COOKIE_KEY = "migration_data"

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
    redis_key = f"{REDIS_KEY_PREFIX}-{redis_id}"
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

    migration_data = redis_conn.hgetall(f"{REDIS_KEY_PREFIX}-{id}")
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
    session[SESSION_COOKIE_KEY] = json.dumps(migration_data)

    return redirect(url_for('.main'))

def _validate_migration_data(migration_data):
    if not isinstance(migration_data, dict):
        LOG.debug(f"Migration validation failure: migration_data is not a dict")
        return {"error": "Failed to load migration data."}

    game = get_game("1846")
    railroad_info = get_railroad_info(game)
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

def process_migrate_data():
    migration_data = json.loads(session.pop(SESSION_COOKIE_KEY, "{}"))
    if migration_data:
        LOG.debug(f"Migration finalizing: found session data.")
        if _validate_migration_data(migration_data):
            LOG.debug(f"Migration data pre-conversion: {migration_data}")
            migration_data = _convert_migration_data(migration_data)
            LOG.debug(f"Migration data post-conversion: {migration_data}")
        else:
            LOG.debug(f"Migration validation failed: continuing without migration")
            migration_data = {}
    return migration_data