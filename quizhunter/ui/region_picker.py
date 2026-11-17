"""可视化框选：把「即将被截图的那一块」原样显示出来拖框，所见即所得。

对 DPI 缩放的处理：窗口与截图坐标都是物理像素，展示时按需要缩小到屏幕可容纳
的比例，确定时再把画布坐标除回缩放系数，换算成相对窗口 / 绝对屏幕坐标。
"""

from __future__ import annotations

import tkinter as tk
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageTk

from .. import screen


class RegionPicker(tk.Toplevel):
    """模态框选窗口。

    参数：
      image      —— 待框选的截图（PIL.Image）
      origin     —— 该截图左上角在物理屏幕上的坐标 (x, y)
      window_rect—— 目标窗口物理边界 (l, t, r, b)，用于换算「跟随窗口」相对坐标
    """

    def __init__(self, master: tk.Misc, image: Image.Image, origin: Tuple[int, int],
                 window_rect: Tuple[int, int, int, int]):
        super().__init__(master)
        self.title("框选识别区域 · 拖拽选择，回车确定，Esc 取消")
        self.configure(bg="#11141b")
        self.resizable(False, False)
        self.transient(master)
        self.attributes("-topmost", True)

        self.origin = origin
        self.window_rect = window_rect
        self.result: Optional[List[int]] = None

        # 缩放适配屏幕（留 80px 边距）
        sw = self.winfo_screenwidth() - 80
        sh = self.winfo_screenheight() - 120
        self.scale = min(1.0, sw / image.width, sh / image.height)
        disp_w = max(1, int(image.width * self.scale))
        disp_h = max(1, int(image.height * self.scale))
        self.preview = image.resize((disp_w, disp_h), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(self.preview)

        self.canvas = tk.Canvas(self, width=disp_w, height=disp_h,
                                bg="#000000", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(padx=12, pady=12)
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        self.rect_id = None
        self.start: Optional[Tuple[int, int]] = None

        self.canvas.bind("<ButtonPress-1>", self._on_down)
        self.canvas.bind("<B1-Motion>", self._on_move)
        self.canvas.bind("<ButtonRelease-1>", self._on_up)
        self.bind("<Return>", lambda _e: self._confirm())
        self.bind("<Escape>", lambda _e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        hint = tk.Label(self, text="拖拽框选题目区域 · Enter 确定 · Esc 取消",
                        bg="#11141b", fg="#929daf", font=("Microsoft YaHei UI", 9))
        hint.pack(pady=(0, 10))

        self.update_idletasks()
        self.geometry(f"+{max(0, (self.winfo_screenwidth() - self.winfo_reqwidth()) // 2)}"
                      f"+{max(0, (self.winfo_screenheight() - self.winfo_reqheight()) // 2)}")
        self.grab_set()
        self.focus_force()

    def _on_down(self, event) -> None:
        self.start = (event.x, event.y)
        if self.rect_id:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#4f9dff", width=2,
            fill="#4f9dff", stipple="gray25")

    def _on_move(self, event) -> None:
        if self.start and self.rect_id:
            self.canvas.coords(self.rect_id, self.start[0], self.start[1], event.x, event.y)

    def _on_up(self, event) -> None:
        if self.start:
            self.canvas.coords(self.rect_id, self.start[0], self.start[1], event.x, event.y)

    def _confirm(self) -> None:
        if not self.start or not self.rect_id:
            self._cancel()
            return
        x1, y1, x2, y2 = self.canvas.coords(self.rect_id)
        # 画布坐标 → 源图像物理像素坐标（除以展示缩放）
        l = int(min(x1, x2) / self.scale)
        t = int(min(y1, y2) / self.scale)
        r = int(max(x1, x2) / self.scale)
        b = int(max(y1, y2) / self.scale)
        if r - l < 8 or b - t < 8:
            self._cancel()
            return
        # 源图像坐标 + origin = 屏幕绝对坐标；再换算成相对窗口坐标
        abs_box = (self.origin[0] + l, self.origin[1] + t,
                   self.origin[0] + r, self.origin[1] + b)
        wx, wy = self.window_rect[0], self.window_rect[1]
        self.result = [abs_box[0] - wx, abs_box[1] - wy, abs_box[2] - wx, abs_box[3] - wy]
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


def pick_region(master: tk.Misc, window_rect: Tuple[int, int, int, int],
                region_mode: str) -> Optional[List[int]]:
    """截取目标区域并弹出框选，返回相对窗口坐标（供 region_mode='window' 使用）。

    注意：不能构造 WindowInfo(0, ...) 再走 capture——hwnd=0 是无效句柄，
    capture 会调 window_info(0) 失败并报「无法读取窗口位置」。这里直接按矩形抓屏。
    """
    from tkinter import messagebox
    bbox = screen.clamp_bbox(window_rect)
    if bbox is None:
        messagebox.showerror("截图失败", "目标窗口不在屏幕可见范围内", parent=master)
        return None
    try:
        image = screen.grab(bbox, all_screens=True)
    except Exception as exc:
        messagebox.showerror("截图失败", f"截图失败: {exc}", parent=master)
        return None

    # origin 用实际截图左上角（裁剪后），window_rect 用原窗口左上角：
    # 这样即使窗口部分越屏，框选坐标减窗口原点后仍是正确的相对坐标。
    picker = RegionPicker(master, image, (bbox[0], bbox[1]), window_rect)
    master.wait_window(picker)
    return picker.result
