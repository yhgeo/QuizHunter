"""快捷键录制器：弹出置顶窗口，等待用户按下组合键，自动解析成 hotkey 字符串。

Windows 用 ctypes 轮询 GetAsyncKeyState，能捕获 F1~F24 / 字母 / 数字 /
方向键 / Ctrl+Alt+Shift+Win 组合，比 Tk 的 KeyPress 可靠（Tk 在修饰键先按
下的场景会丢事件）。输出与 hotkey.parse_hotkey 兼容：F8 / Ctrl+F8 / Alt+Shift+Q。
"""

from __future__ import annotations

import ctypes
import tkinter as tk
from typing import Optional

from . import theme

try:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
except OSError:  # pragma: no cover
    user32 = None

VK_MAP = {
    "BACKSPACE": 0x08, "TAB": 0x09, "ENTER": 0x0D,
    "SPACE": 0x20, "PAGEUP": 0x21, "PAGEDOWN": 0x22,
    "HOME": 0x24, "END": 0x23,
    "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
    "INSERT": 0x2D, "DELETE": 0x2E, "PRINTSCREEN": 0x2C,
}
for _i in range(1, 25):
    VK_MAP[f"F{_i}"] = 0x6F + _i   # F1=0x70 ... F24=0x87
for _i in range(10):
    VK_MAP[str(_i)] = 0x30 + _i    # 0~9

VK_CONTROL, VK_ALT, VK_SHIFT, VK_WIN = 0x11, 0x12, 0x10, 0x5B
VK_ESC = 0x1B
MOD_VKS = [(VK_CONTROL, "Ctrl"), (VK_ALT, "Alt"), (VK_SHIFT, "Shift"), (VK_WIN, "Win")]
MOD_ORDER = ["Ctrl", "Alt", "Shift", "Win"]


def _down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000) if user32 else False


def _key_name(vk: int) -> Optional[str]:
    for name, code in VK_MAP.items():
        if code == vk:
            return name
    if 0x41 <= vk <= 0x5A:
        return chr(vk)
    return None


def format_combo(mods, key: str) -> str:
    present = set(mods)
    parts = [m for m in MOD_ORDER if m in present]
    parts.append(key)
    return "+".join(parts)


class HotkeyRecorder(tk.Toplevel):
    def __init__(self, master: tk.Misc, current: str = ""):
        super().__init__(master)
        self.title("录制快捷键")
        self.configure(bg=theme.PANEL)
        self.resizable(False, False)
        self.transient(master)
        self.result: Optional[str] = None
        self._running = True
        self._candidate: Optional[str] = None

        box = tk.Frame(self, bg=theme.PANEL, padx=28, pady=22)
        box.pack()
        tk.Label(box, text="按下想设置的组合键", bg=theme.PANEL,
                 fg=theme.FG, font=(theme.FONT, 13, "bold")).pack(pady=(0, 6))
        self.display = tk.Label(box, text=current or "请按键…", bg=theme.PANEL2,
                                fg=theme.ACCENT, font=(theme.MONO, 18, "bold"),
                                width=20, pady=10)
        self.display.pack(pady=8)
        tk.Label(box, text="支持 F1-F24 / 字母 / 方向键 + Ctrl/Alt/Shift/Win",
                 bg=theme.PANEL, fg=theme.MUTED, font=(theme.FONT, 9)).pack()
        tk.Label(box, text="单独按 ESC 取消", bg=theme.PANEL, fg=theme.MUTED,
                 font=(theme.FONT, 9)).pack(pady=(4, 0))

        self.update_idletasks()
        x = master.winfo_x() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_y() + (master.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.grab_set()
        self.focus_force()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.after(40, self._poll)

    def _poll(self) -> None:
        if not self._running:
            return
        if user32 is None:
            self.display.configure(text="当前系统不支持录制，请手动输入")
            self.after(300, self._cancel)
            return
        if _down(VK_ESC):
            self._cancel()
            return

        mods = [label for vk, label in MOD_VKS if _down(vk)]
        key = None
        # 特殊键与功能键
        for name, code in VK_MAP.items():
            if _down(code):
                key = name
                break
        # 字母
        if key is None:
            for vk in range(0x41, 0x5B):
                if _down(vk):
                    key = chr(vk)
                    break

        if key is not None:
            # 捕获到主键 → 记录候选（此时修饰键状态一并记下）
            self._candidate = format_combo(mods, key)
            self.display.configure(text=self._candidate)
        elif not mods and self._candidate is not None:
            # 全部松开且之前捕获过 → 确认
            self.result = self._candidate
            self._running = False
            self.destroy()
            return
        elif mods:
            self.display.configure(text="+".join(mods) + " + ?")

        self.after(40, self._poll)

    def _cancel(self) -> None:
        self._running = False
        self.result = None
        self.destroy()


def record_hotkey(master: tk.Misc, current: str = "") -> Optional[str]:
    """录制快捷键，返回 'Ctrl+F8' 之类；取消返回 None。"""
    recorder = HotkeyRecorder(master, current)
    master.wait_window(recorder)
    return recorder.result
