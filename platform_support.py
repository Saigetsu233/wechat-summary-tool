# -*- coding: utf-8 -*-
"""跨平台细节集中在这里：数据目录、字体、打开文件、微信进程名。

Windows 与 macOS 的差异只允许出现在本模块和 wechat_macos.py 里，
其余模块一律通过这里的函数取值。
"""

import os
import subprocess
import sys
from pathlib import Path


IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"
IS_FROZEN = bool(getattr(sys, "frozen", False))

APP_NAME = "ChatroomDigest"

# 微信 4.x 的主进程名：Windows 是 Weixin.exe，macOS 上进程名为 Weixin
# （旧版 App 名为 WeChat，这里两个都认）。
WECHAT_PROCESS_NAMES = ("weixin.exe",) if IS_WINDOWS else ("weixin", "wechat")


def app_data_dir():
    """打包后配置写到系统规定的用户目录，源码运行时仍留在项目里。"""
    source_dir = os.path.dirname(os.path.abspath(__file__))
    if not IS_FROZEN:
        return source_dir
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, APP_NAME)
    if IS_MACOS:
        return os.path.expanduser(f"~/Library/Application Support/{APP_NAME}")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, APP_NAME)


# ── 字体 ─────────────────────────────────────────────────────────────────────
# 三档用途：display 是手写/招牌体，bold 是粗黑体，regular 是正文。
# 每档按平台给一串候选，取第一个真实存在的文件。
_FONT_FILES = {
    "windows": {
        "display": ("C:/Windows/Fonts/STHUPO.TTF", "C:/Windows/Fonts/FZYTK.TTF"),
        "bold": ("C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/simhei.ttf"),
        "regular": ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/Deng.ttf"),
    },
    "macos": {
        # 系统自带、无需用户安装：华文琥珀风格在 mac 上没有等价字体，
        # 退而用较有分量的黑体族，保证中文一定有字形。
        "display": (
            "/System/Library/Fonts/Supplemental/Songti.ttc",
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
        ),
        "bold": (
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/System/Library/Fonts/STHeiti Medium.ttc",
        ),
        "regular": (
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
        ),
    },
}
_FONT_FALLBACKS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def font_candidates(kind="regular"):
    """返回该用途下按优先级排序的字体文件路径候选。"""
    table = _FONT_FILES["windows"] if IS_WINDOWS else _FONT_FILES["macos"]
    paths = list(table.get(kind) or table["regular"])
    if not IS_WINDOWS:
        # macOS 上 PingFang.ttc 是字体集合，PIL 需要 index 才能取到粗体，
        # 具体索引在调用处按需处理；这里只保证文件存在。
        paths.extend(_FONT_FILES["windows"][kind])
    paths.extend(_FONT_FALLBACKS)
    return [path for path in paths if Path(path).is_file()]


def first_font(kind="regular"):
    """取该用途下第一个可用字体文件，没有就返回 None（调用方退回默认字体）。"""
    found = font_candidates(kind)
    return found[0] if found else None


# tkinter 里按字体族名设置，和上面的文件路径是两套东西。
_UI_FAMILIES = {
    "windows": {
        "display": ("华文琥珀", "站酷快乐体", "幼圆", "微软雅黑"),
        "round": ("幼圆", "微软雅黑 UI", "微软雅黑"),
        "body": ("微软雅黑", "Deng", "等线"),
    },
    "macos": {
        "display": ("翩翩体-简", "华文琥珀", "PingFang SC", "Hiragino Sans GB"),
        "round": ("PingFang SC", "Hiragino Sans GB", "STHeiti"),
        "body": ("PingFang SC", "Hiragino Sans GB", "STHeiti"),
    },
}


def ui_font_families(kind):
    """返回界面字体族候选，按平台排序。"""
    table = _UI_FAMILIES["windows"] if IS_WINDOWS else _UI_FAMILIES["macos"]
    return table.get(kind) or table["body"]


def default_ui_family():
    return "微软雅黑" if IS_WINDOWS else "PingFang SC"


def open_path(path):
    """用系统默认程序打开文件或目录。"""
    target = str(path)
    if IS_WINDOWS:
        os.startfile(target)  # noqa: S606  - Windows 专用 API
        return
    if IS_MACOS:
        subprocess.run(["open", target], check=False)
        return
    subprocess.run(["xdg-open", target], check=False)


def reveal_path(path):
    """在文件管理器里定位到该文件。"""
    target = str(path)
    if IS_WINDOWS:
        subprocess.run(["explorer", "/select,", target], check=False)
    elif IS_MACOS:
        subprocess.run(["open", "-R", target], check=False)
    else:
        subprocess.run(["xdg-open", os.path.dirname(target)], check=False)


def platform_label():
    if IS_WINDOWS:
        return "Windows"
    if IS_MACOS:
        return "macOS"
    return sys.platform
