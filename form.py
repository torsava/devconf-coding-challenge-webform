from collections import namedtuple, defaultdict
import base64
import os
import urllib.parse
import csv
import io
import hashlib
from datetime import datetime

from flask import Flask, render_template, request, abort, redirect, url_for
from flask import Response, send_from_directory
from jinja2 import StrictUndefined
from flask_sqlalchemy import SQLAlchemy
import werkzeug
import click

app = Flask(__name__)

QUESTIONS = 'name', 'email'
CHECKBOXES = 'wants_job',
FILES = 'file_1', 'file_2', 'file_3'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = './files'  # can get overwritten in wsgi.py
ALLOWED_EXTENSIONS = set(['py'])

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def path_file(filename):
    return os.path.join(app.config['UPLOAD_FOLDER'], filename)

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
    works = db.Column(db.Boolean, nullable=True)
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
        token = bencoded.rstrip(b'=').decode('ascii')
    if not (5 < len(token) < 20):
        abort(404)

    # Saving the results into a list eliminates further SQL queries in POST
    # processing, saving it to a dict addressable by something else than the
    # object does not.
    prefetch = {'data': list(db.session.query(Data).filter_by(token=token)),
                'file': list(db.session.query(File).filter_by(token=token))}

    data = { f.question_slug: f.answer for f in prefetch['data']}
    files = { f.file_slug: f.filename for f in prefetch['file']}

    if request.method == 'POST':
        for question in QUESTIONS:
            answer = request.form.get(question)
            db.session.merge(Data(
                token=token,
                question_slug=question,
                answer=answer,
                timestamp=datetime.now(),
            ))
        for checkbox in CHECKBOXES:
            db.session.merge(Data(
                token=token,
                question_slug=checkbox,
                answer=checkbox in request.form,
                timestamp=datetime.now(),
            ))

        user_name = request.form.get('name')
        for file_slug in [f for f in FILES if f in request.files]:
            file = request.files[file_slug]
            if file:
                if not allowed_file(file.filename):
                    warning = 'wrong-extension'
                else:
                    # Remove the previous file if any.
                    # This is necessary if the user has changed his name after
                    # the file was saved last time
                    if file_slug in files and files[file_slug]:
                        os.remove(path_file(files[file_slug]))

                    filename = token + "-" \
                            + werkzeug.utils.secure_filename(user_name) \
                            + "-" + file_slug + ".py"
                    file.save(path_file(filename))

                    db.session.merge(File(
                        token=token,
                        file_slug=file_slug,
                        filename=filename,
                        works=None,
                        timestamp=datetime.now(),
                    ))

        db.session.commit()
        return redirect(url_for('form', token=token, warning=warning))

    return render_template(
        'index.html',
        data=data,
        files=files,
        FILES=FILES,
        token=token,
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


@app.route('/admin/<password>/')
@app.route('/admin/<password>/evaluate/<token>/', methods=['GET', 'POST'])
def admin(password=None, token=None):
    # Check an environment variable on the OpenShift server that only we know
    # the value of
    secret = os.environ.get("OPENSHIFT_APP_UUID")
    if not secret or secret != password:
        abort(werkzeug.exceptions.Unauthorized.code)

    if request.method == 'POST':
        for file_slug in [f for f in FILES if f in request.form]:
            answer = request.form.get(file_slug)
            works = None
            if answer == '1':
                works = True
            elif answer == '0':
                works = False

            db.session.merge(File(
                token=token,
                file_slug=file_slug,
                works=works,
            ))

        db.session.commit()
        return redirect(url_for('admin', password=password))

    all_data = defaultdict(lambda: {"last_edit": datetime.fromtimestamp(0)})
    for d in db.session.query(Data):
        all_data[d.token][d.question_slug] = d.answer
        if all_data[d.token]["last_edit"] < d.timestamp:
            all_data[d.token]["last_edit"] = d.timestamp

    for f in db.session.query(File):
        all_data[f.token][f.file_slug] = (f.filename, f.works)
        if all_data[d.token]["last_edit"] < f.timestamp:
            all_data[d.token]["last_edit"] = f.timestamp

    # Fully evaluated?
    for elem in all_data.values():
        unevaluated = [1 for f in FILES if f in elem and elem[f][1] is None]
        elem["fully_evaluated"] = sum(unevaluated) == 0

    # Sort by the time of the last edit: oldest first so they can be evaluated
    iter_data = sorted(all_data.items(), key=lambda d: d[1]["last_edit"])

    return render_template(
        'admin.html',
        password=password,
        data=iter_data,
        QUESTIONS=QUESTIONS,
        CHECKBOXES=CHECKBOXES,
        FILES=FILES,
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
