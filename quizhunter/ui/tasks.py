"""后台线程 → 主线程回调桥。

Tk 不是线程安全的：后台线程只把 (fn, args, kwargs) 塞进队列，
主线程的 after 轮询消费并在主线程执行回调。
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from typing import Any, Callable, Optional, Tuple


class TaskRunner:
    def __init__(self, root: tk.Misc, interval: int = 40):
        self.root = root
        self._queue: "queue.Queue[Tuple[Callable, tuple, dict]]" = queue.Queue()
        self._busy = 0
        self._lock = threading.Lock()
        self._interval = interval
        self._pump()

    def submit(self, worker: Callable, callback: Optional[Callable] = None,
               error_callback: Optional[Callable] = None) -> None:
        """在后台线程跑 worker；完成后 callback(result) 回到主线程。"""
        with self._lock:
            self._busy += 1

        def run() -> None:
            try:
                result = worker()
                success = True
            except Exception as exc:  # noqa: BLE001 - 边界处统一兜住
                result, success = exc, False
            self.post(lambda: self._finish(success, result, callback, error_callback))

        threading.Thread(target=run, daemon=True).start()

    def _finish(self, success: bool, result: Any, callback, error_callback) -> None:
        with self._lock:
            self._busy -= 1
        try:
            if success and callback:
                callback(result)
            elif not success and error_callback:
                error_callback(result)
        finally:
            self.on_idle_changed(self._busy)

    def post(self, fn: Callable, *args, **kwargs) -> None:
        self._queue.put((fn, args, kwargs))

    def on_idle_changed(self, busy: int) -> None:
        """子类可覆写：更新进度条/状态。"""

    @property
    def busy(self) -> int:
        with self._lock:
            return self._busy

    def _pump(self) -> None:
        try:
            while True:
                fn, args, kwargs = self._queue.get_nowait()
                try:
                    fn(*args, **kwargs)
                except Exception:
                    pass
        except queue.Empty:
            pass
        self.root.after(self._interval, self._pump)
