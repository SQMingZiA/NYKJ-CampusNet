"""单实例运行。

以前点几次就有几个进程：它们各自采样、写同一个历史文件，流量会被**重复累加**。
现在用 Qt 自带的本地套接字做单实例：

* 第一个实例监听 ``CampusNet.SingleInstance``；
* 再次启动时，新进程把"把窗口调出来"发给已有实例，然后自己退出，不再建界面。

用 ``QLocalServer``（Windows 上是命名管道）而不是 win32 mutex + 自定义消息，
是因为纯 Qt 就能完成、不用额外依赖，也不需要 ctypes。
"""
from __future__ import annotations

from typing import Callable

from PyQt5.QtNetwork import QLocalServer, QLocalSocket

SERVER_NAME = "CampusNet.SingleInstance"
_MESSAGE = b"show-window"
_ACK = b"ok"
_TIMEOUT_MS = 800
_READ_TIMEOUT_MS = 400


def notify_existing() -> bool:
    """如果已有实例在跑，通知它显示窗口并返回 True。"""
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if not socket.waitForConnected(_TIMEOUT_MS):
        return False
    try:
        socket.write(_MESSAGE)
        socket.flush()
        socket.waitForBytesWritten(_TIMEOUT_MS)


        socket.waitForReadyRead(_TIMEOUT_MS)
    except Exception:
        pass
    return True


def is_running() -> bool:
    """只查询"有没有实例在跑"，**不会**把对方窗口弹出来。

    做法：连上去什么都不发就断开 —— 服务端只在收到 ``show-window`` 时才显示窗口。
    """
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    connected = socket.waitForConnected(300)
    try:
        socket.disconnectFromServer()
    except Exception:
        pass
    return connected


def create_server(on_show: Callable[[], None]) -> QLocalServer:
    """建立监听。``on_show`` 会在收到第二个实例的消息时被调用。"""

    try:
        QLocalServer.removeServer(SERVER_NAME)
    except Exception:
        pass

    server = QLocalServer()
    try:
        server.listen(SERVER_NAME)
    except Exception:
        return server

    def handle_connection() -> None:
        while server.hasPendingConnections():
            connection = server.nextPendingConnection()
            if connection is None:
                return

            def make_reader(conn: QLocalSocket):
                def read_message() -> None:
                    try:
                        data = bytes(conn.readAll())
                    except Exception:
                        data = b""
                    if not data:
                        return
                    if _MESSAGE in data:
                        try:
                            conn.write(_ACK)
                            conn.flush()
                        except Exception:
                            pass
                        try:
                            on_show()
                        except Exception:
                            pass
                        try:
                            conn.disconnectFromServer()
                        except Exception:
                            return

                return read_message

            reader = make_reader(connection)
            connection.readyRead.connect(reader)



            try:
                if connection.waitForReadyRead(_READ_TIMEOUT_MS):
                    reader()
            except Exception:
                pass

    server.newConnection.connect(handle_connection)
    return server
