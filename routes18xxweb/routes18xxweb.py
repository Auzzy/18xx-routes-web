from flask import Blueprint, Flask
from flask_wtf.csrf import CSRFProtect

app = Flask(__name__, template_folder='templates', static_folder='static')
app.config.from_object("routes18xxweb.settings")

game_app = Blueprint('game_app', __name__)

csrf = CSRFProtect(app)