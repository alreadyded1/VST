"""WSGI entry point for production (gunicorn wsgi:app).

gunicorn.conf.py sets preload_app, so this module is imported once in the
master process: the schema migrations below run exactly once, before the
worker processes are forked.
"""
from app import app, setup

setup()

__all__ = ['app']
