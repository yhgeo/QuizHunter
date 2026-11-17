"""主窗口（Tkinter）。单栏识别界面 + 右上角弹出式面板。

布局：
┌──────────────────────────────────────────────────────┐
│ 猎答 QuizHunter      [题库][日志][记录] [⚙ 选项]        │  顶栏
├──────────────────────────────────────────────────────┤
│ 截图来源（目标窗口 / 识别区域 / 框选 / 预览 / 重置）      │
│ ────────────────────────────────────────────────────  │
│ ┌──────────────────────────────────────────────────┐ │
│ │            大字号答案（居中，动态字号）              │ │
│ └──────────────────────────────────────────────────┘ │
│ 来源 · 相似度 · 耗时                                  │
│ OCR 识别文本（占据剩余空间，紧凑）                      │
│ ────────────────────────────────────────────────────  │
│ [识别F8][连续识题][复制答案][清空]                     │
├──────────────────────────────────────────────────────┤
│ 状态栏                                                │
└──────────────────────────────────────────────────────┘

题库/日志/记录 由顶栏按钮弹出独立面板窗口，不占主界面空间。
设置由「⚙ 选项」按钮弹出独立 SettingsDialog。
耗时操作走 TaskRunner 后台线程，结果经队列回主线程。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Dict, List, Optional, Tuple

from PIL import ImageTk

from .. import APP_TITLE, __version__, screen
from .. import config as cfgmod
from ..config import AppConfig
from ..hotkey import HotkeyManager
from ..llm import LLMClient
from ..log import LogBus
from ..solver import SolveResult, Solver
from . import region_picker, theme
from . import widgets as w
from .panels import BankPanel, HistoryPanel, LogPanel
from .settings_dialog import SettingsDialog
from .tasks import TaskRunner

PANEL_TITLES = {"bank": "题库管理", "log": "运行日志", "record": "答题记录"}
RecordRow = Tuple[str, str, str, str]


class PanelWindow(tk.Toplevel):
    """可复用的浮动面板容器：关窗时只隐藏，内容与订阅保持，避免重复。"""

    def __init__(self, master: tk.Misc, title: str, factory: Callable[[tk.Misc], tk.Widget]):
        super().__init__(master)
        self.title(title)
        self.configure(bg=theme.PANEL)
        self.geometry("940x620")
        body = ttk.Frame(self, padding=6)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        factory(body).grid(row=0, column=0, sticky="nsew")
        self.protocol("WM_DELETE_WINDOW", self.hide)

    def hide(self) -> None:
        self.withdraw()

    def toggle(self) -> None:
        if self.state() == "withdrawn":
            self.deiconify()
            self.lift()
        else:
            self.withdraw()


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = AppConfig.load()
        self.log = LogBus(echo=False)
        self.solver = Solver(self.cfg, self.log)
        self.runner = TaskRunner(root, interval=40)
        self.hotkeys = HotkeyManager(self.log)
        self.windows: List[screen.WindowInfo] = []
        self._panels: Dict[str, PanelWindow] = {}
        self._records: List[RecordRow] = []          # 记录缓存，面板未创建时也不丢
        self._history_panel: Optional[HistoryPanel] = None
        self._bank_panel: Optional[BankPanel] = None
        self._continuous = False
        self._after_id: Optional[str] = None
        self._busy = False

        screen.enable_dpi_awareness()
        theme.apply(root)
        root.title(f"{APP_TITLE}  v{__version__}")
        root.geometry("840x820")
        root.minsize(720, 680)
        self._build()
        self.runner.submit(self._boot, self._on_boot, self._on_error)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ================================================================ 构建
    def _build(self) -> None:
        root = self.root
        self._build_topbar(root)

        body = ttk.Frame(root, padding=(12, 8))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        self._build_capture(body)
        self._build_result(body)
        self._build_ops(body)

        self.status = tk.Label(root, text="就绪", bg=theme.PANEL, fg=theme.MUTED,
                               anchor="w", font=(theme.FONT, 9), padx=12, pady=4)
        self.status.pack(fill="x", side="bottom")

    def _build_topbar(self, root) -> None:
        bar = ttk.Frame(root, padding=(12, 10))
        bar.pack(fill="x")
        ttk.Label(bar, text=APP_TITLE, style="Title.TLabel").pack(side="left")
        ttk.Label(bar, text=f"  v{__version__}", style="Muted.TLabel").pack(side="left")
        # 右上角：选项 + 三个面板按钮
        ttk.Button(bar, text="⚙ 选项", style="Accent.TButton",
                   command=self.open_settings).pack(side="right", padx=(2, 0))
        ttk.Button(bar, text="记录", command=lambda: self.toggle_panel("record")).pack(side="right", padx=2)
        ttk.Button(bar, text="日志", command=lambda: self.toggle_panel("log")).pack(side="right", padx=2)
        ttk.Button(bar, text="题库", command=lambda: self.toggle_panel("bank")).pack(side="right", padx=2)

    def _build_capture(self, parent) -> None:
        src = ttk.LabelFrame(parent, text=" 截图来源 ", padding=10)
        src.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        src.columnconfigure(1, weight=1)

        ttk.Label(src, text="目标窗口:").grid(row=0, column=0, sticky="w", pady=3)
        self.window_var = tk.StringVar()
        self.window_combo = ttk.Combobox(src, textvariable=self.window_var, state="readonly")
        self.window_combo.grid(row=0, column=1, columnspan=2, sticky="ew", pady=3)
        self.window_combo.bind("<<ComboboxSelected>>", self._on_window_selected)
        ttk.Button(src, text="刷新", command=self.refresh_windows).grid(row=0, column=3, padx=(6, 0))

        ttk.Label(src, text="识别区域:").grid(row=1, column=0, sticky="w", pady=3)
        self.mode_var = tk.StringVar()
        self._mode_display = {"整窗/全屏": "none", "跟随窗口": "window", "屏幕绝对": "screen"}
        self._mode_reverse = {v: k for k, v in self._mode_display.items()}
        self.mode_var.set(self._mode_reverse.get(self.cfg.region_mode, "整窗/全屏"))
        mode = ttk.Combobox(src, textvariable=self.mode_var, state="readonly", width=12,
                            values=list(self._mode_display.keys()))
        mode.grid(row=1, column=1, sticky="w", pady=3)
        mode.bind("<<ComboboxSelected>>", lambda _e: self._on_mode_changed())
        ttk.Button(src, text="框选区域", command=self.pick_region).grid(row=1, column=2, padx=6)
        ttk.Button(src, text="预览", command=self.preview).grid(row=1, column=3)

        self.region_label = ttk.Label(src, text="", style="Muted.TLabel")
        self.region_label.grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Button(src, text="重置", command=self.reset_region).grid(row=2, column=3)
        self._update_region_label()

    def _build_result(self, parent) -> None:
        res = ttk.LabelFrame(parent, text=" 识别结果 ", padding=12)
        res.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
        res.columnconfigure(0, weight=1)
        res.rowconfigure(5, weight=1)

        # ---- 答案卡片：固定高度 + 动态字号，视觉焦点 ----
        self.answer_var = tk.StringVar(value="等待识别…")
        self.answer_card = tk.Frame(res, bg=theme.PANEL2, height=132)
        self.answer_card.grid(row=0, column=0, sticky="ew")
        self.answer_card.grid_propagate(False)
        self.answer_card.columnconfigure(0, weight=1)
        self.answer_card.rowconfigure(0, weight=1)
        self.answer_lbl = tk.Label(
            self.answer_card, textvariable=self.answer_var,
            font=(theme.FONT, 52, "bold"), fg=theme.OK, bg=theme.PANEL2,
            wraplength=760, justify="center")
        self.answer_lbl.grid(row=0, column=0, sticky="nsew", padx=12, pady=8)

        self.meta_var = tk.StringVar(value="")
        ttk.Label(res, textvariable=self.meta_var, style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(8, 0))
        self.detail_var = tk.StringVar(value="")
        ttk.Label(res, textvariable=self.detail_var, style="Muted.TLabel").grid(
            row=2, column=0, sticky="w", pady=(2, 0))

        # ---- 匹配题库卡片：显示命中的题库原题与答案，便于核对 ----
        match_box = ttk.LabelFrame(res, text=" 匹配题库 ", padding=8)
        match_box.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        match_box.columnconfigure(0, weight=1)
        self.match_var = tk.StringVar(value="—")
        self.match_lbl = ttk.Label(match_box, textvariable=self.match_var,
                                   wraplength=740, justify="left", anchor="w")
        self.match_lbl.grid(row=0, column=0, sticky="ew")

        ttk.Label(res, text="OCR 识别文本:", style="Muted.TLabel").grid(
            row=4, column=0, sticky="nw", pady=(10, 0))
        ocr_wrap, self.ocr_text = w.make_text(res, height=5)
        ocr_wrap.grid(row=5, column=0, sticky="nsew", pady=(4, 0))

    def _build_ops(self, parent) -> None:
        ops = ttk.Frame(parent)
        ops.grid(row=2, column=0, sticky="ew")
        for i, (text, cmd, style) in enumerate([
            ("识别题目 (F8)", self.solve_async, "Accent.TButton"),
            ("连续识题", self.toggle_continuous, "TButton"),
            ("复制答案", self.copy_answer, "TButton"),
            ("清空", self.clear_result, "TButton"),
        ]):
            ttk.Button(ops, text=text, command=cmd, style=style).grid(
                row=0, column=i, sticky="ew", padx=3)
            ops.columnconfigure(i, weight=1)

    # ================================================================ 面板
    def toggle_panel(self, key: str) -> None:
        panel = self._panels.get(key)
        if panel is None or not panel.winfo_exists():
            if key == "bank":
                def factory(m):
                    p = BankPanel(m, self.solver, self.log, self._on_bank_changed)
                    p.refresh()
                    self._bank_panel = p
                    return p
            elif key == "log":
                def factory(m):
                    return LogPanel(m, self.log, self.runner.post)
            else:
                def factory(m):
                    p = HistoryPanel(m, self.solver, self._to_clipboard,
                                     on_clear=self._clear_records)
                    p.load_records(self._records)
                    self._history_panel = p
                    return p
            panel = PanelWindow(self.root, PANEL_TITLES[key], factory)
            self._panels[key] = panel
        panel.toggle()

    def _clear_records(self) -> None:
        self._records.clear()

    # ================================================================ 启动
    def _boot(self):
        self.log.info("正在加载题库与 OCR 引擎…")
        report = self.solver.reload_bank()
        ok, reason = self.solver.warm_ocr()
        self.windows = screen.list_windows()
        self.runner.post(self._on_windows, self.windows)
        return {"report": report, "ocr": (ok, reason)}

    def _on_boot(self, info):
        ok, reason = info["ocr"]
        if ok:
            self.log.ok(f"OCR 就绪（{self.solver.ocr.warm_elapsed:.2f}s，档位 {self.cfg.ocr_mode}）")
        else:
            self.log.warn(f"OCR 不可用：{reason}")
        self.log.info(f"题库：{info['report'].summary()}")
        self.set_status(f"就绪 · {self.solver.bank_status()}")

    # ================================================================ 选项/设置
    def open_settings(self) -> None:
        SettingsDialog(self.root, self.cfg, self._apply_settings,
                       self._fetch_models, self.reload_bank)

    def _apply_settings(self, new_cfg: AppConfig) -> None:
        new_cfg.save()
        self.cfg = new_cfg
        self.solver.apply_config(new_cfg)
        self._start_hotkey()
        self.log.ok("设置已保存并生效")
        self.set_status("设置已更新")

    def _fetch_models(self, dialog: SettingsDialog) -> None:
        client = LLMClient(self.cfg, self.log)

        def worker():
            return client.fetch_models()

        def done(data):
            models, err = data
            client.close()
            if err:
                messagebox.showwarning("模型列表", err, parent=dialog)
            else:
                messagebox.showinfo("模型列表",
                                    f"共 {len(models)} 个模型：\n" + "\n".join(models[:50]),
                                    parent=dialog)

        self.runner.submit(worker, done, lambda e: (client.close(), self._on_error(e)))

    def reload_bank(self) -> None:
        self.runner.submit(self.solver.reload_bank, self._on_bank_reloaded, self._on_error)

    def _on_bank_reloaded(self, report) -> None:
        if self._bank_panel is not None:
            self._bank_panel.refresh()
        self.log.ok(f"题库已重新加载：{report.summary()}")
        self.set_status(self.solver.bank_status())

    def _on_bank_changed(self) -> None:
        self.set_status(self.solver.bank_status())

    # ================================================================ 窗口/区域
    def refresh_windows(self) -> None:
        self.runner.submit(screen.list_windows, self._on_windows, self._on_error)

    def _on_windows(self, windows) -> None:
        self.windows = list(windows)
        labels = [w.label() for w in self.windows]
        self.window_combo.configure(values=labels)
        current = self.cfg.window_title
        match = next((w.label() for w in self.windows if w.title == current), None)
        self.window_var.set(match or (labels[0] if labels else ""))
        if match is None and labels:
            self.cfg.window_title = self.windows[0].title
        self.set_status(f"枚举到 {len(windows)} 个窗口")

    def _selected_window(self) -> Optional[screen.WindowInfo]:
        label = self.window_var.get()
        return next((w for w in self.windows if w.label() == label), None)

    def _on_window_selected(self, _event=None) -> None:
        win = self._selected_window()
        if win:
            self.cfg.window_title = win.title
            self.set_status(f"目标窗口：{win.title}")

    def _on_mode_changed(self) -> None:
        self.cfg.region_mode = self._mode_display.get(self.mode_var.get(), "none")
        self.cfg.region = []
        self._update_region_label()
        self.set_status(f"区域模式：{self.mode_var.get()}（区域已清空）")

    def _update_region_label(self) -> None:
        self.region_label.configure(text=(
            f"区域: {tuple(self.cfg.region)}" if len(self.cfg.region) == 4
            else "区域: 未设置（整窗/全屏）"))

    def pick_region(self) -> None:
        win = self._selected_window()
        rect = win.rect if win else screen.virtual_screen_rect()
        result = region_picker.pick_region(self.root, rect, self.cfg.region_mode)
        if result:
            self.cfg.region = result
            self.cfg.region_mode = "window"
            self.mode_var.set("跟随窗口")
            self._update_region_label()
            self.log.ok(f"区域已设置：{tuple(result)}（相对窗口）")

    def reset_region(self) -> None:
        self.cfg.region = []
        self.cfg.region_mode = "none"
        self.mode_var.set("整窗/全屏")
        self._update_region_label()
        self.set_status("区域已重置")

    def preview(self) -> None:
        self.runner.submit(self.solver.grab, self._show_preview, self._on_error)

    def _show_preview(self, data) -> None:
        image, error = data
        if image is None:
            messagebox.showerror("截图失败", error, parent=self.root)
            return
        top = tk.Toplevel(self.root)
        top.title(f"截图预览 · {image.width}×{image.height}")
        top.attributes("-topmost", True)
        scale = min(1.0, (self.root.winfo_screenwidth() - 80) / image.width,
                    (self.root.winfo_screenheight() - 120) / image.height)
        disp = image.resize((max(1, int(image.width * scale)),
                             max(1, int(image.height * scale))))
        photo = ImageTk.PhotoImage(disp)
        lbl = tk.Label(top, image=photo)
        lbl.pack(padx=10, pady=10)
        lbl.image = photo
        try:
            out = cfgmod.log_dir_path() / "preview.png"
            out.parent.mkdir(exist_ok=True)
            image.save(out)
            self.log.info(f"预览已保存：{out}")
        except Exception:
            pass

    # ================================================================ 识别
    def solve_async(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.set_status("识别中…")
        self.runner.submit(self.solver.capture_and_solve, self._on_solved,
                           lambda e: (self._on_error(e), self._set_idle()))

    def _set_idle(self) -> None:
        self._busy = False

    def _set_answer(self, text: str, color: str) -> None:
        """按答案长度动态调字号：短答案超大字，长答案自动缩小防溢出。"""
        size = 52 if len(text) <= 10 else (36 if len(text) <= 22 else
               (26 if len(text) <= 40 else 18))
        # 只更新变量，不要 configure(text=...)：那会解除 textvariable 绑定，
        # 导致 answer_var 与显示脱节（copy/清空都读变量）。
        self.answer_var.set(text)
        self.answer_lbl.configure(fg=color,
                                  font=(theme.FONT, size, "bold"), wraplength=760)

    def _on_solved(self, result: SolveResult) -> None:
        self._set_idle()
        if not result.ok:
            self._set_answer("未识别", theme.ERR)
            self.detail_var.set(result.error or result.detail)
            self.meta_var.set("")
        else:
            self._set_answer(result.answer,
                             theme.OK if result.source == "题库" else theme.WARN)
            self.meta_var.set(f"来源：{result.source} · {result.mode} · 相似度 {result.score:.2f}")
            parts = [f"{k} {v:.2f}s" for k, v in result.timings.items()]
            self.detail_var.set(" | ".join(parts) + f" | 合计 {result.total:.2f}s")
            self._append_record(result)
            if self.cfg.auto_copy:
                self._to_clipboard(result.answer)

        # ---- 匹配题库卡片：命中显示原题，未命中显示最接近的候选 ----
        match = result.match
        if match is not None and match.hit and match.entry is not None:
            entry = match.entry
            question = getattr(entry, "question", "") or ""
            answer = getattr(entry, "answer", "") or ""
            aliases = getattr(entry, "aliases", []) or []
            text = f"题　目：{question}\n答　案：{answer}"
            if aliases:
                text += f"\n别　名：{', '.join(str(a) for a in aliases)}"
            self.match_var.set(text)
            self.match_lbl.configure(foreground=theme.FG)
        elif result.candidates:
            lines = [f"{i}. [{c.score:.2f}] {c.answer} · {c.question[:60]}"
                     for i, c in enumerate(result.candidates, 1)]
            self.match_var.set("未命中 · 最接近的题库题目：\n" + "\n".join(lines))
            self.match_lbl.configure(foreground=theme.MUTED)
        else:
            self.match_var.set("—" if not result.ok else "（AI 作答，无匹配题库条目）")
            self.match_lbl.configure(foreground=theme.MUTED)

        w.set_readonly(self.ocr_text, False)
        self.ocr_text.delete("1.0", "end")
        self.ocr_text.insert("end", result.question or "（无识别文本）")
        w.set_readonly(self.ocr_text, True)
        self.set_status("识别完成" if result.ok else "未命中，可补题或调低阈值")

    def _append_record(self, result: SolveResult) -> None:
        import time
        row: RecordRow = (time.strftime("%H:%M:%S"), result.source, result.answer,
                          result.question.replace("\n", " ")[:80])
        self._records.append(row)
        if self._history_panel is not None:
            self._history_panel.add_record(row)

    def toggle_continuous(self) -> None:
        if self._continuous:
            self._continuous = False
            if self._after_id:
                self.root.after_cancel(self._after_id)
            self.set_status("连续识题已停止")
        else:
            self._continuous = True
            self.set_status("连续识题运行中…")
            self._continuous_tick()

    def _continuous_tick(self) -> None:
        if not self._continuous:
            return
        if not self._busy:
            self.solve_async()
        self._after_id = self.root.after(max(200, int(self.cfg.auto_interval * 1000)),
                                         self._continuous_tick)

    def copy_answer(self) -> None:
        text = self.answer_var.get()
        if text and text != "等待识别…":
            self._to_clipboard(text)
            self.set_status("答案已复制")

    def clear_result(self) -> None:
        self._set_answer("等待识别…", theme.OK)
        self.meta_var.set("")
        self.detail_var.set("")
        self.match_var.set("—")
        self.match_lbl.configure(foreground=theme.MUTED)
        w.set_readonly(self.ocr_text, False)
        self.ocr_text.delete("1.0", "end")
        w.set_readonly(self.ocr_text, True)

    def _to_clipboard(self, text: str) -> None:
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except Exception:
            pass

    # ================================================================ 辅助
    def _start_hotkey(self) -> None:
        err = self.hotkeys.start(self.cfg.hotkey, self._hotkey_callback)
        if err:
            self.log.warn(err)
        else:
            self.log.info(f"全局热键：{self.cfg.hotkey}")

    def _hotkey_callback(self) -> None:
        self.runner.post(self.solve_async)

    def set_status(self, text: str) -> None:
        self.status.configure(text=text)

    def _on_error(self, exc) -> None:
        self.log.error(f"操作失败：{exc}")
        self.set_status(f"错误：{exc}")

    def on_close(self) -> None:
        self._continuous = False
        if self._after_id:
            try:
                self.root.after_cancel(self._after_id)
            except Exception:
                pass
        self.hotkeys.stop()
        self.solver.llm.close()
        try:
            self.cfg.save()
        except Exception:
            pass
        self.root.destroy()


def launch() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()
