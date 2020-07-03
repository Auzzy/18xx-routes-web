import collections
import json
import os

from routes18xx import board, game, railroads, tiles, trains

_DIR_NAME = "data"
_DATA_ROOT_DIR = os.path.abspath(os.path.normpath(os.path.join(os.path.dirname(__file__), _DIR_NAME)))

_GAMES = {}
_BOARDS = {}
_RAILROAD_INFO = {}
_TRAIN_INFO = {}
_PLACEMENT_INFO = collections.defaultdict(dict)

def get_game(game_name):
    if game_name not in _GAMES:
        _GAMES[game_name] = game.Game.load(game_name)
    return _GAMES[game_name]

def _get_config(game_obj, config_dict, load_func):
    game_name = game_obj.name if isinstance(game_obj, game.Game) else game_obj
    if game_name not in config_dict:
        config_dict[game_name] = load_func(get_game(game_name))
    return config_dict[game_name]

def get_board(game_obj):
    return _get_config(game_obj, _BOARDS, board.Board.load)

def get_railroad_info(game_obj):
    return _get_config(game_obj, _RAILROAD_INFO, railroads._load_railroad_info)

def get_train_info(game_obj):
    return _get_config(game_obj, _TRAIN_INFO, trains.load_train_info)


def _get_placement_info(game_obj, filename):
    game_name = game_obj.name if isinstance(game_obj, game.Game) else game_obj
    if filename not in _PLACEMENT_INFO or game_name not in _PLACEMENT_INFO[filename]:
        filepath = os.path.join(_DATA_ROOT_DIR, game_name, filename)
        if os.path.exists(filepath):
            with open(filepath) as placement_file:
                _PLACEMENT_INFO[filename][game_name] = json.load(placement_file)
        else:
            _PLACEMENT_INFO[filename][game_name] = {}
    return _PLACEMENT_INFO[filename][game_name].copy()

def get_station_offsets(game):
    return _get_placement_info(game, "stations.json")

def get_private_offsets(game):
    return _get_placement_info(game, "private-companies.json")

def get_termini_boundaries(game):
    return _get_placement_info(game, "termini.json")
