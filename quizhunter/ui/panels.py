"""副面板：题库 / 日志 / 记录。

各自封装成类，只依赖传给它的 solver / log 与少量回调，主窗口 app.py 负责装配。
"""

from __future__ import annotations

import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, List, Optional

from ..bank import BankEntry
from ..log import LogBus, Record
from ..solver import SolveResult, Solver
from . import theme
from . import widgets as w


class BankPanel(ttk.Frame):
    """题库管理表格。"""

    def __init__(self, master, solver: Solver, log: LogBus, on_changed: Callable[[], None]):
        super().__init__(master, padding=10)
        self.solver = solver
        self.log = log
        self.on_changed = on_changed
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._build()

    def _build(self) -> None:
        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(top, text="搜索:").pack(side="left")
        self.search_var = tk.StringVar()
        se = ttk.Entry(top, textvariable=self.search_var, width=22)
        se.pack(side="left", padx=4)
        se.bind("<Return>", lambda _e: self.refresh())
        for text, cmd in [("查找", self.refresh), ("新增", self.add), ("编辑", self.edit),
                          ("删除", self.delete), ("导入", self.import_file),
                          ("导出", self.export_file)]:
            ttk.Button(top, text=text, command=cmd).pack(side="left", padx=2)

        cols = ("question", "answer", "source")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=16)
        self.tree.heading("question", text="题目")
        self.tree.heading("answer", text="答案")
        self.tree.heading("source", text="来源")
        self.tree.column("question", width=520, anchor="w")
        self.tree.column("answer", width=150, anchor="w")
        self.tree.column("source", width=150, anchor="w")
        self.tree.grid(row=1, column=0, sticky="nsew")
        self.tree.bind("<Double-1>", lambda _e: self.edit())
        self.tree.tag_configure("ro", foreground=theme.MUTED)
        scroll = ttk.Scrollbar(self, command=self.tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.count = ttk.Label(self, text="", style="Muted.TLabel")
        self.count.grid(row=2, column=0, sticky="w", pady=(4, 0))

    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        keyword = self.search_var.get().strip()
        entries = self.solver.bank.search(keyword) if keyword else self.solver.bank.entries
        for e in entries:
            self.tree.insert("", "end", iid=e.entry_id,
                             values=(e.preview(80), e.answer, e.source),
                             tags=("ro",) if not e.writable else ())
        self.count.configure(text=f"显示 {len(entries)} / 共 {len(self.solver.bank.entries)} 条")

    def _selected(self) -> Optional[BankEntry]:
        sel = self.tree.selection()
        return self.solver.bank.get(sel[0]) if sel else None

    def _entry_dialog(self, entry=None):
        dlg = tk.Toplevel(self)
        dlg.title("编辑题目" if entry else "新增题目")
        dlg.transient(self.winfo_toplevel())
        dlg.resizable(False, False)
        dlg.configure(bg=theme.PANEL)
        q = tk.StringVar(value=entry.question if entry else "")
        a = tk.StringVar(value=entry.answer if entry else "")
        al = tk.StringVar(value=",".join(entry.aliases) if entry else "")
        for i, (lab, var) in enumerate([("题目:", q), ("答案:", a), ("别名(逗号分隔):", al)]):
            ttk.Label(dlg, text=lab).grid(row=i, column=0, sticky="w", padx=10, pady=8)
            ttk.Entry(dlg, textvariable=var, width=48).grid(row=i, column=1, padx=10, pady=8)
        result = {}

        def ok():
            result.update(ok=True, q=q.get().strip(), a=a.get().strip(),
                          al=[x.strip() for x in al.get().split(",") if x.strip()])
            dlg.destroy()

        ttk.Button(dlg, text="确定", style="Accent.TButton", command=ok).grid(
            row=3, column=1, sticky="e", padx=10, pady=10)
        dlg.grab_set()
        self.winfo_toplevel().wait_window(dlg)
        return result if result.get("ok") else None

    def _after_change(self) -> None:
        self.solver.matcher.set_entries(self.solver.bank.entries)
        self.refresh()
        self.on_changed()

    def add(self) -> None:
        data = self._entry_dialog()
        if data:
            try:
                self.solver.bank.add(data["q"], data["a"], data["al"])
                self._after_change()
                self.log.ok("题目已新增")
            except Exception as exc:
                messagebox.showerror("错误", str(exc), parent=self)

    def edit(self) -> None:
        entry = self._selected()
        if not entry:
            return
        if not entry.writable:
            messagebox.showinfo("只读题库", "该题目来自只读题库，请直接改对应 JSON 文件。", parent=self)
            return
        data = self._entry_dialog(entry)
        if data:
            try:
                self.solver.bank.update(entry.entry_id, data["q"], data["a"], data["al"])
                self._after_change()
                self.log.ok("题目已更新")
            except Exception as exc:
                messagebox.showerror("错误", str(exc), parent=self)

    def delete(self) -> None:
        entry = self._selected()
        if not entry:
            return
        if not entry.writable:
            messagebox.showinfo("只读题库", "只读题库条目无法删除。", parent=self)
            return
        if messagebox.askyesno("确认删除", f"删除题目？\n{entry.preview(60)}", parent=self):
            try:
                self.solver.bank.delete(entry.entry_id)
                self._after_change()
                self.log.ok("题目已删除")
            except Exception as exc:
                messagebox.showerror("错误", str(exc), parent=self)

    def import_file(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")], parent=self)
        if not path:
            return
        added, skipped, err = self.solver.bank.import_file(Path(path))
        if err:
            messagebox.showerror("导入失败", err, parent=self)
        else:
            self._after_change()
            messagebox.showinfo("导入完成", f"新增 {added} 条，跳过 {skipped} 条", parent=self)

    def export_file(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".json",
                                            filetypes=[("JSON", "*.json")], parent=self)
        if not path:
            return
        count = self.solver.bank.export_all(Path(path))
        messagebox.showinfo("导出完成", f"已导出 {count} 条到\n{path}", parent=self)


class LogPanel(ttk.Frame):
    """实时日志面板，订阅 LogBus。"""

    def __init__(self, master, log: LogBus, post):
        super().__init__(master, padding=8)
        self.log = log
        self.post = post
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.text, self._wrap = self._build()
        self.log.subscribe(lambda rec: self.post(self._append, rec))

    def _build(self):
        wrap, text = w.make_text(self, mono=True, height=14)
        wrap.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Frame(self)
        bar.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        ttk.Button(bar, text="清空日志", command=lambda: self._clear()).pack(side="left")
        # 回填已有记录：此时 self.text 尚未赋值（在 _build 返回后才赋值），
        # 必须直接用局部 text，不能调用依赖 self.text 的 _append。
        text.configure(state="normal")
        for rec in self.log.snapshot():
            text.insert("end", f"[{rec.clock}] {rec.prefix} {rec.message}\n")
        text.configure(state="disabled")
        text.see("end")
        return text, wrap

    def _append(self, rec: Record) -> None:
        w.set_readonly(self.text, False)
        self.text.insert("end", f"[{rec.clock}] {rec.prefix} {rec.message}\n")
        w.set_readonly(self.text, True)
        self.text.see("end")

    def _clear(self) -> None:
        w.set_readonly(self.text, False)
        self.text.delete("1.0", "end")
        w.set_readonly(self.text, True)


class HistoryPanel(ttk.Frame):
    """答题记录与统计。记录行由外部 App 缓存维护，面板只负责渲染。"""

    def __init__(self, master, solver: Solver, on_copy: Callable[[str], None],
                 on_clear: Optional[Callable[[], None]] = None):
        super().__init__(master, padding=8)
        self.solver = solver
        self.on_copy = on_copy
        self.on_clear = on_clear
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self._build()

    def _build(self) -> None:
        cols = ("time", "source", "answer", "question")
        self.tree = ttk.Treeview(self, columns=cols, show="headings")
        for c, t, wd in [("time", "时间", 78), ("source", "来源", 88),
                         ("answer", "答案", 200), ("question", "题目", 460)]:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=wd, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<Double-1>", lambda _e: self._copy_selected())
        scroll = ttk.Scrollbar(self, command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        bar = ttk.Frame(self)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.stats = ttk.Label(bar, text="共 0 次 · 命中率 0%", style="Muted.TLabel")
        self.stats.pack(side="left")
        ttk.Button(bar, text="清空记录", command=self.clear).pack(side="right")

    def _refresh_stats(self) -> None:
        s = self.solver.stats.as_dict()
        self.stats.configure(text=(
            f"共 {s['total']} 次 · 题库 {s['bank']} · AI {s['ai']} · "
            f"无匹配 {s['miss']} · 命中率 {s['hit_rate']:.0f}%"))

    def load_records(self, rows) -> None:
        """从外部缓存补齐历史行（面板延迟创建时用）。"""
        for row in rows:
            self.tree.insert("", "end", values=row)
        self._refresh_stats()

    def add_record(self, row) -> None:
        self.tree.insert("", "end", values=row)
        self.tree.see(self.tree.get_children()[-1])
        self._refresh_stats()

    def add(self, result: SolveResult) -> None:
        self.add_record((time.strftime("%H:%M:%S"), result.source, result.answer,
                         result.question.replace("\n", " ")[:80]))

    def _copy_selected(self) -> None:
        sel = self.tree.selection()
        if sel:
            self.on_copy(str(self.tree.item(sel[0], "values")[2]))

    def clear(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.solver.stats.reset()
        self.stats.configure(text="共 0 次 · 命中率 0%")
        if self.on_clear:
            self.on_clear()
