from collections import namedtuple
import base64
import os
import urllib.parse
import csv
import io
import hashlib

from flask import Flask, render_template, request, abort, redirect, url_for
from flask import Response, send_from_directory
from jinja2 import StrictUndefined
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
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

    if request.method == 'POST':
        print(request.form)
        _prefetch = (list(db.session.query(Data).filter_by(token=token)),
                     list(db.session.query(File).filter_by(token=token)))

        for question in QUESTIONS:
            answer = request.form.get(question)
            db.session.merge(Data(
                token=token,
                question_slug=question,
                answer=answer,
            ))
        for checkbox in CHECKBOXES:
            db.session.merge(Data(
                token=token,
                question_slug=checkbox,
                answer=checkbox in request.form,
            ))

        user_name = request.form.get('name')
        for file_slug in [f for f in FILES if f in request.files]:
            file = request.files[file_slug]
            if file:
                if not allowed_file(file.filename):
                    warning = 'extension'
                else:
                    filename = token + "-" + secure_filename(user_name) \
                            + "-" + file_slug + ".py"
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

                    db.session.merge(File(
                        token=token,
                        file_slug=file_slug,
                        filename=filename,
                    ))

        db.session.commit()
        return redirect(url_for('form', token=token, warning=warning))

    data = {
        f.question_slug: f.answer
        for f in db.session.query(Data).filter_by(token=token)}
    files = {
        f.file_slug: f.filename
        for f in db.session.query(File).filter_by(token=token)}

    return render_template(
        'index.html',
        data=data,
        files=files,
        FILES=FILES,
        token=token,
        show_thankyou=show_thankyou,
        warning=warning,
    )

@app.route('/file/<string:filename>/')
def file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/results.csv')
def results():
    result_file = io.StringIO()
    with result_file as f:
        writer = csv.DictWriter(f, ['user_hash', 'lesson', 'category', 'answer'])
        writer.writeheader()
        for feedback in LessonFeedback.query.order_by(db.func.random()):
            writer.writerow({
                'user_hash': hashlib.sha256(feedback.token.encode('utf-8')).hexdigest(),
                'category': feedback.category_slug,
                'lesson': feedback.lesson_slug,
                'answer': feedback.mark,
            })
        for feedback in Data.query.order_by(db.func.random()):
            if feedback.question_slug not in PRIVATE_QUESTIONS:
                writer.writerow({
                    'user_hash': hashlib.sha256(feedback.token.encode('utf-8')).hexdigest(),
                    'category': feedback.question_slug,
                    'answer': feedback.answer,
                })
        return Response(response=result_file.getvalue(),
                        headers={"Content-Disposition": 'inline; filename="results.csv"'},
                        mimetype='text/csv')

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
