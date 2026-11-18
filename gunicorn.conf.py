# gunicorn settings, loaded automatically when gunicorn starts in this directory.
#
# Don't edit this file to change the port: updates replace it. Set these in
# /etc/vehicle-tracker.env instead (the systemd service loads it, and updates
# never touch it), then restart the service:
#   VT_PORT     port to listen on (default 5000)
#   VT_HOST     address to listen on (default 0.0.0.0, every interface)
#   VT_WORKERS  worker processes (default 2)
import os

bind = f"{os.environ.get('VT_HOST') or '0.0.0.0'}:{os.environ.get('VT_PORT') or '5000'}"
workers = int(os.environ.get('VT_WORKERS') or 2)
threads = 4
# Import the app (and run migrations) once in the master, before forking
preload_app = True
# NHTSA lookups can take up to 10s; leave headroom over that
timeout = 60
accesslog = '-'
errorlog = '-'
# No runtime control socket: not needed here, and the service user has no
# home directory to put it in
control_socket_disable = True
