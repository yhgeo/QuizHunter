"""设置窗口：由主界面「选项」按钮弹出的独立模态页面。

四个分区：模型 / 题库与匹配 / OCR 与交互 / 请求。
只负责收集字段并回调 on_save(new_config)；拉取模型列表走 on_fetch_models。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Dict, Optional

from ..config import PROVIDER_PRESETS, AppConfig, default_base_url
from . import theme
from . import widgets as w


class SettingsDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, cfg: AppConfig,
                 on_save: Callable[[AppConfig], None],
                 on_fetch_models: Optional[Callable[[], None]] = None,
                 on_reload_bank: Optional[Callable[[], None]] = None):
        super().__init__(master)
        self.title("选项 · 设置")
        self.configure(bg=theme.PANEL)
        self.resizable(True, True)
        self.transient(master)
        self._cfg = cfg
        self._on_save = on_save
        self._on_fetch = on_fetch_models
        self._on_reload = on_reload_bank
        self.vars: Dict[str, tk.Variable] = {}
        self._build()
        self.geometry("720x680")
        self.update_idletasks()
        x = master.winfo_x() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_y() + (master.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.grab_set()
        self.focus_force()

    def _build(self) -> None:
        main = ttk.Frame(self, padding=14)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(0, weight=1)

        nb = ttk.Notebook(main)
        nb.grid(row=0, column=0, sticky="nsew")
        settings_page = ttk.Frame(nb, padding=(8, 10))
        help_page = ttk.Frame(nb, padding=(8, 10))
        nb.add(settings_page, text="参数设置")
        nb.add(help_page, text="配置说明")
        settings_page.columnconfigure(0, weight=1)
        settings_page.rowconfigure(0, weight=1)
        help_page.columnconfigure(0, weight=1)
        help_page.rowconfigure(0, weight=1)
        self._build_help(help_page)

        canvas = tk.Canvas(settings_page, bg=theme.PANEL, highlightthickness=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(settings_page, orient="vertical", command=canvas.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scroll.set)
        inner = ttk.Frame(canvas, padding=(4, 4))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        inner.columnconfigure(1, weight=1)

        cfg = self._cfg
        r = 0
        w.section(inner, "模型（可选，题库未命中时兜底）", r); r += 1
        r = w.check_row(inner, self.vars, r, "model_enabled", "启用大模型答题", cfg.model_enabled)
        r = w.combo_row(inner, self.vars, r, "provider_name", "提供方:",
                        [p[0] for p in PROVIDER_PRESETS],
                        cfg.provider_name or PROVIDER_PRESETS[0][0],
                        on_change=self._on_provider)
        r = w.entry_row(inner, self.vars, r, "base_url", "Base URL:", cfg.base_url, 44)
        r = w.entry_row(inner, self.vars, r, "api_key", "API Key:", cfg.api_key, 44)
        r = w.entry_row(inner, self.vars, r, "model", "模型 ID:", cfg.model, 44)
        ttk.Button(inner, text="拉取模型列表", command=self._fetch).grid(
            row=r, column=1, sticky="w", pady=4); r += 1
        r = w.entry_row(inner, self.vars, r, "background_prompt", "答题背景提示词:",
                        cfg.background_prompt, 44)

        w.section(inner, "题库与匹配", r); r += 1
        r = w.check_row(inner, self.vars, r, "bank_enabled", "启用题库优先", cfg.bank_enabled)
        r = w.entry_row(inner, self.vars, r, "similarity_threshold", "相似度阈值 (0-1):",
                        cfg.similarity_threshold, 12)
        r = w.entry_row(inner, self.vars, r, "substring_threshold", "子串覆盖阈值 (0-1):",
                        cfg.substring_threshold, 12)
        r = w.entry_row(inner, self.vars, r, "min_query_length", "最短查询长度:",
                        cfg.min_query_length, 12)
        r = w.entry_row(inner, self.vars, r, "min_contains_length", "包含匹配最短长度:",
                        cfg.min_contains_length, 12)

        w.section(inner, "OCR 识别（速度档位直接影响识题耗时）", r); r += 1
        r = w.combo_row(inner, self.vars, r, "ocr_mode", "速度档位:",
                        [("fast", "极速 (1 变体)"), ("balance", "均衡 (默认)"),
                         ("accurate", "精准 (多变体投票)")],
                        self._mode_label(cfg.ocr_mode))
        self._mode_map = self.vars.get("ocr_mode__map", {})
        r = w.entry_row(inner, self.vars, r, "ocr_scale", "OCR 放大倍数:", cfg.ocr_scale, 12)
        r = w.check_row(inner, self.vars, r, "ocr_auto_invert", "暗底自动反色", cfg.ocr_auto_invert)
        r = w.check_row(inner, self.vars, r, "ocr_binarize", "硬二值化 (一般关闭)", cfg.ocr_binarize)
        r = w.check_row(inner, self.vars, r, "ocr_use_cls", "方向分类 (屏幕文字建议关闭)",
                        cfg.ocr_use_cls)
        r = w.entry_row(inner, self.vars, r, "ocr_det_limit_side", "检测边长上限:",
                        cfg.ocr_det_limit_side, 12)

        w.section(inner, "交互与请求", r); r += 1
        # 全局热键：只读显示 + 录制按钮（按键自动填入）
        from .hotkey_capture import record_hotkey
        ttk.Label(inner, text="全局热键:").grid(row=r, column=0, sticky="w", pady=3)
        hk_var = tk.StringVar(value=cfg.hotkey)
        self.vars["hotkey"] = hk_var
        hk_row = ttk.Frame(inner)
        hk_row.grid(row=r, column=1, sticky="w", pady=3)
        hk_entry = ttk.Entry(hk_row, textvariable=hk_var, width=14, state="readonly")
        hk_entry.pack(side="left")

        def _record():
            captured = record_hotkey(self, hk_var.get())
            if captured:
                hk_var.set(captured)
                self.hint.configure(text=f"已录制：{captured}")

        ttk.Button(hk_row, text="录制按键", command=_record).pack(side="left", padx=6)
        r += 1
        r = w.entry_row(inner, self.vars, r, "auto_interval", "连续识题间隔 (秒):",
                        cfg.auto_interval, 12)
        r = w.check_row(inner, self.vars, r, "auto_copy", "识别成功自动复制", cfg.auto_copy)
        r = w.entry_row(inner, self.vars, r, "request_timeout", "请求超时 (秒):",
                        cfg.request_timeout, 12)
        r = w.entry_row(inner, self.vars, r, "max_tokens", "最大 token:", cfg.max_tokens, 12)

        bar = ttk.Frame(inner)
        bar.grid(row=r + 1, column=0, columnspan=2, sticky="ew", pady=(16, 4))
        ttk.Button(bar, text="保存并关闭", style="Accent.TButton",
                   command=self._save).pack(side="left")
        ttk.Button(bar, text="取消", command=self.destroy).pack(side="left", padx=6)
        if self._on_reload:
            ttk.Button(bar, text="重新加载题库", command=self._on_reload).pack(side="left", padx=6)
        self.hint = ttk.Label(bar, text="", style="Muted.TLabel")
        self.hint.pack(side="left", padx=10)

    def _build_help(self, parent) -> None:
        """配置说明页：逐项解释配置含义、推荐值与相互影响。"""
        help_text = """【整体工作流】
