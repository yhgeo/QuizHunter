"""一键打包成免依赖 exe（PyInstaller）。

用法：
    python build_exe.py            # onedir（推荐：启动快，dist/QuizHunter/ 整个发给别人）
    python build_exe.py --onefile  # 单文件（dist/QuizHunter.exe，启动稍慢，拷贝方便）

产物：
    dist/QuizHunter/QuizHunter.exe   （onedir）
    或 dist/QuizHunter.exe           （onefile）

打包内容：Python 解释器 + 全部依赖 + RapidOCR 的 onnx 模型 + config.example.json
+ question_banks 示例题库。目标电脑无需装 Python / 任何依赖，双击即用。

首次运行会在 exe 同级目录生成 config.json / my_bank.json / question_banks / logs。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
NAME = "QuizHunter"


def main() -> int:
    onefile = "--onefile" in sys.argv
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("缺少 PyInstaller，请先: pip install pyinstaller")
        return 1

    # 清理旧产物
    for p in (DIST / NAME, DIST / f"{NAME}.exe", ROOT / f"{NAME}.spec"):
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink()
    if BUILD.exists():
        shutil.rmtree(BUILD, ignore_errors=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", NAME,
        "--windowed",                      # 不弹控制台黑窗
        "--collect-all", "rapidocr_onnxruntime",  # onnx 模型 + 配置全部带上
        "--collect-all", "onnxruntime",
        "--hidden-import", "tkinter",
        "--hidden-import", "PIL",
        "--add-data", f"{ROOT / 'config.example.json'}{';' if sys.platform == 'win32' else ':'}.",
        "--add-data", f"{ROOT / 'question_banks'}{';' if sys.platform == 'win32' else ':'}question_banks",
    ]
    if onefile:
        cmd.append("--onefile")
    cmd.append(str(ROOT / "run.py"))

    print("打包命令：", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print("打包失败")
        return result.returncode

    print("\n打包完成：")
    if onefile:
        exe = DIST / f"{NAME}.exe"
        print(f"  {exe}")
        print(f"  大小 {exe.stat().st_size / 1024 / 1024:.1f} MB")
    else:
        exe = DIST / NAME / f"{NAME}.exe"
        print(f"  {exe}")
        total = sum(f.stat().st_size for f in (DIST / NAME).rglob("*") if f.is_file())
        print(f"  目录总大小 {total / 1024 / 1024:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
