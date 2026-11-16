"""Production WSGI entrypoint. Gunicorn imports `app` from this module."""
from app import app, init_database

init_database()
