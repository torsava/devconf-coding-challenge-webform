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

app = Flask(__name__)

QUESTIONS = 'name', 'email'
CHECKBOXES = 'is_redhatter',
FILES = 'file_py', 'file_c', 'file_java'
FILE_LABELS = 'Python 3', 'C', 'Java'
SETTINGS = 'submissions_enabled', 'scoreboard_enabled'
SETTING_TEXTS = 'Submissions enabled', 'Scoreboard enabled'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = './files'  # can get overwritten in wsgi.py
ALLOWED_EXTENSIONS = {'file_py': 'py',
                      'file_c': 'c',
                      'file_java': 'java'}

def allowed_file(file_slug, filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() == ALLOWED_EXTENSIONS[file_slug]

def path_file(filename):
    return os.path.join(app.config['UPLOAD_FOLDER'], filename)

def get_setting(setting_slug, default):
    setting = db.session.query(Setting) \
              .filter_by(setting_slug=setting_slug).first()
    return setting.value if setting else default

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
    time_complexity = db.Column(db.Integer, nullable=True)
    memory_complexity = db.Column(db.Integer, nullable=True)
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

                    filename = token + "__" \
                            + werkzeug.utils.secure_filename(user_name) \
                            + "__" + file_slug + "." + ALLOWED_EXTENSIONS[file_slug]
                    filename = filename.replace("-", "_") # In case of a dash in user name
                    file.save(path_file(filename))

                    db.session.merge(File(
                        token=token,
                        file_slug=file_slug,
                        filename=filename,
                        valid=None,
                        time_complexity=None,
                        memory_complexity=None,
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

    return all_data


@app.route('/admin/<password>/', methods=['GET', 'POST'])
@app.route('/admin/<password>/evaluate/<token>/', methods=['GET', 'POST'])
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
        SETTING_TUPLES=zip(SETTINGS, SETTING_TEXTS)
    )

@app.route('/winners/')
@app.route('/winners/<token>/')
@app.route('/admin/<password>/winners/')
def winners(token=None, password=None):
    admin_mode = False
    if password is not None:
        check_password(password)
        admin_mode = True

    scoreboard_enabled = get_setting("scoreboard_enabled", False)

    all_data = get_all_data(db, only_file_edits=True)

    # Sort by number of solved problems and time the last file was submitted
    iter_data = sorted(all_data.items(),
            key=lambda d: (-sum([1 for f in FILES if f in d[1] and d[1][f][1]]),
                           d[1]["last_edit"]))

    return render_template(
        'winners.html',
        token=token,
        password=password,
        admin_mode=admin_mode,
        scoreboard_enabled=scoreboard_enabled,
        data=iter_data,
        FILES=FILES,
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

@app.route('/api/<password>/rate/', methods=['GET', 'POST'])
def api_rate(password=None):
    check_password(password)

    if request.method == 'POST':
        filename = request.form.get('filename')

        f = db.session.query(File).filter(File.filename==filename).first()
        if f is None:
            return json.dumps({'success': False}), 404, \
                              {'ContentType': 'application/json'}

        f.filename = filename
        f.valid = 1 if 'valid' in request.form else 0
        f.time_complexity = request.form.get('time_complexity')
        f.memory_complexity = request.form.get('memory_complexity')
        f.tokens = request.form.get('tokens')
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
