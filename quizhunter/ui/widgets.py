"""通用控件工厂：统一深色风格的表单行、卡片、小按钮，减少重复代码。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Dict, Optional

from . import theme


def section(parent, text: str, row: int, colspan: int = 2) -> None:
    ttk.Label(parent, text=text, style="Title.TLabel").grid(
        row=row, column=0, columnspan=colspan, sticky="w", pady=(12, 6))


def entry_row(page, vars: Dict[str, tk.Variable], row: int, key: str, label: str,
              default, width: int = 34) -> int:
    ttk.Label(page, text=label).grid(row=row, column=0, sticky="w", pady=3)
    var = tk.StringVar(value=str(default))
    ttk.Entry(page, textvariable=var, width=width).grid(row=row, column=1, sticky="ew", pady=3)
    vars[key] = var
    return row + 1


def check_row(page, vars: Dict[str, tk.Variable], row: int, key: str, label: str,
              default: bool) -> int:
    var = tk.BooleanVar(value=bool(default))
    ttk.Checkbutton(page, text=label, variable=var).grid(
        row=row, column=0, columnspan=2, sticky="w", pady=2)
    vars[key] = var
    return row + 1


def combo_row(page, vars: Dict[str, tk.Variable], row: int, key: str, label: str,
              values, default: str, on_change=None) -> int:
    """values 支持字符串列表，或 (内部值, 显示文本) 元组列表。

    当传入元组列表时，vars[key] 存的是「显示文本」，调用方需用映射转回内部值；
    可通过返回前附加 vars[key + '__map'] 取得该映射。
    """
    ttk.Label(page, text=label).grid(row=row, column=0, sticky="w", pady=3)
    var = tk.StringVar(value=default)
    display = []
    mapping = {}
    for item in values:
        if isinstance(item, (tuple, list)) and len(item) == 2:
            mapping[item[1]] = item[0]
            display.append(item[1])
        else:
            display.append(str(item))
    combo = ttk.Combobox(page, textvariable=var, state="readonly", values=display)
    combo.grid(row=row, column=1, sticky="ew", pady=3)
    if on_change:
        def _changed(_e=None):
            sel = var.get()
            on_change(mapping.get(sel, sel))
        combo.bind("<<ComboboxSelected>>", _changed)
    vars[key] = var
    if mapping:
        vars[key + "__map"] = mapping
    return row + 1


def set_readonly(text: tk.Text, readonly: bool) -> None:
    text.configure(state="disabled" if readonly else "normal")


def make_text(parent, *, mono: bool = False, height: int = 6):
    wrap = ttk.Frame(parent)
    wrap.columnconfigure(0, weight=1)
    wrap.rowconfigure(0, weight=1)
    text = tk.Text(wrap, height=height, wrap="word", bg=theme.PANEL2, fg=theme.FG,
                   insertbackground=theme.FG, relief="flat",
                   font=(theme.MONO if mono else theme.FONT, 9 if mono else 10))
    text.grid(row=0, column=0, sticky="nsew")
    scroll = ttk.Scrollbar(wrap, command=text.yview)
    scroll.grid(row=0, column=1, sticky="ns")
    text.configure(yscrollcommand=scroll.set)
    set_readonly(text, True)
    return wrap, text
