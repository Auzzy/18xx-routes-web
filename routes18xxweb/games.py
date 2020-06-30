from routes18xx import board, game, railroads, tiles, trains

_GAMES = {}
_BOARDS = {}
_RAILROAD_INFO = {}
_TRAIN_INFO = {}

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
