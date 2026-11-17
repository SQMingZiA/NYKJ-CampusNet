"""单实例运行。

以前点几次就有几个进程：它们各自采样、写同一个历史文件，流量会被**重复累加**。
现在用 Qt 自带的本地套接字做单实例：

* 第一个实例监听 ``CampusNet.SingleInstance``（激活）与 ``CampusNet.SingleInstance.Probe``（探活）；
* 再次启动时，新进程连上「激活」通道就算把消息送到了 —— 已有实例立刻把窗口调出来，
  新进程自己退出，不再建界面。

用连接本身当消息，而不是往管道里写字节：Windows 命名管道的首包经常在
``flush()`` 之后还留在缓冲区里（实测 ``waitForBytesWritten`` 直接返回 False），
依赖写成功会偶发「点了没反应」。连接建立是内核事件，不会丢。

命名管道在「一边提权、一边没提权」时可能连不上，所以再加一个文件标记兜底：
新进程写 ``.show-request``，已有实例每 400ms 看一眼，看到就删掉并把窗口调出来。
"""
from __future__ import annotations

import os
import time
from typing import Callable

from PyQt5.QtCore import QTimer
from PyQt5.QtNetwork import QLocalServer, QLocalSocket

SERVER_NAME = "CampusNet.SingleInstance"
PROBE_NAME = "CampusNet.SingleInstance.Probe"
_TIMEOUT_MS = 800
_RETRIES = 3
_RETRY_DELAY = 0.15
_POLL_MS = 400
MARKER_NAME = ".show-request"


def _connect(name: str, timeout_ms: int = _TIMEOUT_MS) -> bool:
    socket = QLocalSocket()
    socket.connectToServer(name)
    connected = socket.waitForConnected(timeout_ms)
    try:
        socket.disconnectFromServer()
    except Exception:
        pass
    return connected


def notify_existing() -> bool:
    """如果已有实例在跑，通知它显示窗口并返回 True。

    连上「激活」通道就代表消息送到了：服务端收到连接就调 ``on_show``，
    不需要我们再写内容，也不等 ACK。
    """
    for attempt in range(_RETRIES):
        if _connect(SERVER_NAME):
            return True
        if attempt + 1 < _RETRIES:
            time.sleep(_RETRY_DELAY)
    return False


def is_running() -> bool:
    """只查询"有没有实例在跑"，**不会**把对方窗口弹出来（走独立的探活通道）。"""
    return _connect(PROBE_NAME, 300)


def _marker_path(data_dir: str) -> str:
    return os.path.join(data_dir or "", MARKER_NAME)


def request_show(data_dir: str) -> bool:
    """管道连不上时的兜底：留一个标记文件，让已有实例自己把窗口调出来。"""
    if not data_dir:
        return False
    try:
        os.makedirs(data_dir, exist_ok=True)
        with open(_marker_path(data_dir), "w", encoding="utf-8") as handle:
            handle.write(str(int(time.time())))
        return True
    except OSError:
        return False


def consume_show_request(data_dir: str) -> bool:
    """读到标记就删掉并返回 True。"""
    path = _marker_path(data_dir)
    try:
        if not os.path.exists(path):
            return False
        os.remove(path)
        return True
    except OSError:
        return False


def clear_show_request(data_dir: str) -> None:
    """启动时清掉上次遗留的标记，免得一开机就自己弹窗口。"""
    path = _marker_path(data_dir)
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _listen(name: str, on_connection: Callable[[], None]) -> QLocalServer:
    try:
        QLocalServer.removeServer(name)
    except Exception:
        pass

    server = QLocalServer()
    try:
        server.listen(name)
    except Exception:
        pass

    def handle_connection() -> None:
        while server.hasPendingConnections():
            connection = server.nextPendingConnection()
            if connection is None:
                return
            try:
                connection.disconnectFromServer()
            except Exception:
                pass
            try:
                connection.deleteLater()
            except Exception:
                pass

        try:
            on_connection()
        except Exception:
            pass

    server.newConnection.connect(handle_connection)
    return server


def create_server(on_show: Callable[[], None], data_dir: str = "") -> QLocalServer:
    """建立监听。``on_show`` 会在第二个实例启动时被调用。"""

    clear_show_request(data_dir)
    server = _listen(SERVER_NAME, on_show)
    probe = _listen(PROBE_NAME, lambda: None)
    server._probe_server = probe  # 跟着主 server 一起活，避免被 GC

    if data_dir:
        def poll_marker() -> None:
            if consume_show_request(data_dir):
                try:
                    on_show()
                except Exception:
                    pass

        timer = QTimer(server)
        timer.setInterval(_POLL_MS)
        timer.timeout.connect(poll_marker)
        timer.start()
        server._show_poll = timer  # 跟着 server 一起活，避免被 GC

    return server