截图 → 离线 OCR 识题 → 本地题库模糊匹配 → 未命中才（可选）调用 AI。
只有题库没匹配且你开了模型时才联网，纯题库模式全程离线。

────────────────────────────────────────
【模型（可选）】
· 启用大模型答题：关掉就是纯题库模式，全程离线。
· 提供方 / Base URL / API Key / 模型 ID：任何一个 OpenAI 兼容接口都能用。
  选好提供方会自动填 Base URL，可手动改；API Key 明文存在本地 config.json。
· 拉取模型列表：接口支持 /models 时自动列出，不支持就手填模型 ID。
· 答题背景提示词：给 AI 的补充背景，如「本场为体育理论考试」，可留空。
· 请求超时 / 最大 token：AI 响应慢就调大超时；答案长就调大 token。

────────────────────────────────────────
【题库与匹配】
· 启用题库优先：开着才会用本地题库答题（推荐常开）。
· 相似度阈值（0-1，默认 0.72）：整体文字相似度低于此值判为未命中。
  误答多 → 调高（如 0.8）；匹配不到 → 调低（如 0.65）。
· 子串覆盖阈值（默认 0.80）：OCR 漏字时的覆盖率门槛，处理识别不全的情况。
· 最短查询长度（默认 8）：识别文字短于此值直接跳过匹配，
  防止「下列说法正确的是」这种通用句误命中所有题。
