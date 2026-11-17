"""深色主题与 ttk 样式。

关键点：clam 主题下 Combobox 在 readonly 状态会用系统浅色绘制字段和下拉箭头，
这就是截图里「目标窗口/识别区域/提供方」下拉框变白、字被盖住的根因。
修复靠三处：style.map 显式覆盖 readonly 的 fieldbackground/foreground/background，
并配置下拉列表框（Listbox）配色，最后把箭头按钮的浅色边框压掉。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

BG = "#11141b"
PANEL = "#191e28"
PANEL2 = "#202634"
BORDER = "#2c3444"
FG = "#e6eaf2"
MUTED = "#929daf"
ACCENT = "#4f9dff"
ACCENT2 = "#2f7fe0"
OK = "#43c88a"
WARN = "#f0b429"
ERR = "#f0616d"

FONT = "Microsoft YaHei UI"
MONO = "Consolas"


def apply(root: tk.Tk) -> None:
    root.configure(bg=BG)
    try:
        root.option_add("*Font", (FONT, 10))
    except Exception:
        pass
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    style.configure(".", background=PANEL, foreground=FG, fieldbackground=PANEL2,
                    bordercolor=BORDER, font=(FONT, 10))
    style.configure("TFrame", background=PANEL)
    style.configure("Card.TFrame", background=PANEL2, relief="flat")
    style.configure("TLabel", background=PANEL, foreground=FG)
    style.configure("Muted.TLabel", background=PANEL, foreground=MUTED)
    style.configure("Title.TLabel", background=PANEL, foreground=FG, font=(FONT, 14, "bold"))
    style.configure("Answer.TLabel", background=PANEL2, foreground=OK, font=(FONT, 26, "bold"))

    style.configure("TButton", background=PANEL2, foreground=FG, padding=(10, 6),
                    bordercolor=BORDER, focuscolor=PANEL2)
    style.map("TButton", background=[("active", BORDER), ("disabled", PANEL)],
              foreground=[("disabled", MUTED)])
    style.configure("Accent.TButton", background=ACCENT2, foreground="#ffffff")
    style.map("Accent.TButton", background=[("active", ACCENT), ("disabled", BORDER)])

    style.configure("TEntry", fieldbackground=PANEL2, foreground=FG, insertcolor=FG,
                    bordercolor=BORDER)

    # ---- Combobox：这是白底问题的核心 ----
    style.configure("TCombobox", fieldbackground=PANEL2, foreground=FG,
                    background=PANEL2, bordercolor=BORDER, arrowcolor=FG,
                    lightcolor=PANEL2, darkcolor=PANEL2, insertcolor=FG)
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", PANEL2), ("disabled", PANEL), ("focus", PANEL2), ("", PANEL2)],
        foreground=[("readonly", FG), ("disabled", MUTED), ("", FG)],
        background=[("readonly", PANEL2), ("active", BORDER), ("", PANEL2)],
        bordercolor=[("focus", ACCENT2), ("", BORDER)],
    )

    style.configure("TCheckbutton", background=PANEL, foreground=FG)
    style.map("TCheckbutton", background=[("active", PANEL)], foreground=[("disabled", MUTED)])

    style.configure("TNotebook", background=PANEL, borderwidth=0)
    style.configure("TNotebook.Tab", background=PANEL2, foreground=MUTED, padding=(14, 6))
    style.map("TNotebook.Tab", background=[("selected", BORDER)],
              foreground=[("selected", FG)])

    style.configure("Treeview", background=PANEL2, fieldbackground=PANEL2, foreground=FG,
                    rowheight=26, bordercolor=BORDER)
    style.configure("Treeview.Heading", background=BORDER, foreground=FG)
    style.map("Treeview", background=[("selected", ACCENT2)])

    style.configure("TProgressbar", background=ACCENT, troughcolor=BORDER)
    style.configure("Vertical.TScrollbar", background=PANEL2, troughcolor=PANEL)

    # 下拉列表弹出层（不是 ttk 控件，是 Listbox），必须在这里单独设色
    for opt, value in (
        ("*TCombobox*Listbox.background", PANEL2),
        ("*TCombobox*Listbox.foreground", FG),
        ("*TCombobox*Listbox.selectBackground", ACCENT2),
        ("*TCombobox*Listbox.selectForeground", "#ffffff"),
        ("*TCombobox*Listbox.activeBackground", BORDER),
        ("*TCombobox*Listbox.borderWidth", 0),
        ("*TCombobox*Listbox.relief", "flat"),
    ):
        try:
            root.option_add(opt, value)
        except Exception:
            pass
