"""线程安全日志总线：控制台输出 + 环形缓冲 + 订阅回调。

GUI 用它把后台线程的日志安全地推到界面（订阅回调里只放队列，不碰 Tk 控件）。
"""

from __future__ import annotations

import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, List, Optional

LEVELS = {"debug": 10, "info": 20, "ok": 25, "warn": 30, "error": 40}


@dataclass(frozen=True)
class Record:
    ts: float
    level: str
    message: str

    @property
    def clock(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.ts))

    @property
    def prefix(self) -> str:
        return {"debug": "·", "info": "·", "ok": "✓", "warn": "!", "error": "×"}.get(
            self.level, "·")


class LogBus:
    def __init__(self, capacity: int = 3000, echo: bool = True, min_level: str = "info"):
        self._records: Deque[Record] = deque(maxlen=capacity)
        self._listeners: List[Callable[[Record], None]] = []
        self._lock = threading.RLock()
        self._echo = echo
        self._min = LEVELS.get(min_level, 20)

    def subscribe(self, cb: Callable[[Record], None]) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(cb)

        def unsubscribe() -> None:
            with self._lock:
                if cb in self._listeners:
                    self._listeners.remove(cb)

        return unsubscribe

    def snapshot(self) -> List[Record]:
        with self._lock:
            return list(self._records)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def log(self, level: str, message: str) -> None:
        level = level if level in LEVELS else "info"
        if LEVELS[level] < self._min:
            return
        rec = Record(time.time(), level, str(message))
        with self._lock:
            self._records.append(rec)
            listeners = list(self._listeners)
        if self._echo:
            stream = sys.stderr if level == "error" else sys.stdout
            try:
                print(f"[{rec.clock}] {rec.prefix} {rec.message}", file=stream, flush=True)
            except Exception:
                pass
        for cb in listeners:
            try:
                cb(rec)
            except Exception:
                pass

    def debug(self, m: str) -> None: self.log("debug", m)
    def info(self, m: str) -> None: self.log("info", m)
    def ok(self, m: str) -> None: self.log("ok", m)
    def warn(self, m: str) -> None: self.log("warn", m)
    def error(self, m: str) -> None: self.log("error", m)
