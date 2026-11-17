"""配置模型、路径解析与原子读写。

要点：
1. 路径统一由 app_home() 推导，兼容源码运行与 PyInstaller 冻结运行；
2. from_dict 容忍缺字段 / 未知字段 / 类型错误，旧配置可平滑升级；
3. 写入用临时文件 + os.replace 原子替换，中途崩溃不留半截 JSON。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional

APP_NAME = "猎答"
CONFIG_FILENAME = "config.json"
USER_BANK_FILENAME = "my_bank.json"

PROVIDER_PRESETS: List[tuple] = [
    ("OpenAI", "https://api.openai.com/v1"),
    ("DeepSeek", "https://api.deepseek.com/v1"),
    ("阿里云百炼 (兼容模式)", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    ("智谱 GLM", "https://open.bigmodel.cn/api/paas/v4"),
    ("月之暗面 Moonshot", "https://api.moonshot.cn/v1"),
    ("SiliconFlow", "https://api.siliconflow.cn/v1"),
    ("OpenRouter", "https://openrouter.ai/api/v1"),
    ("自定义 (OpenAI 兼容)", None),
]
PROVIDER_NAMES = [name for name, _ in PROVIDER_PRESETS]


def app_home() -> Path:
    override = os.environ.get("QUIZHUNTER_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def default_base_url(provider_name: str) -> Optional[str]:
    for name, url in PROVIDER_PRESETS:
        if name == provider_name:
            return url
    return None


@dataclass
class AppConfig:
    # 模型（可选）
    provider_name: str = ""
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    model_enabled: bool = False
    background_prompt: str = ""

    # 题库与匹配
    bank_enabled: bool = True
    similarity_threshold: float = 0.72
    substring_threshold: float = 0.80
    min_query_length: int = 8
    min_contains_length: int = 8

    # 截图
    window_title: str = ""
    region_mode: str = "none"          # none | window | screen
    region: List[int] = field(default_factory=list)
    all_screens: bool = True

    # OCR
    ocr_scale: float = 2.0
    ocr_binarize: bool = False
    ocr_auto_invert: bool = True
    ocr_mode: str = "balance"          # fast | balance | accurate
    ocr_use_cls: bool = False          # 关闭方向分类：屏幕文字恒为水平，省一次推理
    ocr_det_limit_side: int = 960      # 检测边长上限，限制大图推理耗时

    # 请求
    request_timeout: int = 30
    max_tokens: int = 256
    temperature: float = 0.1

    # 交互
    hotkey: str = "F8"
    auto_interval: float = 2.0
    auto_copy: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def save(self, path: Optional[Path] = None) -> Path:
        target = Path(path) if path else config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".cfg-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        return target

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "AppConfig":
        target = Path(path) if path else config_path()
        if not target.exists():
            return cls()
        try:
            with target.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception:
            return cls()
        return cls.from_dict(raw) if isinstance(raw, dict) else cls()

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "AppConfig":
        cfg = cls()
        known = {f.name for f in fields(cls)}
        for key, value in raw.items():
            if key not in known:
                continue
            current = getattr(cfg, key)
            try:
                if isinstance(current, bool):
                    setattr(cfg, key, _as_bool(value))
                elif isinstance(current, int) and not isinstance(current, bool):
                    setattr(cfg, key, int(value))
                elif isinstance(current, float):
                    setattr(cfg, key, float(value))
                elif isinstance(current, list):
                    setattr(cfg, key, list(value) if isinstance(value, (list, tuple)) else [])
                else:
                    setattr(cfg, key, "" if value is None else str(value))
            except (TypeError, ValueError):
                continue
        if not cfg.base_url and cfg.provider_name:
            cfg.base_url = default_base_url(cfg.provider_name) or ""
        cfg.base_url = cfg.base_url.strip().rstrip("/")
        if cfg.region and len(cfg.region) != 4:
            cfg.region = []
        if cfg.region_mode not in ("none", "window", "screen"):
            cfg.region_mode = "none"
        if cfg.ocr_mode not in ("fast", "balance", "accurate"):
            cfg.ocr_mode = "balance"
        return cfg

    def model_ready(self) -> bool:
        return bool(self.model_enabled and self.api_key and self.base_url and self.model)

    def masked_key(self) -> str:
        key = self.api_key or ""
        if len(key) <= 8:
            return "*" * len(key)
        return f"{key[:4]}{'*' * 8}{key[-4:]}"

    def copy(self) -> "AppConfig":
        return AppConfig.from_dict(self.to_dict())


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def config_path() -> Path:
    return app_home() / CONFIG_FILENAME


def bank_dir_path() -> Path:
    return app_home() / "question_banks"


def user_bank_path() -> Path:
    return app_home() / USER_BANK_FILENAME


def log_dir_path() -> Path:
    return app_home() / "logs"


def resource_dir() -> Path:
    """只读资源目录：冻结态在 PyInstaller 解压目录，源码态在项目根。"""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return Path(base)
    return Path(__file__).resolve().parent.parent


def ensure_assets() -> None:
    """冻结态首次运行：把示例题库/配置从打包资源复制到 exe 同级目录。

    这样用户解压/安装后 exe 旁边就有 question_banks/ 与 config.example.json，
    且后续增删改都写在 exe 目录，不碰临时的 _MEIPASS。
    """
    home = app_home()
    res = resource_dir()
    if not getattr(sys, "frozen", False):
        return
    try:
        bank_home = bank_dir_path()
        bank_res = res / "question_banks"
        if bank_res.is_dir() and not bank_home.exists():
            shutil_copytree(bank_res, bank_home)
        example = res / "config.example.json"
        if example.exists() and not (home / "config.example.json").exists():
            shutil_copy2(example, home / "config.example.json")
    except Exception:
        pass


def shutil_copytree(src: Path, dst: Path) -> None:
    import shutil
    shutil.copytree(src, dst)


def shutil_copy2(src: Path, dst: Path) -> None:
    import shutil
    shutil.copy2(src, dst)
