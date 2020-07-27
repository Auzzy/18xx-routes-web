from flask import abort, g, render_template

from routes18xxweb.games import get_supported_game_info
from routes18xxweb.logger import get_logger, init_logger, set_log_format
from routes18xxweb.routes18xxweb import app, game_app
from routes18xx import LOG as LIB_LOG

LOG = get_logger("routes18xxweb")
init_logger(LOG, "APP_LOG_LEVEL")
set_log_format(LOG)

init_logger(LIB_LOG, "LIB_LOG_LEVEL", 0)
set_log_format(LIB_LOG)

GAME_APP_ROOT = "/game"
UNSUPPORTED_GAME_MESSAGE = "unsupported game"


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


from routes18xxweb.views import calculate, game, migrate, report

app.register_blueprint(game_app, url_prefix=f"{GAME_APP_ROOT}/<game_name>")