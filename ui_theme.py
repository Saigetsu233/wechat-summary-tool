# -*- coding: utf-8 -*-
"""桌面界面的手绘主题：配色、字体、圆角粗描边卡片和 PIL 现画的小插画。

图标全部在运行时用 PIL 画出来，不依赖任何素材文件，PyInstaller 打包后也能用。
"""

import tkinter as tk
from tkinter import font as tkfont

from PIL import Image, ImageDraw, ImageTk


# 与日报海报同一套配色，界面和产出物看起来是一家人。
PAPER = "#FFFDF6"
PAPER_DEEP = "#F6F1E4"
CARD = "#FFFFFF"
NAVY = "#1F2B54"
NAVY_DARK = "#16203F"
INK = "#17283D"
MUTED = "#5F6F81"
LINE = "#DFE5EE"

BLUE = "#1596D2"
PINK = "#EF4D78"
GREEN = "#12A36E"
ORANGE = "#F59B23"
PURPLE = "#7046C1"
CYAN = "#08AFC7"
YELLOW = "#F6C531"

CAT_BODY = "#2C2C38"
CAT_LINE = "#14141C"

_ICON_CACHE = {}


def _available(root):
    try:
        return {name.lower() for name in tkfont.families(root)}
    except tk.TclError:
        return set()


def pick_font(root, *candidates):
    """挑第一个装了的字体族，都没有就退回微软雅黑。"""
    families = _available(root)
    for name in candidates:
        if name.lower() in families:
            return name
    return "微软雅黑"


def fonts(root):
    """界面用到的三档字体：手写标题、圆润小标题、正文。"""
    return {
        "display": pick_font(root, "华文琥珀", "站酷快乐体", "幼圆", "微软雅黑"),
        "round": pick_font(root, "幼圆", "微软雅黑 UI", "微软雅黑"),
        "body": pick_font(root, "微软雅黑", "Deng", "等线"),
    }


