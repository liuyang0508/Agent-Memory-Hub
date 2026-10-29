"""Temporary synthetic-data server for a pinned-SSH integration test."""
import json
import os
import socket
from pathlib import Path
import uvicorn
from agent_brain.native_sync.api import create_app
from agent_brain.native_sync.hub import Hub

root = Path(__file__).resolve().parent
hub = Hub(root / 'test.sqlite')
invites = {
    'author': hub.invite('native-smoke', 'Smoke Author'),
    'reader': hub.invite('native-smoke', 'Smoke Reader'),
    'outsider': hub.invite('other-smoke', 'Outside Reader'),
}
(root / 'invites.json').write_text(json.dumps(invites))
sock = socket.socket()
sock.bind(('127.0.0.1', 0))
sock.listen()
(root / 'ready.json').write_text(json.dumps({'port': sock.getsockname()[1], 'pid': os.getpid()}))
uvicorn.Server(uvicorn.Config(create_app(hub), log_level='warning', access_log=False, loop='asyncio', http='h11')).run(sockets=[sock])
