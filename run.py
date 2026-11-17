"""图形界面入口：python run.py。"""

from __future__ import annotations

import sys


def main() -> int:
    try:
        import tkinter  # noqa: F401
    except Exception as exc:
        print("当前 Python 缺少 tkinter，无法启动图形界面。")
        print(f"详情：{exc}")
        print("解决：安装带 tkinter 的官方 CPython，或改用命令行 python cli.py")
        return 1
    from quizhunter.config import ensure_assets
    ensure_assets()
    from quizhunter.ui.app import launch
    launch()
    return 0


if __name__ == "__main__":
    sys.exit(main())
