# This file contains the WSGI configuration required to serve up your
# web application at http://<your-username>.pythonanywhere.com/
# It works by setting the variable 'application' to a WSGI handler of some
# description.
#
# The below has been auto-generated for your Flask project

import sys
import os

# add your project directory to the sys.path
project_home = u'/home/frenzymadness/devconf-coding-challenge-webform'
if project_home not in sys.path:
    sys.path = [project_home] + sys.path

os.environ['CODING_CHALLENGE_ADMIN_PASSWORD'] = 'password'

# import flask app but need to call it "application" for WSGI to work
from form import app as application
application.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///form.db'
application.config['UPLOAD_FOLDER'] = '/home/frenzymadness/devconf-coding-challenge-webform/files'
