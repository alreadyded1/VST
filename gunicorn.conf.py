# gunicorn settings, loaded automatically when gunicorn starts in this directory
bind = '0.0.0.0:5000'
workers = 2
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
