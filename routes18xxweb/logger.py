import logging
import os
import sys


LOG_LEVEL_MAP = {
    0: logging.ERROR,
    1: logging.INFO,
    2: logging.DEBUG
}
MAX_LOG_LEVEL = max(LOG_LEVEL_MAP.values())

def get_level(tracking_env_var_name=None, default_level=1):
    if tracking_env_var_name:
        try:
            log_level = int(os.environ.get(tracking_env_var_name, str(default_level)).strip())
        except ValueError:
            log_level = default_level

        if log_level < 0:
            # If the log level is negative, set it to 0
            log_level = 0
        elif log_level > MAX_LOG_LEVEL:
            # If the log level is higher than the max level, set it to the max level
            log_level = MAX_LOG_LEVEL
    else:
        log_level = default_level
    return LOG_LEVEL_MAP[log_level]

def get_logger(name=None):
    return logging.getLogger(name) if name else logging.getLogger()

def init_logger(logger, tracking_env_var_name=None, default_level=1):
    handler = logging.StreamHandler(stream=sys.stdout)
    logger.addHandler(handler)
    logger.setLevel(get_level(tracking_env_var_name, default_level))
    return logger

def set_log_format(logger, log_format="%(asctime)s %(name)s: %(message)s"):
    for handler in logger.handlers:
        handler.setFormatter(logging.Formatter(log_format))