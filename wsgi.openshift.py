import os

from form import app as application

if 'OPENSHIFT_DATA_DIR' in os.environ:
    datadir = os.environ['OPENSHIFT_DATA_DIR']
    application.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + datadir + '/form.db'
    application.config['UPLOAD_FOLDER'] = datadir + '/files'
