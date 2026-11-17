"""全局热键（Win32 RegisterHotKey）。

系统级注册，稳定且不需要管理员权限；独立线程维护消息循环，stop 用
PostThreadMessage(WM_QUIT) 退出。支持 F1~F24、字母数字、PrintScreen、Space 等，
修饰键 Ctrl/Alt/Shift/Win 可组合，如 Ctrl+F8、Alt+Shift+Q。
"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from typing import Callable, Optional, Tuple

try:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
except OSError:  # pragma: no cover
    user32 = kernel32 = None

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x0008, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012

_MODIFIERS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT,
              "shift": MOD_SHIFT, "win": MOD_WIN, "meta": MOD_WIN, "cmd": MOD_WIN}
_SPECIAL_VK = {"printscreen": 0x2C, "prtsc": 0x2C, "space": 0x20, "tab": 0x09,
               "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B,
               "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
               "pageup": 0x21, "pagedown": 0x22,
               "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28}


def parse_hotkey(spec: str) -> Tuple[int, int]:
    """'Ctrl+F8' -> (modifiers, vk)。解析失败抛 ValueError。"""
    raw = (spec or "").strip()
    if not raw:
        raise ValueError("热键为空")
    parts = [p.strip().lower() for p in raw.replace("-", "+").split("+") if p.strip()]
    modifiers = 0
    key_token = ""
    for part in parts:
        if part in _MODIFIERS:
            modifiers |= _MODIFIERS[part]
        else:
            key_token = part
    if not key_token:
        raise ValueError(f"热键缺少主键: {spec!r}")
    if key_token in _SPECIAL_VK:
        return modifiers, _SPECIAL_VK[key_token]
    if len(key_token) == 1 and key_token.isalnum():
        return modifiers, ord(key_token.upper())
    if key_token.startswith("f") and key_token[1:].isdigit():
        number = int(key_token[1:])
        if 1 <= number <= 24:
            return modifiers, 0x70 + number - 1
    if key_token.startswith("num") and key_token[3:].isdigit():
        return modifiers, 0x60 + int(key_token[3:])
    raise ValueError(f"无法识别按键: {key_token!r}")


class HotkeyListener(threading.Thread):
    HOTKEY_ID = 0xA51F

    def __init__(self, spec: str, callback: Callable[[], None], log=None, daemon: bool = True):
        super().__init__(name="hotkey-listener", daemon=daemon)
        self.spec, self.callback, self.log = spec, callback, log
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread_id: Optional[int] = None
        self._error = ""
        self._mods, self._vk = parse_hotkey(spec)

    @property
    def error(self) -> str:
        return self._error

    def wait_ready(self, timeout: float = 2.0) -> bool:
        return self._ready.wait(timeout)

    def run(self) -> None:
        if user32 is None:
            self._error = "当前系统不支持全局热键"
            self._ready.set()
            return
        self._thread_id = kernel32.GetCurrentThreadId()
        registered = user32.RegisterHotKey(None, self.HOTKEY_ID,
                                           self._mods | MOD_NOREPEAT, self._vk)
        if not registered:
            self._error = f"热键 {self.spec} 注册失败（可能被其他程序占用）"
            if self.log:
                self.log.warn(self._error)
            self._ready.set()
            return
        if self.log:
            self.log.info(f"全局热键已生效：{self.spec}")
        self._ready.set()

        message = wintypes.MSG()
        try:
            while not self._stop.is_set():
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result in (0, -1):
                    break
                if message.message == WM_HOTKEY:
                    try:
                        self.callback()
                    except Exception as exc:
                        if self.log:
                            self.log.error(f"热键回调异常: {exc}")
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            user32.UnregisterHotKey(None, self.HOTKEY_ID)

    def stop(self) -> None:
        self._stop.set()
        thread_id = self._thread_id
        if thread_id:
            for _ in range(20):
                if user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0):
                    break
                time.sleep(0.02)


class HotkeyManager:
    """管理单个热键的启停，支持运行中更换组合键。"""

    def __init__(self, log=None):
        self.log = log
        self._listener: Optional[HotkeyListener] = None

    @property
    def active(self) -> bool:
        return self._listener is not None and self._listener.is_alive()

    def start(self, spec: str, callback: Callable[[], None]) -> str:
        self.stop()
        try:
            listener = HotkeyListener(spec, callback, self.log)
        except ValueError as exc:
            return str(exc)
        listener.start()
        listener.wait_ready(1.5)
        if listener.error:
            return listener.error
        self._listener = listener
        return ""

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
