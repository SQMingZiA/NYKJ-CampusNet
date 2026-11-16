"""后台任务辅助：把耗时操作丢到线程里，主线程只处理界面。"""

from __future__ import annotations

import traceback
from typing import Any, Callable

from PyQt5.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

_LIVE: set['Task'] = set()


class _Signals(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)


class Task(QRunnable):
    """在线程池里执行一个函数。"""

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:
            self._emit("failed", f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}")
            return
        else:
            self._emit("finished", result)

    def _emit(self, name: str, value: Any) -> None:
        """信号对象可能随窗口销毁，退出阶段再发信号会抛 RuntimeError。"""
        try:
            getattr(self.signals, name).emit(value)
        except RuntimeError:
            pass


def run_async(fn: Callable[..., Any], *args: Any, on_done: Callable[[Any], None] | None = None, on_error: Callable[[str], None] | None = None, **kwargs: Any) -> Task:
    """把 fn 丢到全局线程池执行。

    用法：``run_async(self.service.scan_wifi, True, on_done=cb)``

    ``_LIVE`` 会一直持有运行中的 Task：QThreadPool 只保证 C++ 侧的对象存活，
    Python 侧如果没人引用，``Task.signals`` 可能先被回收，
    任务跑完再发信号就会抛 "wrapped C/C++ object has been deleted"。
    """
    task = Task(fn, *args, **kwargs)
    if on_done is not None:
        task.signals.finished.connect(on_done)
    if on_error is not None:
        task.signals.failed.connect(on_error)
    _LIVE.add(task)
    task.signals.finished.connect(lambda _result: _LIVE.discard(task))
    task.signals.failed.connect(lambda _error: _LIVE.discard(task))
    QThreadPool.globalInstance().start(task)
    return task
