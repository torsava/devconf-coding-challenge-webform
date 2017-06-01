from collections import namedtuple
import base64
import os
import urllib.parse
import csv
import io
import hashlib

from flask import Flask, render_template, request, abort, redirect, url_for
from flask import Response
from jinja2 import StrictUndefined
from flask_sqlalchemy import SQLAlchemy
import click

app = Flask(__name__)

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = database = SQLAlchemy(app)

class SimpleFeedback(db.Model):
    token = db.Column(db.Unicode, primary_key=True)
    question_slug = db.Column(db.Unicode, primary_key=True)
    answer = db.Column(db.Unicode, nullable=True)
    timestamp = db.Column(db.DateTime(timezone=True),
                          server_default=db.func.now())

SIMPLE_QUESTIONS = 'name', 'email', 'file_1', 'file_2', 'file_3'


@app.route('/')
@app.route('/form/')
@app.route('/form/<token>/', methods=['GET', 'POST'])
def form(token=None):
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
        _prefetch = (list(db.session.query(SimpleFeedback).filter_by(token=token)))
        for question in SIMPLE_QUESTIONS:
            answer = request.form.get(question)
            db.session.merge(SimpleFeedback(
                token=token,
                question_slug=question,
                answer=answer,
            ))
        db.session.commit()
        return redirect(url_for('form', token=token))

    simple_feedback = {
        f.question_slug: f.answer
        for f in db.session.query(SimpleFeedback).filter_by(token=token)}

    return render_template(
        'index.html',
        simple_feedback=simple_feedback,
        token=token,
        show_thankyou=show_thankyou,
    )

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
        for feedback in SimpleFeedback.query.order_by(db.func.random()):
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
