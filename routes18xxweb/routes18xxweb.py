from flask import Flask
from flask_wtf.csrf import CSRFProtect
from flask_mail import Mail

app = Flask(__name__, template_folder='templates', static_folder='static')
app.config.from_object("routes18xxweb.settings")

csrf = CSRFProtect(app)
mail = Mail(app)