· 包含匹配最短长度（默认 8）：防止「体育」这类短词命中「体育理论测试」。
· 题库来源：question_banks/*.json 是只读资料；界面里增删改写入 my_bank.json。

────────────────────────────────────────
【OCR 识别（直接影响识题速度）】
· 速度档位：
    - 极速 fast：按亮度只跑 1 个变体，最快（实测 ~0.7s/题）。
    - 均衡 balance（默认）：最多 2 个变体，原图够好就早停（~0.8s/题）。
    - 精准 accurate：多变体投票，最慢（~1.9s/题），深色/低对比度截图更稳。
  三档在标准题图上命中率相同，只是耗时不同。觉得慢就切「极速」。
· OCR 放大倍数：小字号题目调大（2.0~3.0）更准但更慢；极速档上限 1.5。
· 暗底自动反色：检测深色背景自动反色成白底黑字，深色主题软件必须开。
· 硬二值化：一般不勾选。会把灰度压成纯黑白，常反而丢失抗锯齿边缘。
· 方向分类：屏幕文字恒为水平，建议关闭（省一次模型推理）。竖排文字才勾选。
· 检测边长上限：限制送检图片边长，大图调小提速，过小会漏检小字。

────────────────────────────────────────
【交互】
· 全局热键：默认 F8，任意程序前台都能触发识别；被占用会写日志提示。
  点「录制按键」后直接按下想要的组合（如 Ctrl+F8）会自动填入，ESC 取消；
  也可手动填，格式如 F8、Ctrl+F8、Alt+Shift+Q。
· 连续识题间隔：连续模式下每次识别的等待秒数，按实际答题节奏调。
· 识别成功自动复制：答案自动写入剪贴板，方便直接粘贴。

────────────────────────────────────────
【提效建议】
1. 框选区域只框题目和选项，别把窗口标题、任务栏截进去（会干扰 OCR）。
2. 识题慢优先切「极速」档 + 缩小框选区域。
3. 某题总匹配不到：看识别结果里的「最接近题目」相似度，
   0.6~0.7 说明只差一点，把 OCR 识别文本加到该题 aliases；低于 0.5 是题库没这题。
4. 匹配错了：调高相似度阈值，或给易错题补充 aliases。
"""
        text = tk.Text(parent, wrap="word", bg=theme.PANEL2, fg=theme.FG,
                       relief="flat", font=(theme.FONT, 10), padx=10, pady=8)
        text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(parent, command=text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=scroll.set)
        text.insert("1.0", help_text)
        text.configure(state="disabled")

    @staticmethod
    def _mode_label(mode: str) -> str:
        return {"fast": "极速 (1 变体)", "balance": "均衡 (默认)",
                "accurate": "精准 (多变体投票)"}.get(mode, "均衡 (默认)")

    def _on_provider(self, name: str) -> None:
        url = default_base_url(name)
        if url and "base_url" in self.vars:
            self.vars["base_url"].set(url)

    def _fetch(self) -> None:
        if self._on_fetch:
            self._on_fetch(self)

    def _save(self) -> None:
        try:
            data: Dict[str, object] = {}
            for key, var in self.vars.items():
                if key.endswith("__map"):
                    continue
                if isinstance(var, tk.BooleanVar):
                    data[key] = bool(var.get())
                else:
                    data[key] = var.get()
            # 速度档位显示文本 → 内部值
            data["ocr_mode"] = self._mode_map.get(str(data["ocr_mode"]), "balance")
            for key in ("similarity_threshold", "substring_threshold", "ocr_scale",
                        "auto_interval", "temperature"):
                if key in data:
                    data[key] = float(data[key])
            for key in ("min_query_length", "min_contains_length", "request_timeout",
                        "max_tokens", "ocr_det_limit_side"):
                if key in data:
                    data[key] = int(data[key])
            if not data.get("base_url") and data.get("provider_name"):
                data["base_url"] = default_base_url(str(data["provider_name"])) or ""
            # 保留界面状态字段
            for key in ("region_mode", "region", "window_title", "all_screens", "temperature"):
                data[key] = getattr(self._cfg, key)
            new_cfg = AppConfig.from_dict(data)  # type: ignore[arg-type]
            self._on_save(new_cfg)
            self.destroy()
        except Exception as exc:
            self.hint.configure(text=f"保存失败: {exc}")
            messagebox.showerror("保存失败", str(exc), parent=self)
