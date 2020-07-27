import base64
import json
import os

from flask import request
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Attachment

from routes18xxweb.routes18xxweb import app, game_app
from routes18xxweb.views import calculate

MESSAGE_BODY_FORMAT = "User: {user}\nComments:\n{comments}\nPhase: {phase}"
TILE_MESSAGE_BODY_FORMAT = MESSAGE_BODY_FORMAT + "\nSelected:\n\tcoordinate: {coord}\n\ttile: {tile_id}\n\torientation: {orientation}"

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

    routes_json = calculate.get_calculate_result(job_id)
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
