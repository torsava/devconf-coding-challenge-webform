from collections import defaultdict
import base64
import os
import urllib.parse
from datetime import datetime
from pytz import timezone

from flask import Flask, render_template, request, abort, redirect, url_for
from flask import send_from_directory
from jinja2 import StrictUndefined
from flask_sqlalchemy import SQLAlchemy
import werkzeug
import click
import json
from itertools import product

app = Flask(__name__)

QUESTIONS = 'name', 'email'
CHECKBOXES = 'is_redhatter',
LANGUAGES = 'python', 'c', 'java'
FILES = 'python', 'c', 'java'  # TODO unify LANGUAGES and FILES?
VALUATIONS = 'time', 'memory', 'tokens'
FILE_LABELS = 'Python 3', 'C', 'Java'
SETTINGS = 'submissions_enabled', 'scoreboard_enabled'
SETTING_TEXTS = 'Submissions enabled', 'Scoreboard enabled'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = './files'  # can get overwritten in wsgi.py
ALLOWED_EXTENSIONS = {'python': 'py', 'c': 'c', 'java': 'java'}

def allowed_file(file_slug, filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() == ALLOWED_EXTENSIONS[file_slug]

def path_file(filename):
    return os.path.join(app.config['UPLOAD_FOLDER'], filename)

def get_setting(setting_slug, default):
    setting = db.session.query(Setting) \
              .filter_by(setting_slug=setting_slug).first()
    return setting.value if setting else default

def ordinal_number(num):
    if 10 <= num % 100 < 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(num % 10, "th")
    return str(num) + suffix


tz_prague = timezone('Europe/Prague')

db = database = SQLAlchemy(app)

class Data(db.Model):
    token = db.Column(db.Unicode, primary_key=True)
    question_slug = db.Column(db.Unicode, primary_key=True)
    answer = db.Column(db.Unicode, nullable=True)
    timestamp = db.Column(db.DateTime(timezone=True),
                          server_default=db.func.now())

class File(db.Model):
    token = db.Column(db.Unicode, primary_key=True)
    file_slug = db.Column(db.Unicode, primary_key=True)
    filename = db.Column(db.Unicode, nullable=True)
    valid = db.Column(db.Boolean, nullable=True)
    time = db.Column(db.Float, nullable=True)
    memory = db.Column(db.Float, nullable=True)
    tokens = db.Column(db.Integer, nullable=True)
    timestamp = db.Column(db.DateTime(timezone=True),
                          server_default=db.func.now())

class Setting(db.Model):
    setting_slug = db.Column(db.Unicode, primary_key=True)
    value = db.Column(db.Boolean, nullable=True)
    timestamp = db.Column(db.DateTime(timezone=True),
                          server_default=db.func.now())

@app.route('/')
@app.route('/form/')
@app.route('/form/<token>/', methods=['GET', 'POST'])
@app.route('/form/<token>/<warning>/', methods=['GET', 'POST'])
def form(token=None, warning=None):
    show_thankyou = bool(token)
    if token is None:
        # Generate a random token
        # (in Python 3.6+ this would be `secrets.token_urlsafe`)
        bencoded = base64.urlsafe_b64encode(os.urandom(8))
        token = bencoded.rstrip(b'=').decode('ascii').replace("-", "_")
    if not (5 < len(token) < 20):
        abort(404)

    submissions_enabled = get_setting("submissions_enabled", True)
    scoreboard_enabled = get_setting("scoreboard_enabled", False)

    # Saving the results into a list eliminates further SQL queries in POST
    # processing, saving it to a dict addressable by something else than the
    # object does not.
    prefetch = {'data': list(db.session.query(Data).filter_by(token=token)),
                'file': list(db.session.query(File).filter_by(token=token))}

    data = { f.question_slug: f.answer for f in prefetch['data']}
    files = { f.file_slug: f.filename for f in prefetch['file']}

    if request.method == 'POST':
        if not submissions_enabled:
            return redirect(url_for('form', token=token))

        for question in QUESTIONS:
            answer = request.form.get(question)
            db.session.merge(Data(
                token=token,
                question_slug=question,
                answer=answer,
                timestamp=datetime.now(tz_prague),
            ))
        for checkbox in CHECKBOXES:
            db.session.merge(Data(
                token=token,
                question_slug=checkbox,
                answer=checkbox in request.form,
                timestamp=datetime.now(tz_prague),
            ))

        user_name = request.form.get('name')
        for file_slug in [f for f in FILES if f in request.files]:
            file = request.files[file_slug]
            if file:
                if not allowed_file(file_slug, file.filename):
                    warning = 'wrong-extension'
                else:
                    # Remove the previous file if any.
                    # This is necessary if the user has changed his name after
                    # the file was saved last time
                    if file_slug in files and files[file_slug]:
                        os.remove(path_file(files[file_slug]))

                    filename = token + "___" \
                            + werkzeug.utils.secure_filename(file.filename)
                    filename = filename.replace("-", "_") # In case of a dash in user name
                    file.save(path_file(filename))

                    db.session.merge(File(
                        token=token,
                        file_slug=file_slug,
                        filename=filename,
                        valid=None,
                        time=None,
                        memory=None,
                        tokens=None,
                        timestamp=datetime.now(tz_prague),
                    ))

        db.session.commit()
        return redirect(url_for('form', token=token, warning=warning))

    return render_template(
        'index.html',
        data=data,
        files=files,
        FILE_TUPLES=zip(FILES, FILE_LABELS),
        token=token,
        submissions_enabled=submissions_enabled,
        scoreboard_enabled=scoreboard_enabled,
        show_thankyou=show_thankyou,
        warning=warning,
    )

@app.errorhandler(werkzeug.exceptions.RequestEntityTooLarge)
def request_entity_too_large(error):
    """Error 413"""
    return redirect(request.base_url + "file-too-large/"), \
        werkzeug.exceptions.RequestEntityTooLarge.code

@app.route('/file/<string:filename>/')
def file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


def check_password(password):
    # Check an environment variable on the OpenShift server that only we know
    # the value of
    secret = os.environ.get("CODING_CHALLENGE_ADMIN_PASSWORD")
    if not secret or secret != password:
        abort(werkzeug.exceptions.Unauthorized.code)

def get_all_data(db):
    all_data = defaultdict(dict)

    for d in db.session.query(Data):
        all_data[d.token][d.question_slug] = d.answer

    for f in db.session.query(File):
        all_data[f.token][f.file_slug] = f

    # How many files the user submitted?
    for elem in all_data.values():
        elem["files_submitted"] = sum([1 for f in FILES if f in elem])

    return all_data


@app.route('/admin/<password>/', methods=['GET', 'POST'])
def admin(password=None, token=None):
    check_password(password)

    if request.method == 'POST':
        # Site settings
        for setting_slug in SETTINGS:
            value = setting_slug in request.form
            db.session.merge(Setting(
                setting_slug=setting_slug,
                value=value,
            ))

        db.session.commit()
        return redirect(url_for('admin', password=password))

    all_data = get_all_data(db)

    # Sort by fully evaluated and by the token
    iter_data = sorted(all_data.items(),
            key=lambda d: d[0].lower())

    settings = { s.setting_slug: s.value for s in db.session.query(Setting)}

    return render_template(
        'admin.html',
        password=password,
        data=iter_data,
        settings=settings,
        QUESTIONS=QUESTIONS,
        CHECKBOXES=CHECKBOXES,
        FILES=FILES,
        FILE_LABELS=FILE_LABELS,
        zip=zip,
        SETTING_TUPLES=zip(SETTINGS, SETTING_TEXTS),
    )

@app.route('/winners/<token>/')
@app.route('/winners/<token>/<any(rh,nonrh):rh_string>/')
@app.route('/winners/<token>/<any(rh,nonrh):rh_string>/<string:language>/<string:order>/')
@app.route('/winners/')
@app.route('/winners/<any(rh,nonrh):rh_string>/')
@app.route('/winners/<any(rh,nonrh):rh_string>/<string:language>/<string:order>/')
@app.route('/admin/<password>/winners/')
@app.route('/admin/<password>/winners/<any(rh,nonrh):rh_string>/')
@app.route('/admin/<password>/winners/<any(rh,nonrh):rh_string>/<string:language>/<string:order>/')
def winners(token=None, password=None, rh_string=None, language=None, order=None):
    admin_mode = False
    if password is not None:
        check_password(password)
        admin_mode = True

    rh_string = rh_string if rh_string == "rh" else "nonrh"
    rh_mode = rh_string == "rh"
    language = language if language in LANGUAGES else "python"
    order = order if order in VALUATIONS else "time"

    scoreboard_enabled = get_setting("scoreboard_enabled", False)
    all_data = get_all_data(db)

    # Filter out non/RedHatters
    rh_mode_code = '1' if rh_mode else '0'
    all_data = {k: v for (k, v) in all_data.items()
                if v["is_redhatter"] == rh_mode_code}


    # Figure out positions in all languages and valuations
    def winner_sort_key(d, lang, valuation):
        # Not submitted, not evaluated and invalid files are sorted last (inf)
        if getattr(d.get(lang, {}), 'valid', None):
            return getattr(d.get(lang, {}), valuation, None) or float('inf')
        else:
            return float('inf')

    for lang, valuation in product(LANGUAGES, VALUATIONS):
        ordered = sorted(all_data.values(),
                         key=lambda d: winner_sort_key(d, lang, valuation))
        for i, d in enumerate(ordered):
            d.setdefault("position", {}).setdefault(lang, {})[valuation] = i + 1


    iter_data = sorted(all_data.items(),
            key=lambda d: winner_sort_key(d[1], language, order))

    return render_template(
        'winners.html',
        token=token,
        password=password,
        rh_mode=rh_mode,
        rh_string=rh_string,
        language=language,
        order=order,
        admin_mode=admin_mode,
        scoreboard_enabled=scoreboard_enabled,
        data=iter_data,
        FILES=FILES,
        FILE_LABELS=FILE_LABELS,
        LANGUAGES=LANGUAGES,
        VALUATIONS=VALUATIONS,
        getattr=getattr,
        ordinal_number=ordinal_number,
    )

@app.route('/api/<password>/unrated/', methods=['GET'])
def api_unrated(password=None):
    check_password(password)

    files = [f for f in db.session.query(File).filter(File.valid.is_(None))
            .order_by(File.timestamp)]

    return render_template(
        'api_unrated.txt',
        files=files,
    )

@app.route('/api/<password>/invalid/<filename>/', methods=['GET'])
def api_invalid(password=None, filename=None):
    check_password(password)

    if filename is not None:
        f = db.session.query(File).filter(File.filename==filename).first()
        if f is not None:
            f.valid = 0
            db.session.commit()
            return json.dumps({'success': True}), 200, \
                              {'ContentType': 'application/json'}

    return json.dumps({'success': False}), 404, \
                      {'ContentType': 'application/json'}

@app.route('/api/<password>/rate/', methods=['GET', 'POST'])
def api_rate(password=None):
    check_password(password)

    if request.method == 'POST':
        filename = request.form.get('filename')

        f = db.session.query(File).filter(File.filename==filename).first()
        if f is None:
            return json.dumps({'success': False}), 404, \
                              {'ContentType': 'application/json'}

        def input_to_number(type, num):
            try:
                return type(num)
            except ValueError:
                return None
        f.filename = filename
        f.valid = 1 if 'valid' in request.form else 0
        f.time = input_to_number(float, request.form.get('time'))
        f.memory = input_to_number(float, request.form.get('memory'))
        f.tokens = input_to_number(int, request.form.get('tokens'))
        db.session.commit()

        return json.dumps({'success': True}), 200, \
                          {'ContentType': 'application/json'}

    else:
        return render_template(
            'api_rate.html',
            password=password,
        )

@click.group()
def cli():
    pass

@cli.command()
@click.option('--port', type=int, help='Port to listen on.')
@click.option('--debug/--no-debug', default=True,
              help='Run in insecure debug mode.')
@click.option('--db', default='form.db',
              help='Database to use. May be a file (created & filled with '
                   'initial data if not present), or a SQLAlchemy URL.')
def serve(port, debug, db):
    if debug:
        app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.jinja_env.undefined = StrictUndefined

    url = urllib.parse.urlparse(db)
    print(url)
    if url.scheme in ('', 'file'):
        if url.scheme == 'file':
            db = url.path
        filename = os.path.abspath(db)
        @app.before_first_request
        def setup_db():
            if not os.path.exists(filename):
                print('Filling initial data in', filename)
                database.create_all()
                database.session.commit()
        db = 'sqlite:///' + filename
    app.config['SQLALCHEMY_DATABASE_URI'] = db
    app.config['SQLALCHEMY_ECHO'] = True

    app.run(port=port, debug=debug)


if __name__ == '__main__':
    cli()
