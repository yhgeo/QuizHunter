"""命令行入口：python cli.py。

只负责「问与答」，识别逻辑走 Solver。
命令：回车识题 · q 退出 · w 换窗口 · r 设区域 · s 统计 · b 题库
"""

from __future__ import annotations

import sys
import time
from typing import List, Optional

from quizhunter import APP_TITLE, __version__, screen
from quizhunter.config import PROVIDER_PRESETS, AppConfig, default_base_url
from quizhunter.llm import LLMClient
from quizhunter.log import LogBus
from quizhunter.solver import SolveResult, Solver

LINE = "=" * 68


def _ask(prompt: str, default: str = "") -> str:
    try:
        raw = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return raw or default


def _ask_yes(prompt: str, default: bool = True) -> bool:
    answer = _ask(f"{prompt} ({'Y/n' if default else 'y/N'}): ").lower()
    return default if not answer else answer in ("y", "yes", "1", "true")


def _choose(prompt: str, options: List[str]) -> Optional[int]:
    for i, option in enumerate(options, 1):
        print(f"  {i}. {option}")
    raw = _ask(prompt)
    return (int(raw) - 1) if raw.isdigit() and 1 <= int(raw) <= len(options) else None


def configure_model(cfg: AppConfig, log: LogBus) -> bool:
    print(f"\n{LINE}\n模型配置（OpenAI 兼容接口，可留空只用题库）\n{LINE}")
    names = [p[0] for p in PROVIDER_PRESETS]
    index = _choose("选择提供方序号: ", names)
    if index is None:
        return False
    provider, url = PROVIDER_PRESETS[index]
    cfg.provider_name = provider
    if url:
        cfg.base_url = (_ask(f"Base URL（回车用 {url}）: ") or url).rstrip("/")
    else:
        cfg.base_url = _ask("输入 Base URL: ").rstrip("/")
    cfg.api_key = _ask("API Key: ")
    if not cfg.api_key:
        return False
    client = LLMClient(cfg, log)
    print("\n拉取模型列表…")
    models, error = client.fetch_models()
    if error:
        print(f"! {error}")
        cfg.model = _ask("手动输入模型 ID: ")
    else:
        print(f"共 {len(models)} 个模型")
        cfg.model = _ask("输入模型 ID（回车用第一个）: ") or (models[0] if models else "")
    client.close()
    if not cfg.model or not cfg.base_url:
        return False
    cfg.model_enabled = True
    cfg.background_prompt = _ask("答题背景提示词（可留空）: ", cfg.background_prompt)
    cfg.save()
    print(f"✓ 配置已保存，模型：{cfg.model}")
    return True


def configure_window(cfg: AppConfig) -> None:
    if not _ask_yes("设置固定窗口?", False):
        return
    windows = screen.list_windows()
    for i, info in enumerate(windows[:40], 1):
        print(f"  {i:>2}. {info.label()}")
    raw = _ask("输入序号或标题关键词: ")
    target = None
    if raw.isdigit() and 1 <= int(raw) <= min(len(windows), 40):
        target = windows[int(raw) - 1]
    else:
        target = next((w for w in windows if raw.lower() in w.title.lower()), None)
    if target is None:
        print("✗ 未找到窗口")
        return
    cfg.window_title = target.title
    print(f"✓ 已选择：{target.title} ({target.width}×{target.height})")
    if not _ask_yes("设置相对窗口的识别区域?", False):
        cfg.region, cfg.region_mode = [], "none"
        cfg.save()
        return
    raw = _ask(f"输入 左,上,右,下（窗口 {target.width}×{target.height}）: ")
    try:
        coords = [int(p.strip()) for p in raw.split(",")]
    except ValueError:
        print("✗ 格式错误")
        return
    if len(coords) != 4 or coords[2] <= coords[0] or coords[3] <= coords[1]:
        print("✗ 区域无效")
        return
    cfg.region, cfg.region_mode = coords, "window"
    cfg.save()
    print("✓ 区域已保存")


def print_result(result: SolveResult) -> None:
    print(f"\n{'-' * 60}")
    if result.ok:
        print(f"✓ {result.source}答案：{result.answer}")
        print(f"  依据：{result.detail}")
    else:
        print(f"✗ 无结果：{result.error or result.detail}")
    if result.question:
        preview = result.question if len(result.question) <= 240 else result.question[:240] + "…"
        print(f"识别题目：\n{preview}")
    if result.candidates and not result.ok:
        print("最接近的题库题目：")
        for i, c in enumerate(result.candidates, 1):
            print(f"  {i}. [{c.score:.2f}] {c.answer} · {c.question[:48]}")
    if result.timings:
        parts = [f"{k} {v:.2f}s" for k, v in result.timings.items()]
        print("耗时 " + " · ".join(parts) + f" · 合计 {result.total:.2f}s")
    print(f"{'-' * 60}\n")


def main(argv: Optional[List[str]] = None) -> int:
    screen.enable_dpi_awareness()
    from quizhunter.config import ensure_assets
    ensure_assets()
    log = LogBus(echo=True)
    cfg = AppConfig.load()
    solver = Solver(cfg, log)
    print(f"\n{LINE}\n  {APP_TITLE} v{__version__} · 截图识题 + 离线 OCR + 本地题库 + AI 兜底\n{LINE}")
    print("回车识题 · q 退出 · w 换窗口 · r 区域 · s 统计\n")

    if _ask_yes("启用 AI 答题?", cfg.model_ready()):
        if not (cfg.model_ready() and _ask_yes(f"用已保存配置（{cfg.model}）?", True)):
            if not configure_model(cfg, log):
                print("! 模型未配置，只用题库")
                cfg.model_enabled = False
    else:
        cfg.model_enabled = False
    cfg.bank_enabled = _ask_yes("启用题库优先?", cfg.bank_enabled)
    solver.apply_config(cfg)

    if cfg.bank_enabled:
        report = solver.reload_bank()
        print(f"题库：{report.summary()}")
        ok, reason = solver.warm_ocr()
        print(f"OCR：{'就绪' if ok else '不可用 - ' + reason}")

    if not cfg.bank_enabled and not cfg.model_ready():
        print("\n✗ 题库与模型都不可用，退出。")
        return 1

    configure_window(cfg)
    solver.apply_config(cfg)
    print(f"\n{LINE}\n准备就绪，看到题目按回车。\n{LINE}")

    while True:
        cmd = _ask("回车识题 (q/w/r/s): ").lower()
        if cmd in ("q", "quit", "exit"):
            break
        if cmd == "w":
            cfg.window_title, cfg.region, cfg.region_mode = "", [], "none"
            configure_window(cfg)
            solver.apply_config(cfg)
            continue
        if cmd == "r":
            configure_window(cfg)
            solver.apply_config(cfg)
            continue
        if cmd == "s":
            s = solver.stats.as_dict()
            print(f"\n共 {s['total']} 次 · 题库 {s['bank']} · AI {s['ai']} · "
                  f"无匹配 {s['miss']} · 命中率 {s['hit_rate']:.0f}% · 均速 {s['speed']:.2f}s/题\n")
            continue
        print_result(solver.capture_and_solve())

    s = solver.stats.as_dict()
    print(f"\n{LINE}\n本次：{s['total']} 次 · 题库 {s['bank']} · AI {s['ai']} · 无匹配 {s['miss']}\n再见。\n{LINE}")
    solver.llm.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
