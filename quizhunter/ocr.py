"""离线 OCR（RapidOCR / onnxruntime）封装，带三档速度模式。

性能瓶颈分析（实测）：
* 每次 recognize 会对每个「变体」跑一次完整 det+rec 推理，多变体投票 = 2~3 倍耗时；
* 方向分类 cls 对屏幕截图毫无意义（文字恒为水平），却要多跑一个模型；
* 大图放大到 4 倍像素后 det 很慢。

对应三档模式：
* fast     —— 只看亮度选 1 个最可能的变体，跑 1 次；关 cls；低放大。
* balance  —— 最多 2 个变体（原图 + 主处理图），取较好者；关 cls。（默认）
* accurate —— 全部变体（原图/灰度/反色）投票；关 cls。

同时兼容 RapidOCR 1.x 的 (result, elapse) 与 2.x 的对象式返回。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence, Tuple

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None
try:
    from PIL import Image, ImageOps
except Exception:  # pragma: no cover
    Image = None
    ImageOps = None
try:
    from rapidocr_onnxruntime import RapidOCR
except Exception:  # pragma: no cover
    try:
        from rapidocr import RapidOCR  # 2.x 包名
    except Exception:
        RapidOCR = None

MODE_CONFIG = {
    # mode: (max_variants, scale_cap)
    "fast": (1, 1.5),
    "balance": (2, 2.0),
    "accurate": (3, 4.0),
}


@dataclass
class OcrLine:
    text: str
    score: float
    y: float
    x: float
    h: float = 0.0


@dataclass
class OcrResult:
    text: str = ""
    lines: List[OcrLine] = field(default_factory=list)
    elapsed: float = 0.0
    variant: str = ""
    variants_run: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.text.strip())

    @property
    def mean_score(self) -> float:
        return sum(l.score for l in self.lines) / len(self.lines) if self.lines else 0.0


class OcrEngine:
    """线程安全的 RapidOCR 包装；首次 warmup 加载模型约 0.5~2 秒。"""

    def __init__(self, scale: float = 2.0, binarize: bool = False, auto_invert: bool = True,
                 mode: str = "balance", use_cls: bool = False, det_limit_side: int = 960):
        self.scale = max(1.0, min(4.0, float(scale)))
        self.binarize = bool(binarize)
        self.auto_invert = bool(auto_invert)
        self.mode = mode if mode in MODE_CONFIG else "balance"
        self.use_cls = bool(use_cls)
        self.det_limit_side = int(det_limit_side)
        self._engine: Any = None
        self._lock = threading.RLock()
        self._ready = False
        self._reason = "未初始化"
        self._warm_elapsed = 0.0

    # ---------------------------------------------------------------- 状态
    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def warm_elapsed(self) -> float:
        return self._warm_elapsed

    def warmup(self) -> Tuple[bool, str]:
        with self._lock:
            if self._ready:
                return True, ""
            if RapidOCR is None:
                self._reason = "未安装 rapidocr-onnxruntime"
                return False, self._reason
            if np is None:
                self._reason = "未安装 numpy"
                return False, self._reason
            started = time.time()
            try:
                # 构造期关闭方向分类：屏幕文字恒水平，省掉整条 cls 推理链
                self._engine = RapidOCR(use_angle_cls=self.use_cls)
            except Exception:
                try:
                    self._engine = RapidOCR()
                except Exception as exc:
                    self._engine = None
                    self._reason = f"OCR 引擎初始化失败: {exc}"
                    return False, self._reason
            except Exception as exc:
                self._engine = None
                self._reason = f"OCR 引擎初始化失败: {exc}"
                return False, self._reason
            self._warm_elapsed = time.time() - started
            self._ready = True
            self._reason = ""
            return True, ""

    def configure(self, *, mode: Optional[str] = None, scale: Optional[float] = None,
                  binarize: Optional[bool] = None, auto_invert: Optional[bool] = None) -> None:
        if mode in MODE_CONFIG:
            self.mode = mode
        if scale is not None:
            self.scale = max(1.0, min(4.0, float(scale)))
        if binarize is not None:
            self.binarize = bool(binarize)
        if auto_invert is not None:
            self.auto_invert = bool(auto_invert)

    # ---------------------------------------------------------------- 变体
    def _effective_scale(self) -> float:
        return min(self.scale, MODE_CONFIG[self.mode][1])

    def _variants(self, image: "Image.Image") -> List[Tuple[str, Any]]:
        """按模式生成变体列表；fast 只返回一个，accurate 返回全部。"""
        max_variants = MODE_CONFIG[self.mode][0]
        scale = self._effective_scale()
        rgb = image.convert("RGB")
        variants: List[Tuple[str, Any]] = []

        if np is None:
            return variants

        # 先算一次亮度，决定暗底是否需要反色，避免盲目跑两种版本
        is_dark = _image_mean(rgb) < 110
        arr = np.array(rgb)
        original = ("原图", arr[:, :, ::-1].copy())  # RGB -> BGR

        if self.mode == "fast":
            # 只跑一个：暗底用反色灰度，浅色用灰度放大，单张最直接的版本
            if ImageOps is None:
                variants.append(original)
            else:
                variants.append((f"{'反色' if is_dark else '灰度'}×{scale:g}",
                                 self._processed(rgb, scale, is_dark)))
            return variants[:max_variants]

        # balance / accurate：原图优先
        variants.append(original)
        if ImageOps is None:
            return variants[:max_variants]
        processed_name = f"灰度×{scale:g}{'(反色)' if is_dark and self.auto_invert else ''}"
        variants.append((processed_name, self._processed(rgb, scale, is_dark)))
        if self.mode == "accurate" and self.auto_invert and is_dark:
            variants.append(("反色原图", _to_bgr(ImageOps.invert(rgb))))
        return variants[:max_variants]

    def _processed(self, rgb: "Image.Image", scale: float, is_dark: bool) -> Any:
        gray = ImageOps.grayscale(rgb)
        size = (max(1, int(gray.width * scale)), max(1, int(gray.height * scale)))
        if size != gray.size:
            gray = gray.resize(size, Image.LANCZOS)
        gray = ImageOps.autocontrast(gray)
        if self.binarize:
            gray = gray.point(lambda p: 255 if p > 180 else 0)
        processed = ImageOps.invert(gray) if (self.auto_invert and is_dark) else gray
        return _to_bgr(processed)

    # ---------------------------------------------------------------- 推理
    def _run_engine(self, array: Any) -> Any:
        engine = self._engine
        if engine is None:
            return None
        try:
            return engine(array)
        except TypeError:
            try:
                return engine(array, use_det=True, use_cls=self.use_cls, use_rec=True)
            except TypeError:
                return engine(array)

    @staticmethod
    def _parse_output(output: Any) -> List[OcrLine]:
        if hasattr(output, "txts") and hasattr(output, "scores"):
            txts = list(getattr(output, "txts") or [])
            scores = list(getattr(output, "scores") or [])
            boxes = list(getattr(output, "boxes") or [])
            lines: List[OcrLine] = []
            for idx, text in enumerate(txts):
                score = float(scores[idx]) if idx < len(scores) else 0.0
                y = x = h = 0.0
                if idx < len(boxes) and boxes[idx] is not None:
                    y, x, h = _box_center(boxes[idx])
                lines.append(OcrLine(str(text), score, y, x, h))
            return lines
        if isinstance(output, tuple):
            output = output[0] if output else None
        lines = []
        for item in output or []:
            box = text = None
            score = 0.0
            if isinstance(item, dict):
                text = item.get("text") or item.get("txt")
                score = float(item.get("score") or item.get("confidence") or 0.0)
                box = item.get("box") or item.get("boxes")
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                box, text = item[0], item[1]
                if len(item) >= 3:
                    try:
                        score = float(item[2])
                    except (TypeError, ValueError):
                        score = 0.0
            if not text:
                continue
            y, x, h = _box_center(box)
            lines.append(OcrLine(str(text), score, y, x, h))
        return lines

    def recognize(self, image: "Image.Image") -> OcrResult:
        if Image is None:
            return OcrResult()
        if not self._ready:
            ok, _ = self.warmup()
            if not ok:
                return OcrResult()

        started = time.time()
        best_lines: List[OcrLine] = []
        best_name = ""
        best_rank = (-1, 0.0)
        run_count = 0
        with self._lock:
            for name, array in self._variants(image):
                try:
                    raw = self._run_engine(array)
                except Exception:
                    continue
                run_count += 1
                lines = self._parse_output(raw)
                if not lines:
                    continue
                score = sum(l.score for l in lines) / len(lines)
                rank = (len(lines), score)  # 行数优先，置信度次之
                if rank > best_rank:
                    best_rank, best_lines, best_name = rank, lines, name
                    # balance 模式下第一个变体（原图）已足够好就提前结束
                    if self.mode == "balance" and len(lines) >= 2 and score >= 0.75:
                        break

        elapsed = time.time() - started
        if not best_lines:
            return OcrResult(elapsed=elapsed, variants_run=run_count)
        ordered = _sort_lines(best_lines)
        text = "\n".join(l.text.strip() for l in ordered if l.text.strip())
        return OcrResult(text=text.strip(), lines=ordered, elapsed=elapsed,
                         variant=best_name, variants_run=run_count)


# ---------------------------------------------------------------- 内部工具
def _to_bgr(pil_image: "Image.Image") -> Any:
    if np is None:
        return pil_image
    arr = np.array(pil_image.convert("RGB"))
    return arr[:, :, ::-1].copy()


def _image_mean(pil_image: "Image.Image") -> float:
    try:
        hist = pil_image.convert("L").histogram()
        total = sum(hist)
        return sum(i * c for i, c in enumerate(hist)) / total if total else 255.0
    except Exception:
        return 255.0


def _box_center(box: Any) -> Tuple[float, float, float]:
    if box is None:
        return 0.0, 0.0, 0.0
    try:
        if np is not None and isinstance(box, np.ndarray):
            box = box.tolist()
        pts = [p for p in box if isinstance(p, (list, tuple)) and len(p) >= 2]
        if pts:
            ys = [float(p[1]) for p in pts]
            xs = [float(p[0]) for p in pts]
            return sum(ys) / len(ys), sum(xs) / len(xs), max(ys) - min(ys)
    except Exception:
        pass
    return 0.0, 0.0, 0.0


def _sort_lines(lines: Sequence[OcrLine]) -> List[OcrLine]:
    items = [l for l in lines if l.text.strip()]
    if not items:
        return []
    if all(l.y == 0.0 for l in items):
        return list(items)
    heights = sorted(l.h for l in items if l.h > 0)
    median_h = heights[len(heights) // 2] if heights else 12.0
    tolerance = max(6.0, median_h * 0.55)
    buckets: List[List[OcrLine]] = []
    for line in sorted(items, key=lambda it: it.y):
        if buckets and abs(line.y - buckets[-1][0].y) <= tolerance:
            buckets[-1].append(line)
        else:
            buckets.append([line])
    ordered: List[OcrLine] = []
    for bucket in buckets:
        ordered.extend(sorted(bucket, key=lambda it: it.x))
    return ordered
