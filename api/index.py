"""Vercel Function entrypoint for PetStock Hub."""
import sys
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app import app, init_database  # noqa: E402

# Vercel imports the WSGI application instead of running app.py as __main__.
init_database()
