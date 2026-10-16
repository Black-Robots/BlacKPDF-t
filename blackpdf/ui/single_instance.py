"""Instancia única: abrir un PDF con la app ya abierta lo añade como pestaña."""

from __future__ import annotations

import getpass
import json
import re

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def _server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return "BlackPDF-" + re.sub(r"[^A-Za-z0-9_-]", "_", user)


class SingleInstance(QObject):
    filesReceived = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._name = _server_name()
        self._server: QLocalServer | None = None

    def send_to_running(self, paths: list[str]) -> bool:
        """True si otra instancia recibió los archivos (esta debe terminar)."""
        socket = QLocalSocket()
        socket.connectToServer(self._name)
        if not socket.waitForConnected(400):
            return False
        socket.write(json.dumps({"open": paths}).encode("utf-8"))
        socket.flush()
        socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()
        return True

    def listen(self) -> None:
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(self._name):
            QLocalServer.removeServer(self._name)
            server.listen(self._name)
        server.newConnection.connect(self._accept)
        self._server = server

    def _accept(self) -> None:
        while self._server is not None and self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(socket.deleteLater)

    def _read(self, socket: QLocalSocket) -> None:
        data = bytes(socket.readAll())
        try:
            payload = json.loads(data.decode("utf-8"))
        except ValueError:
            return
        paths = [p for p in payload.get("open", []) if isinstance(p, str)]
        self.filesReceived.emit(paths)