def round_rect(canvas, x0, y0, x1, y1, radius, **kwargs):
    """在 Canvas 上画圆角矩形；tkinter 没有原生圆角，用平滑多边形凑。"""
    radius = max(1, min(radius, (x1 - x0) // 2, (y1 - y0) // 2))
    points = [
        x0 + radius, y0, x1 - radius, y0,
        x1, y0, x1, y0 + radius,
        x1, y1 - radius, x1, y1,
        x1 - radius, y1, x0 + radius, y1,
        x0, y1, x0, y1 - radius,
        x0, y0 + radius, x0, y0,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


def _rounded_mask_rect(draw, box, radius, fill, outline=None, width=0):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _cat_face(draw, box, line_width=3):
    """在给定方框里画一只 Q 版黑猫的脸。"""
    left, top, right, bottom = box
    w, h = right - left, bottom - top
    ear = max(6, int(w * 0.26))
    # 耳朵
    for side in (0, 1):
        base_x = left + (w * 0.12 if side == 0 else w * 0.88)
        draw.polygon(
            [
                (base_x - ear * 0.55, top + h * 0.34),
                (base_x + (ear * 0.1 if side == 0 else -ear * 0.1), top - h * 0.02),
                (base_x + ear * 0.55, top + h * 0.34),
            ],
            fill=CAT_BODY, outline=CAT_LINE, width=max(1, line_width - 1),
        )
    # 头
    draw.ellipse(
        (left, top + h * 0.12, right, bottom),
        fill=CAT_BODY, outline=CAT_LINE, width=line_width,
    )
    eye_y = top + h * 0.52
    eye_r = max(3, int(w * 0.13))
    for cx in (left + w * 0.33, left + w * 0.67):
        draw.ellipse(
            (cx - eye_r, eye_y - eye_r * 1.15, cx + eye_r, eye_y + eye_r * 1.15),
            fill=YELLOW, outline=CAT_LINE, width=max(1, line_width - 2),
        )
        pupil = max(1, int(eye_r * 0.42))
        draw.ellipse(
            (cx - pupil, eye_y - pupil * 1.3, cx + pupil, eye_y + pupil * 1.3),
            fill=CAT_LINE,
        )
        spark = max(1, int(eye_r * 0.22))
        draw.ellipse(
            (cx - eye_r * 0.55, eye_y - eye_r * 0.8,
             cx - eye_r * 0.55 + spark * 2, eye_y - eye_r * 0.8 + spark * 2),
            fill="#FFFFFF",
        )
    # 鼻子和嘴
    nose_y = top + h * 0.72
    draw.polygon(
        [
            (left + w * 0.5 - w * 0.045, nose_y),
            (left + w * 0.5 + w * 0.045, nose_y),
            (left + w * 0.5, nose_y + h * 0.05),
        ],
        fill="#F2A0B4",
    )
    draw.arc(
        (left + w * 0.38, nose_y, left + w * 0.5, nose_y + h * 0.14),
        start=0, end=140, fill=CAT_LINE, width=max(1, line_width - 2),
    )
    draw.arc(
        (left + w * 0.5, nose_y, left + w * 0.62, nose_y + h * 0.14),
        start=40, end=180, fill=CAT_LINE, width=max(1, line_width - 2),
    )
    # 腮红
    for cx in (left + w * 0.18, left + w * 0.82):
        draw.ellipse(
            (cx - w * 0.07, nose_y - h * 0.02, cx + w * 0.07, nose_y + h * 0.08),
            fill="#EE7C96",
        )


def cat_head(size=64, line_width=3):
    """一只圆头小黑猫，用在页头和空状态里。"""
    key = ("cat", size, line_width)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    scale = 4
    canvas = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    pad = size * scale * 0.08
    _cat_face(
        draw,
        (pad, pad * 1.6, size * scale - pad, size * scale - pad * 0.6),
        line_width=line_width * scale,
    )
    image = canvas.resize((size, size), Image.Resampling.LANCZOS)
    _ICON_CACHE[key] = image
    return image


def cat_with_laptop(width=112):
    """页头吉祥物：小黑猫趴在笔记本前面摸鱼，右边配一杯热饮。"""
    key = ("cat_laptop", width)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    scale = 4
    height = int(width * 0.72)
    W, H = width * scale, height * scale
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    line = 3 * scale

    # 桌面
    draw.rounded_rectangle(
        (W * 0.04, H * 0.78, W * 0.96, H * 0.95),
        radius=int(H * 0.06), fill="#E9D5B4", outline=CAT_LINE, width=line,
    )
    # 猫身
    draw.ellipse(
        (W * 0.22, H * 0.36, W * 0.72, H * 0.86),
        fill=CAT_BODY, outline=CAT_LINE, width=line,
    )
    # 尾巴
    draw.arc(
        (W * 0.60, H * 0.48, W * 0.94, H * 0.88),
        start=250, end=25, fill=CAT_LINE, width=line + scale,
    )
    # 猫脸
    _cat_face(draw, (W * 0.24, H * 0.06, W * 0.68, H * 0.56), line_width=3 * scale)
    # 笔记本
    draw.polygon(
        [
            (W * 0.10, H * 0.80), (W * 0.30, H * 0.80),
            (W * 0.36, H * 0.46), (W * 0.14, H * 0.46),
        ],
        fill="#C9D4E4", outline=CAT_LINE, width=line,
    )
    draw.polygon(
        [
            (W * 0.06, H * 0.86), (W * 0.42, H * 0.86),
            (W * 0.38, H * 0.79), (W * 0.10, H * 0.79),
        ],
        fill="#EDF2F8", outline=CAT_LINE, width=line,
    )
    # 马克杯
    draw.rounded_rectangle(
        (W * 0.76, H * 0.60, W * 0.92, H * 0.82),
        radius=int(H * 0.05), fill="#FFFFFF", outline=CAT_LINE, width=line,
    )
    draw.arc(
        (W * 0.90, H * 0.65, W * 0.99, H * 0.77),
        start=270, end=90, fill=CAT_LINE, width=line,
    )
    image = canvas.resize((width, height), Image.Resampling.LANCZOS)
    _ICON_CACHE[key] = image
    return image


def chat_bubble(size=44, color="#FFFFFF", dot=NAVY):
    """页头的白色对话气泡图标。"""
    key = ("bubble", size, color, dot)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    scale = 4
    S = size * scale
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (S * 0.06, S * 0.14, S * 0.94, S * 0.72),
        radius=int(S * 0.22), fill=color,
    )
    draw.polygon(
        [(S * 0.24, S * 0.66), (S * 0.20, S * 0.94), (S * 0.50, S * 0.70)],
        fill=color,
    )
    for cx in (0.30, 0.50, 0.70):
        draw.ellipse(
            (S * (cx - 0.065), S * 0.37, S * (cx + 0.065), S * 0.50), fill=dot
        )
    image = canvas.resize((size, size), Image.Resampling.LANCZOS)
    _ICON_CACHE[key] = image
    return image


def step_badge(text, accent, size=38):
    """步骤序号贴纸：粗描边圆角方块，手绘贴纸感。"""
    key = ("badge", text, accent, size)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    scale = 4
    S = size * scale
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (S * 0.10, S * 0.10, S * 0.90, S * 0.90),
        radius=int(S * 0.26), fill=accent, outline=NAVY_DARK, width=3 * scale,
    )
    from PIL import ImageFont

    font = None
    for candidate in ("C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/simhei.ttf"):
        try:
            font = ImageFont.truetype(candidate, size=int(S * 0.46))
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    draw.text((S * 0.5, S * 0.52), str(text), font=font, fill="#FFFFFF", anchor="mm")
    image = canvas.resize((size, size), Image.Resampling.LANCZOS).rotate(
        -4, resample=Image.Resampling.BICUBIC, expand=False
    )
    _ICON_CACHE[key] = image
    return image


def sparkle(size=18, color=YELLOW):
    """四角小星星，用来点缀标题。"""
    key = ("sparkle", size, color)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    scale = 4
    S = size * scale
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    c = S / 2
    draw.polygon(
        [
            (c, S * 0.02), (c + S * 0.13, c - S * 0.13), (S * 0.98, c),
            (c + S * 0.13, c + S * 0.13), (c, S * 0.98),
            (c - S * 0.13, c + S * 0.13), (S * 0.02, c),
            (c - S * 0.13, c - S * 0.13),
        ],
        fill=color,
    )
    image = canvas.resize((size, size), Image.Resampling.LANCZOS)
    _ICON_CACHE[key] = image
    return image


def to_photo(image):
    """PIL 图转 tkinter 图，并挂在返回值上防止被 GC。"""
    return ImageTk.PhotoImage(image)


class HandCard(tk.Canvas):
    """手绘感卡片：Canvas 画圆角粗描边，内容照旧 pack 进 body。

    用法：
        holder = HandCard(parent, accent=BLUE)
        holder.pack(fill="x")
        ttk.Label(holder.body, ...).pack()
    """

    def __init__(self, parent, accent=BLUE, bg=CARD, outer=PAPER,
                 radius=18, pad=15, border=2, stretch=False, **kwargs):
        super().__init__(parent, bg=outer, highlightthickness=0, bd=0, **kwargs)
        self._accent = accent
        self._bg = bg
        self._radius = radius
        self._pad = pad
        self._border = border
        self._stretch = stretch
        self.body = tk.Frame(self, bg=bg)
        self._window = self.create_window(pad, pad, anchor="nw", window=self.body)
        self.bind("<Configure>", self._redraw)
        if not stretch:
            # 自适应高度的卡片要跟着内容长高；铺满型卡片反过来由外框决定高度。
            self.body.bind("<Configure>", self._redraw)

    def _redraw(self, _event=None):
        width = self.winfo_width()
        if width <= 1:
            return
        inner_width = max(1, width - self._pad * 2)
        if self.itemcget(self._window, "width") != str(inner_width):
            self.itemconfigure(self._window, width=inner_width)
        if self._stretch:
            height = self.winfo_height()
            inner_height = max(1, height - self._pad * 2)
            if self.itemcget(self._window, "height") != str(inner_height):
                self.itemconfigure(self._window, height=inner_height)
        else:
            height = self.body.winfo_reqheight() + self._pad * 2
            if int(self.cget("height")) != height:
                self.configure(height=height)
        self.delete("card_bg")
        # 影子先画，再画卡面，手绘贴纸的浮起感靠这一层
        round_rect(
            self, 4, 5, width - 2, height - 2, self._radius,
            fill=PAPER_DEEP, outline="", tags="card_bg",
        )
        round_rect(
            self, 2, 2, width - 4, height - 5, self._radius,
            fill=self._bg, outline=self._accent, width=self._border, tags="card_bg",
        )
        self.tag_lower("card_bg")
