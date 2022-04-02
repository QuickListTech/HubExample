#!/usr/bin/python
from flup.server.fcgi import WSGIServer
from flask_uds import app

if __name__ == '__main__':
    WSGIServer(app, bindAddress='/tmp/fcgi.sock').run()
