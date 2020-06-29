import os

from flask import Flask
from flask_wtf.csrf import CSRFProtect
from flask_mail import Mail

_DIR_NAME = "data"
_DATA_ROOT_DIR = os.path.abspath(os.path.normpath(os.path.join(os.path.dirname(__file__), _DIR_NAME)))

app = Flask(__name__, template_folder='templates', static_folder='static')
app.config.from_object("routes18xxweb.settings")

csrf = CSRFProtect(app)
mail = Mail(app)

def get_data_file(filename):
    return os.path.join(_DATA_ROOT_DIR, filename)