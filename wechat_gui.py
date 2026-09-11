# -*- coding: utf-8 -*-
"""
微信群聊 AI 总结工具 - 图形界面版
依赖：pip install tkcalendar pycryptodome requests psutil
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import threading
import datetime
import os
import re
import subprocess
import sys
import sqlite3
import traceback
import copy

# 确保能找到 wechat_summary.py（同目录）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from tkcalendar import DateEntry
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "tkcalendar", "-q"], check=False)
    from tkcalendar import DateEntry

try:
    from PIL import Image, ImageTk
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "pillow", "-q"], check=False)
    from PIL import Image, ImageTk

from wechat_summary import (
    find_wechat_data_dir,
    find_db_storage,
    extract_keys_from_memory,
    decrypt_db,
    select_decrypt_temp_dir,
    list_chatrooms,
    list_private_chats,
    provider_model_options,
    build_scope_labels,
    load_contact_name_map,
    load_group_member_name_map,
    load_contact_gender_map,
    get_sender_usernames_by_range,
    get_messages_by_range,
    ai_summarize,
    ai_newspaper_digest,
    load_config,
    save_config,
    DEFAULT_PROMPT_TEMPLATE,
    DEFAULT_PROVIDER,
    PROVIDERS,
    provider_default_model,
    provider_label,
)
import ui_theme as theme
from platform_support import open_path as theme_open, IS_MACOS as theme_is_macos_flag


def theme_is_macos():
    return theme_is_macos_flag
from digest_templates import TEMPLATES, get_template
from newspaper_renderer import render_newspaper, save_poster_image
from topic_image_generator import (
    GEMINI_IMAGE_MODEL,
    GEMINI_POSTER_MODEL,
    build_digest_illustration_requests,
    generate_full_poster,
    generate_topic_images,
)


# 插画模式：整图海报最像手绘，本地排版保证文字准确。
ILLUSTRATION_MODES = (
    ("poster", "整图 AI 海报：模型直接画整页（最像手绘，1 次调用）"),
    ("detailed", "本地排版 + 逐格精绘插画（最多 9 次调用）"),
    ("sheet", "本地排版 + 单次联系表插画（最省钱）"),
)
ILLUSTRATION_MODE_LABELS = {key: label for key, label in ILLUSTRATION_MODES}


def _illustration_mode_from_label(label):
    for key, text in ILLUSTRATION_MODES:
        if text == label:
            return key
    return "poster"


APP_BG = theme.PAPER
CARD_BG = theme.CARD
INK = theme.INK
MUTED = theme.MUTED
PRIMARY = theme.BLUE
PRIMARY_DARK = "#0F7AAD"
CYAN = theme.CYAN
NAVY = theme.NAVY
NAVY_DARK = theme.NAVY_DARK
BORDER = theme.LINE
PINK = theme.PINK
GREEN = theme.GREEN
ORANGE = theme.ORANGE
PURPLE = theme.PURPLE
YELLOW = theme.YELLOW

GENDER_CHOICES = (
    ("未指定（中性人物）", "unspecified"),
    ("女性", "female"),
    ("男性", "male"),
)
GENDER_LABELS = {label: value for label, value in GENDER_CHOICES}
GENDER_VALUES = {value: label for label, value in GENDER_CHOICES}


def resource_path(filename):
    """返回源码运行或 PyInstaller 打包后的资源路径。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, filename)


def _safe_filename_part(value, fallback="群聊"):
    """把群名变成 Windows 文件名中的安全片段。"""
    text = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", str(value or "")).strip()
    text = re.sub(r"\s+", " ", text).strip(" .")
    return (text or fallback)[:60]


class WeChatSummaryApp:
    def __init__(self, root):
        self.root = root
        self.root.title("群聊日报 · AI Digest")
        self.root.resizable(True, True)
        self.root.minsize(1040, 820)
        self.root.configure(bg=APP_BG)

        # 后端状态
        self.key_map = {}
        self.conn_msg = []
        self.conn_contact = None
        self.tmp_msg_paths = []
        self.tmp_contact_path = None
        self.chatrooms = []   # 当前显示的会话列表 [(id, count, display), ...]
        self._all_chats = {"group": [], "private": []}  # 两类会话缓存
        self.chat_kind = "group"  # "group" 群聊日报 / "private" 聊天日报
        self.contact_name_map = {}
        self.group_member_name_map = {}
        self.contact_gender_map = {}
        self._last_summary_key = None
        self._last_summary_text = ""
        self._cancel_event = threading.Event()
        self.config = load_config()
        self._last_digest = None
        saved_template = self.config.get('digest_template', 'handdrawn')
        self.template_var = tk.StringVar(value=get_template(saved_template)['name'])
        self.preview_before_image_var = tk.BooleanVar(value=bool(self.config.get('preview_before_image', True)))
        stored_profiles = self.config.get("member_profiles")
        self.member_profiles = stored_profiles if isinstance(stored_profiles, dict) else {}
        self._initialized = False
        self.ai_topic_images_var = tk.BooleanVar(
            value=bool(self.config.get("ai_topic_images", True))
        )
        saved_mode = str(self.config.get("illustration_mode") or "").strip()
        if saved_mode not in ILLUSTRATION_MODE_LABELS:
            # 兼容旧配置里的布尔开关。
            saved_mode = "detailed" if self.config.get("detailed_illustrations") else "poster"
        self.illustration_mode_var = tk.StringVar(
            value=ILLUSTRATION_MODE_LABELS[saved_mode]
        )
        default_image_model = (
            GEMINI_POSTER_MODEL if saved_mode == "poster" else GEMINI_IMAGE_MODEL
        )
        self.image_model_var = tk.StringVar(
            value=str(self.config.get("image_model") or default_image_model)
        )

        configured_provider = str(self.config.get("provider") or DEFAULT_PROVIDER)
        if configured_provider not in PROVIDERS:
            configured_provider = DEFAULT_PROVIDER
        # 首次升级到 Gemini 版时，把旧 NVIDIA 默认项迁移到付费 Gemini。
        # DeepSeek 用户保持原选择，仍可主动切换。
        if (
            int(self.config.get("provider_migration_version") or 0) < 2
            and configured_provider == "nvidia"
        ):
            configured_provider = "gemini"
        self.config["provider_migration_version"] = 2
        stored_keys = self.config.get("api_keys")
        self.provider_keys = dict(stored_keys) if isinstance(stored_keys, dict) else {}
        # 自动迁移旧版单个 DeepSeek Key 配置。
        legacy_key = str(self.config.get("api_key") or "").strip()
        if legacy_key and not self.provider_keys.get("deepseek"):
            self.provider_keys["deepseek"] = legacy_key
        stored_models = self.config.get("models")
        self.provider_models = dict(stored_models) if isinstance(stored_models, dict) else {}
        # v1.2.1 曾把超大 Pro 模型作为默认值，长群聊在免费端点上经常超时。
        # 只迁移这个历史默认值；用户手动填写的其他模型保持不变。
        if self.provider_models.get("nvidia") == "deepseek-ai/deepseek-v4-pro-0813":
            self.provider_models["nvidia"] = provider_default_model("nvidia")
        self.current_provider = configured_provider
        self.provider_var = tk.StringVar(value=provider_label(configured_provider))
        self.api_key_var = tk.StringVar(
            value=str(self.provider_keys.get(configured_provider, ""))
        )
        self.model_var = tk.StringVar(
            value=str(
                self.provider_models.get(configured_provider)
                or provider_default_model(configured_provider)
            )
        )
        saved_prompt = self.config.get("prompt_template") or ""
        # 旧版示例提示词要求 Markdown 标题/表格，微信群中无法正常渲染。
        # 仅迁移这一类旧模板；用户之后保存的新自定义模板仍会原样保留。
        legacy_markdown_prompt = (
            "# YYYY-MM-DD 群聊总结" in saved_prompt
            or "## 趣味成就" in saved_prompt
            or "**核心话题**" in saved_prompt
        )
        self._prompt_template = (
            DEFAULT_PROMPT_TEMPLATE
            if not saved_prompt or legacy_markdown_prompt
            else saved_prompt
        )
        self._manual_user_dir = None   # 用户手动指定的微信数据目录

        self._build_ui()
        # 旧配置可能把整图模式和 flash 图片模型配在一起，整图靠 flash 写中文必然糊。
        self._on_illustration_mode_change()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ─────────────────────────────────────────────────────────────────────────
    # UI 构建
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._configure_styles()

        # 顶部品牌区：干净的产品化横幅，右侧用日报图标收尾
        hero_wrap = tk.Frame(self.root, bg=APP_BG)
        hero_wrap.pack(fill="x", padx=20, pady=(16, 6))
        hero = tk.Canvas(hero_wrap, bg=APP_BG, highlightthickness=0, bd=0, height=84)
        hero.pack(fill="x")

        def _paint_hero(_event=None):
            width = hero.winfo_width()
            if width <= 1:
                return
            hero.delete("hero")
            theme.round_rect(hero, 2, 2, width - 4, 87, 18,
                             fill=APP_BG, outline=APP_BG, width=1, tags="hero")
            hero.tag_lower("hero")

        hero.bind("<Configure>", _paint_hero)

        self._icon_bubble = theme.to_photo(theme.chat_bubble(38, PRIMARY, '#FFFFFF'))
        tk.Label(hero, image=self._icon_bubble, bg=APP_BG, bd=0).place(x=8, y=17)
        title_block = tk.Frame(hero, bg=APP_BG)
        title_block.place(x=60, y=9)
        tk.Label(
            title_block, text="群聊日报", bg=APP_BG, fg=INK,
            font=(self._fonts["display"], 22, "bold"),
        ).pack(anchor="w")
        tk.Label(
            title_block, text="消息很多，重点交给我。",
            bg=APP_BG, fg=MUTED, font=(self._fonts["body"], 9),
        ).pack(anchor="w", pady=(3, 0))

        ttk.Button(hero, text="模型设置", style="Soft.TButton",
                   command=self._open_settings).place(relx=1.0, x=-12, y=22, anchor='ne')

        workspace = ttk.Frame(self.root, style="App.TFrame", padding=(20, 14, 20, 12))
        workspace.pack(fill="both", expand=True)
        workspace.columnconfigure(0, minsize=310, weight=0)
        workspace.columnconfigure(1, weight=1)
        workspace.rowconfigure(0, weight=1)

        left_shell = ttk.Frame(workspace, style="App.TFrame")
        left_shell.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        left_scroll = tk.Canvas(left_shell, width=310, bg=APP_BG, highlightthickness=0)
        left_bar = ttk.Scrollbar(left_shell, orient='vertical', command=left_scroll.yview)
        left_bar.pack(side='right', fill='y')
        left_scroll.pack(side='left', fill='both', expand=True)
        left_scroll.configure(yscrollcommand=left_bar.set)
        left = ttk.Frame(left_scroll, style="App.TFrame")
        left_slot = left_scroll.create_window(0, 0, window=left, anchor='nw')
        left.bind('<Configure>', lambda e: left_scroll.configure(scrollregion=left_scroll.bbox('all')))
        left_scroll.bind('<Configure>', lambda e: left_scroll.itemconfigure(left_slot, width=e.width))
        right_card = theme.HandCard(workspace, accent=CYAN, stretch=True, pad=18)
        right_card.grid(row=0, column=1, sticky="nsew")
        right = right_card.body
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)

        # 数据源卡片
        source_holder = theme.HandCard(left, accent=PRIMARY, pad=13)
        source_holder.pack(fill="x", pady=(0, 12))
        source_card = source_holder.body
        self._card_heading(source_card, "1", "连接微信", "微信保持登录状态", PRIMARY)
        init_row = ttk.Frame(source_card, style="Card.TFrame")
        init_row.pack(fill="x", pady=(12, 8))
        self.btn_init = ttk.Button(
            init_row, text="自动连接", command=self._on_init_click,
            style="Primary.TButton",
        )
        self.btn_init.pack(side="left", fill="x", expand=True)
        self.btn_manual = ttk.Button(
            init_row, text="选择目录", command=self._on_manual_select,
            style="Soft.TButton",
        )
        self.btn_manual.pack(side="left", padx=(8, 0))
        self.progress = ttk.Progressbar(
            source_card, mode="determinate", value=0,
            style="Accent.Horizontal.TProgressbar",
        )
        self.progress.pack(fill="x", pady=(9, 7))
        self.path_label = ttk.Label(
            source_card, text="尚未连接，将自动检测微信数据目录",
            style="Hint.TLabel", wraplength=286, justify="left",
        )
        self.path_label.pack(anchor="w")

        # 内容范围卡片
        range_holder = theme.HandCard(left, accent=PINK, pad=13)
        range_holder.pack(fill="x", pady=(0, 12))
        range_card = range_holder.body
        self._card_heading(range_card, "2", "选择内容", "群聊 / 聊天与日期范围", PINK)

        # 群聊日报 / 聊天日报 切换
        self.chat_kind_var = tk.StringVar(value="group")
        kind_row = tk.Frame(range_card, bg=CARD_BG)
        kind_row.pack(fill="x", pady=(10, 2))
        self.kind_group_btn = ttk.Radiobutton(
            kind_row, text="群聊日报", value="group",
            variable=self.chat_kind_var, style="Segment.Toolbutton",
            command=self._on_chat_kind_change,
        )
        self.kind_group_btn.pack(side="left")
        self.kind_private_btn = ttk.Radiobutton(
            kind_row, text="聊天日报", value="private",
            variable=self.chat_kind_var, style="Segment.Toolbutton",
            command=self._on_chat_kind_change,
        )
        self.kind_private_btn.pack(side="left", padx=(6, 0))

        self.chat_list_label = ttk.Label(
            range_card, text="选择群聊", style="FieldLabel.TLabel"
        )
        self.chat_list_label.pack(anchor="w", pady=(10, 4))

        # 搜索框：会话太多时按名字过滤
        self.chat_search_var = tk.StringVar()
        self.chat_search_entry = ttk.Entry(
            range_card, textvariable=self.chat_search_var,
            style="Modern.TEntry",
        )
        self.chat_search_entry.pack(fill="x", pady=(0, 6))
        self.chat_search_entry.bind("<KeyRelease>", self._on_chat_search)
        self._add_placeholder(self.chat_search_entry, self.chat_search_var,
                              "🔍 输入名字过滤…")

        self.chatroom_var = tk.StringVar()
        self.chatroom_combo = ttk.Combobox(
            range_card, textvariable=self.chatroom_var, state="disabled",
            style="Modern.TCombobox",
        )
        self.chatroom_combo.pack(fill="x")

        date_row = ttk.Frame(range_card, style="Card.TFrame")
        date_row.pack(fill="x", pady=(12, 0))
        date_row.columnconfigure((0, 1), weight=1)
        today = datetime.date.today()
        week_ago = today - datetime.timedelta(days=6)
        ttk.Label(date_row, text="开始日期", style="FieldLabel.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 5)
        )
        ttk.Label(date_row, text="结束日期", style="FieldLabel.TLabel").grid(
            row=0, column=1, sticky="w", padx=(5, 0)
        )
        self.start_date = DateEntry(
            date_row, locale="zh_CN", date_pattern="yyyy-mm-dd",
            year=week_ago.year, month=week_ago.month, day=week_ago.day,
        )
        self.start_date.grid(row=1, column=0, sticky="ew", padx=(0, 5), pady=(5, 0))
        self.end_date = DateEntry(
            date_row, locale="zh_CN", date_pattern="yyyy-mm-dd",
            year=today.year, month=today.month, day=today.day,
        )
        self.end_date.grid(row=1, column=1, sticky="ew", padx=(5, 0), pady=(5, 0))

        # 模型设置移入独立窗口，主界面只保留每天需要的选项。
        self.settings_window = tk.Toplevel(self.root)
        self.settings_window.withdraw()
        self.settings_window.title('模型与绘图设置')
        self.settings_window.geometry('660x700')
        self.settings_window.configure(bg=CARD_BG)
        self.settings_window.protocol('WM_DELETE_WINDOW', self.settings_window.withdraw)
        self.api_frame = ttk.Frame(self.settings_window, style='Card.TFrame', padding=24)
        self.api_frame.pack(fill='x')
        self._card_heading(self.api_frame, "AI", "文字模型", "密钥仅保存在本机", PRIMARY)
        ttk.Label(self.api_frame, text="服务商", style="FieldLabel.TLabel").pack(
            anchor="w", pady=(10, 4)
        )
        self.provider_combo = ttk.Combobox(
            self.api_frame, textvariable=self.provider_var,
            values=[provider_label(key) for key in PROVIDERS], state="readonly",
            style="Modern.TCombobox",
        )
        self.provider_combo.pack(fill="x")
        self.provider_combo.bind("<<ComboboxSelected>>", self._on_provider_changed)
        ttk.Label(self.api_frame, text="模型", style="FieldLabel.TLabel").pack(
            anchor="w", pady=(9, 4)
        )
        # 可编辑下拉框：预填该服务商的常用模型，也允许手动输入其它模型名
        self.model_entry = ttk.Combobox(
            self.api_frame, textvariable=self.model_var,
            values=provider_model_options(self.current_provider),
            style="Modern.TCombobox",
        )
        self.model_entry.pack(fill="x")
        ttk.Label(
            self.api_frame,
            text="可从下拉选常用模型，也可直接输入其它模型名。",
            style="Hint.TLabel", wraplength=300,
        ).pack(anchor="w", pady=(4, 0))
        ttk.Label(self.api_frame, text="API Key", style="FieldLabel.TLabel").pack(
            anchor="w", pady=(9, 4)
        )
        api_row = ttk.Frame(self.api_frame, style="Card.TFrame")
        api_row.pack(fill="x")
        self.api_entry = ttk.Entry(
            api_row, textvariable=self.api_key_var, show="*", style="Modern.TEntry"
        )
        self.api_entry.pack(side="left", fill="x", expand=True)
        self.show_key_btn = ttk.Button(
            api_row, text="显示", command=self._toggle_key_visibility,
            style="Soft.TButton", width=5,
        )
        self.show_key_btn.pack(side="left", padx=(7, 0))
        self.key_hint_label = ttk.Label(
            self.api_frame,
            text=(
                "Key 仅保存在本机；Gemini 文字与插画可共用同一个 Key"
                if self.current_provider == "gemini"
                else "Key 仅保存在本机；图片日报配图使用单独保存的 Gemini Key"
            ),
            style="Hint.TLabel", wraplength=286,
        )
        self.key_hint_label.pack(anchor="w", pady=(7, 0))
        self.drawing_settings = ttk.Frame(self.settings_window, style='Card.TFrame', padding=(24, 0, 24, 12))
        self.drawing_settings.pack(fill='x')
        self.drawing_settings.columnconfigure(0, weight=1)
        ttk.Button(self.settings_window, text='完成', command=self.settings_window.withdraw,
                   style='Primary.TButton').pack(anchor='e', padx=24, pady=12)

        template_holder = theme.HandCard(left, pad=16)
        template_holder.pack(fill='x')
        template_body = template_holder.body
        self._card_heading(template_body, '3', '日报模板', '换个版面，换种心情', PRIMARY)
        self.template_combo = ttk.Combobox(template_body, textvariable=self.template_var,
            values=[t['name'] for t in TEMPLATES.values()], state='readonly', style='Modern.TCombobox')
        self.template_combo.pack(fill='x', pady=(12, 8))
        self.template_combo.bind('<<ComboboxSelected>>', self._on_template_change)
        self.template_canvas = tk.Canvas(template_body, height=108, highlightthickness=0)
        self.template_canvas.pack(fill='x')
        self.template_canvas.bind('<Configure>', lambda e: self._paint_template(self.template_canvas))
        self.template_hint = ttk.Label(template_body, style='Hint.TLabel', wraplength=265)
        self.template_hint.pack(fill='x', pady=(8, 5))
        ttk.Button(template_body, text='放大预览 ↗', command=self._preview_template,
                   style='Soft.TButton').pack(anchor='e')
        self._refresh_template()
        # 右侧总结工作区
        header_row = ttk.Frame(right, style="Card.TFrame")
        header_row.grid(row=0, column=0, sticky="ew")
        header_row.columnconfigure(0, weight=1)
        title_area = ttk.Frame(header_row, style="Card.TFrame")
        title_area.grid(row=0, column=0, sticky="w")
        title_line = tk.Frame(title_area, bg=CARD_BG)
        title_line.pack(anchor="w")
        tk.Label(
            title_line, text="今日总结", bg=CARD_BG, fg=INK,
            font=(self._fonts["round"], 17, "bold"),
        ).pack(side="left")
        self._icon_title_star = theme.to_photo(theme.sparkle(19, YELLOW))
        tk.Label(title_line, image=self._icon_title_star, bg=CARD_BG, bd=0).pack(
            side="left", padx=(7, 0), pady=(4, 0)
        )
        self.msg_count_label = ttk.Label(title_area, text="等待选择群聊", style="Hint.TLabel")
        self.msg_count_label.pack(anchor="w", pady=(3, 0))
        self.btn_member_profiles = ttk.Button(
            header_row, text="群友名片", command=self._on_edit_member_profiles,
            state="disabled", style="Soft.TButton",
        )
        self.btn_member_profiles.grid(row=0, column=1, sticky="e", padx=(8, 8))
        self.btn_edit_prompt = ttk.Button(
            header_row, text="调整提示词", command=self._on_edit_prompt,
            style="Soft.TButton",
        )
        self.btn_edit_prompt.grid(row=0, column=2, sticky="e")

        action_row = ttk.Frame(right, style="Card.TFrame")
        action_row.grid(row=1, column=0, sticky="ew", pady=(18, 14))
        action_row.columnconfigure((0, 1), weight=1)
        self.btn_summarize = ttk.Button(
            action_row, text="生成文字总结", command=self._on_summarize_click,
            state="disabled", style="Soft.TButton",
        )
        self.btn_summarize.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.btn_image = ttk.Button(
            action_row, text="生成图片日报", command=self._on_image_click,
            state="disabled", style="Primary.TButton",
        )
        self.btn_image.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self.btn_redraw = ttk.Button(action_row, text="一键重画", command=self._on_redraw,
                                     state="disabled", style="Soft.TButton")
        self.btn_redraw.grid(row=0, column=2, padx=(8, 0))
        self.preview_check = ttk.Checkbutton(action_row, text="出图前预览并编辑",
            variable=self.preview_before_image_var, style="Modern.TCheckbutton")
        self.preview_check.grid(row=1, column=0, columnspan=2, sticky="w", pady=(12, 0))
        self.ai_images_check = ttk.Checkbutton(
            self.drawing_settings,
            text="启用 AI 绘图（关闭后使用经典本地排版，不调用图片接口）",
            variable=self.ai_topic_images_var,
            style="Modern.TCheckbutton",
        )
        self.ai_images_check.grid(
            row=1, column=0, sticky="w", pady=(10, 0)
        )
        self.illustration_mode_combo = ttk.Combobox(
            self.drawing_settings,
            textvariable=self.illustration_mode_var,
            values=[label for _key, label in ILLUSTRATION_MODES],
            state="readonly",
            style="Modern.TCombobox",
        )
        self.illustration_mode_combo.grid(
            row=2, column=0, sticky="ew", pady=(9, 0)
        )
        self.illustration_mode_combo.bind(
            "<<ComboboxSelected>>", self._on_illustration_mode_change
        )
        model_row = ttk.Frame(self.drawing_settings, style="Card.TFrame")
        model_row.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(model_row, text="图片模型", style="FieldLabel.TLabel").pack(
            side="left", padx=(0, 8)
        )
        self.image_model_combo = ttk.Combobox(
            model_row,
            textvariable=self.image_model_var,
            values=(
                "gemini-3.1-flash-image",
                "gemini-3-pro-image",
            ),
            state="readonly",
            style="Modern.TCombobox",
            width=24,
        )
        self.image_model_combo.pack(side="left")
        ttk.Label(
            self.drawing_settings,
            text="模板用于整图 AI 海报；本地排版仍使用经典样式。AI 写字可能有误，"
                 "请检查成图。重画会再次调用图片接口并可能产生费用。",
            style="Hint.TLabel", wraplength=560,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(7, 0))
        self.btn_cancel = ttk.Button(
            action_row,
            text="取消任务",
            command=self._on_cancel_task,
            state="disabled",
            style="Soft.TButton",
        )
        self.btn_cancel.grid(row=1, column=2, sticky="e", pady=(10, 0))

        self.result_tabs = ttk.Notebook(right)
        self.result_tabs.grid(row=2, column=0, sticky='nsew')
        text_shell = tk.Frame(
            self.result_tabs, bg="#F8FAFC", highlightbackground=BORDER,
            highlightthickness=1, bd=0,
        )
        self.result_tabs.add(text_shell, text='  文字总结  ')
        self.image_result = tk.Canvas(self.result_tabs, bg='#F8FAFC', highlightthickness=0)
        self.result_tabs.add(self.image_result, text='  图片日报  ')
        self.image_result.create_text(30, 35, anchor='nw', text='好看的头版，还差你点一下生成。', fill=MUTED)
        self.image_result.bind('<Configure>', self._fit_result_image)
        self.image_result.bind('<Double-Button-1>', lambda e: theme_open(self._result_image_path)
                               if getattr(self, '_result_image_path', None) else None)
        self.result_text = scrolledtext.ScrolledText(
            text_shell, wrap="word", state="disabled", bd=0,
            relief="flat", bg="#F8FAFC", fg=INK, insertbackground=PRIMARY,
            selectbackground="#DCE8FF", font=(self._fonts["body"], 10),
            padx=16, pady=14, spacing1=2, spacing3=5,
        )
        self.result_text.pack(fill="both", expand=True)
        self._show_result_placeholder()

        btn_result_row = ttk.Frame(right, style="Card.TFrame")
        btn_result_row.grid(row=3, column=0, sticky="e", pady=(13, 0))
        self.btn_copy = ttk.Button(
            btn_result_row, text="复制文字", command=self._on_copy,
            state="disabled", style="Soft.TButton",
        )
        self.btn_copy.pack(side="left", padx=(0, 8))
        self.btn_save = ttk.Button(
            btn_result_row, text="保存 TXT", command=self._on_save,
            state="disabled", style="Soft.TButton",
        )
        self.btn_save.pack(side="left")

        self.status_var = tk.StringVar(value="就绪 · 请先连接微信")
        status_bar = tk.Label(
            self.root, textvariable=self.status_var, bg=theme.PAPER_DEEP, fg=INK,
            anchor="w", padx=22, pady=8,
            font=(self._fonts["body"], 9, "bold"),
        )
        status_bar.pack(fill="x", side="bottom")

    def _open_settings(self):
        self.settings_window.deiconify()
        self.settings_window.lift()

    def _fit_result_image(self, _event=None):
        if not getattr(self, '_result_image_path', None):
            return
        with Image.open(self._result_image_path) as original:
            image = original.copy()
        image.thumbnail((max(1, self.image_result.winfo_width()-24),
                         max(1, self.image_result.winfo_height()-24)), Image.Resampling.LANCZOS)
        self._result_photo = ImageTk.PhotoImage(image)
        self.image_result.delete('all')
        self.image_result.create_image(self.image_result.winfo_width()/2, 12,
                                      anchor='n', image=self._result_photo)

    def _template_id(self):
        return next((key for key, item in TEMPLATES.items()
                     if item['name'] == self.template_var.get()), 'handdrawn')

    def _refresh_template(self):
        item = get_template(self._template_id())
        self.template_hint.config(text=item['tag'] + '\n整图 AI 模板 · 预览为版式示意')
        self._paint_template(self.template_canvas)

    def _on_template_change(self, _event=None):
        self.ai_topic_images_var.set(True)
        self.illustration_mode_var.set(ILLUSTRATION_MODE_LABELS['poster'])
        self._on_illustration_mode_change()
        self._refresh_template()

    def _paint_template(self, canvas):
        """Code-native layout sample, not a generated poster."""
        key = self._template_id()
        item = get_template(key)
        w, h = max(canvas.winfo_width(), 240), int(canvas['height'])
        canvas.delete('all')
        canvas.configure(bg=item['paper'])
        ink, accent = item['ink'], item['accent']
        large = h > 300
        unit = 2 if large else 1
        margin = 12 * unit
        canvas.create_text(margin, margin, anchor='nw', text='我们的群聊 / 日报',
                           fill=ink, font=(self._fonts['body'], 12 if large else 9, 'bold'))
        top = 36 * unit
        canvas.create_line(margin, top-6, w-margin, top-6, fill=accent, width=2)
        if key == 'editorial':
            boxes = [(0, 0, .62, .65), (.65, 0, 1, .3), (.65, .35, 1, .65), (0, .72, 1, 1)]
        elif key == 'night':
            boxes = [(0, 0, 1, .3), (0, .36, .48, .72), (.52, .36, 1, .72), (0, .79, 1, 1)]
        else:
            boxes = [(0, 0, .48, .64), (.52, 0, 1, .29), (.52, .35, 1, .64), (0, .71, 1, 1)]
        for index, (x0, y0, x1, y1) in enumerate(boxes):
            x0, x1 = margin+x0*(w-2*margin), margin+x1*(w-2*margin)
            y0, y1 = top+y0*(h-top-margin), top+y1*(h-top-margin)
            theme.round_rect(canvas, x0, y0, x1, y1, 5*unit,
                fill=('#252C48' if key == 'night' else '#FFFFFF'), outline=accent, width=1)
            labels = ['今日话题', '群友高光', '趣味成就', '今日金句']
            canvas.create_text(x0+6*unit, y0+5*unit, anchor='nw', text=labels[index],
                fill=ink, font=(self._fonts['body'], 10 if large else 7, 'bold'))
            for n in range(2 if y1-y0 > 45*unit else 1):
                y = y0+(21+n*8)*unit
                if y < y1-4:
                    canvas.create_line(x0+6*unit, y, x1-8*unit, y, fill=accent)
            if index == 0 and y1-y0 > 65*unit:
                canvas.create_text((x0+x1)/2, y1-20*unit, text='✦', fill=accent,
                                   font=(self._fonts['body'], 22*unit))

    def _preview_template(self):
        item = get_template(self._template_id())
        win = tk.Toplevel(self.root)
        win.title(item['name'] + ' · 模板预览')
        win.geometry('540x660')
        win.configure(bg=CARD_BG)
        ttk.Label(win, text=item['name'], style='PanelTitle.TLabel', padding=16).pack(anchor='w')
        canvas = tk.Canvas(win, height=450, highlightthickness=0)
        canvas.pack(fill='x', padx=20)
        canvas.bind('<Configure>', lambda e: self._paint_template(canvas))
        ttk.Label(win, text=item['description'] + '\n\n这是配色与版式示意，不是模型实测成图；实际插画和排版会变化。',
                  wraplength=480, padding=16).pack(fill='x')
        ttk.Button(win, text='就选这个', command=win.destroy, style='Primary.TButton').pack(anchor='e', padx=20)

    def _configure_styles(self):
        self._fonts = theme.fonts(self.root)
        body = self._fonts["body"]
        round_face = self._fonts["round"]
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("App.TFrame", background=APP_BG)
        style.configure("Card.TFrame", background=CARD_BG)
        style.configure("TLabel", background=CARD_BG, foreground=INK,
                        font=(body, 9))
        style.configure("Hint.TLabel", background=CARD_BG, foreground=MUTED,
                        font=(body, 8))
        style.configure("FieldLabel.TLabel", background=CARD_BG, foreground=INK,
                        font=(body, 9, "bold"))
        style.configure("PanelTitle.TLabel", background=CARD_BG, foreground=INK,
                        font=(round_face, 17, "bold"))
        style.configure("CardTitle.TLabel", background=CARD_BG, foreground=INK,
                        font=(round_face, 12, "bold"))

        # 按钮：粗体圆润字 + 海报同款彩色，按下去还会压深一档
        def pill(name, fill, active, disabled, fg="white", pad=(16, 11)):
            style.configure(name, background=fill, foreground=fg, borderwidth=0,
                            focusthickness=0, padding=pad,
                            font=(round_face, 10, "bold"))
            style.map(name,
                      background=[("pressed", active), ("active", active),
                                  ("disabled", disabled)],
                      foreground=[("disabled", "#6C7A8A")])

        pill("Primary.TButton", PRIMARY, PRIMARY_DARK, "#A9D3E8")
        pill("Teal.TButton", CYAN, "#06909F", "#A6E0E6")
        pill("Pink.TButton", PINK, "#D33A63", "#F5AEC1")
        style.configure("Soft.TButton", background="#F8FAFC", foreground=INK,
                        borderwidth=0, relief="flat", padding=(12, 9),
                        font=(body, 9, "bold"))
        style.map("Soft.TButton",
                  background=[("pressed", "#DCE8F7"), ("active", "#EEF4FB"),
                              ("disabled", "#EEF1F5")],
                  foreground=[("disabled", MUTED)])

        style.configure("Modern.TEntry", fieldbackground=theme.PAPER,
                        foreground=INK, bordercolor=BORDER, lightcolor=BORDER,
                        darkcolor=BORDER, borderwidth=1, padding=8)
        style.map("Modern.TEntry", bordercolor=[("focus", PRIMARY)])
        style.configure("Modern.TCombobox", fieldbackground=theme.PAPER,
                        background=theme.PAPER, foreground=INK,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                        borderwidth=1, arrowsize=15, padding=7,
                        arrowcolor=PRIMARY)
        style.map("Modern.TCombobox",
                  fieldbackground=[("readonly", theme.PAPER)],
                  bordercolor=[("focus", PRIMARY)],
                  selectbackground=[("readonly", theme.PAPER)],
                  selectforeground=[("readonly", INK)])
        style.configure("Accent.Horizontal.TProgressbar", background=YELLOW,
                        troughcolor=theme.PAPER_DEEP, bordercolor=CARD_BG,
                        lightcolor=YELLOW, darkcolor=YELLOW, thickness=7)
        style.configure("Modern.TCheckbutton", background=CARD_BG, foreground=INK,
                        font=(body, 9), padding=(0, 3),
                        indicatorcolor=theme.PAPER)
        style.map("Modern.TCheckbutton",
                  background=[("active", CARD_BG)],
                  indicatorcolor=[("selected", GREEN), ("pressed", GREEN)])

        # 群聊/聊天 分段切换：选中填粉底白字，未选中奶油底
        style.configure("Segment.Toolbutton", font=(round_face, 10, "bold"),
                        padding=(16, 7), background=theme.PAPER_DEEP,
                        foreground=INK, borderwidth=0, focusthickness=0,
                        anchor="center")
        style.map("Segment.Toolbutton",
                  background=[("selected", PINK), ("active", "#EFE7D5")],
                  foreground=[("selected", "white")])

    def _card_heading(self, parent, number, title, subtitle, accent=None):
        """卡片标题：手绘序号贴纸 + 圆润标题 + 灰色副标题。"""
        accent = PRIMARY
        row = tk.Frame(parent, bg=CARD_BG)
        row.pack(fill="x")
        badge = theme.to_photo(theme.step_badge(number, accent, 36))
        if not hasattr(self, "_badge_images"):
            self._badge_images = []
        self._badge_images.append(badge)
        tk.Label(row, image=badge, bg=CARD_BG, bd=0).pack(side="left")
        text = tk.Frame(row, bg=CARD_BG)
        text.pack(side="left", padx=(10, 0))
        tk.Label(
            text, text=title, bg=CARD_BG, fg=INK,
            font=(self._fonts["round"], 12, "bold"),
        ).pack(anchor="w")
        tk.Label(
            text, text=subtitle, bg=CARD_BG, fg=MUTED,
            font=(self._fonts["body"], 8),
        ).pack(anchor="w")

    def _show_result_placeholder(self):
        """空状态：用日报图标和明确的下一步提示，避免视觉噪音。"""
        self._placeholder_cat = theme.to_photo(theme.digest_mark(84))
        self.result_text.config(state="normal")
        self.result_text.delete("1.0", "end")
        self.result_text.tag_configure("center", justify="center")
        self.result_text.insert("end", "\n\n\n")
        self.result_text.image_create("end", image=self._placeholder_cat)
        self.result_text.insert(
            "end",
            "\n\n准备好生成一份日报？\n先连接微信，选好群聊和日期，再点上面的按钮\n",
            "center",
        )
        self.result_text.tag_add("center", "1.0", "end")
        self.result_text.config(state="disabled")

    def _set_status(self, msg, color="black"):
        self.root.after(0, lambda: self.status_var.set(msg))

    def _set_image_status(self, count, msg):
        """让耗时阶段在主面板与底部状态栏同时可见。"""
        text = str(msg)
        self._set_status(text)
        self.root.after(
            0,
            lambda value=f"共 {count} 条消息 · {text}": self.msg_count_label.config(
                text=value
            ),
        )

    def _on_cancel_task(self):
        self._cancel_event.set()
        self.btn_cancel.config(state="disabled")
        self._set_status("正在取消任务；当前网络请求结束后立即停止...")

    def _set_progress(self, running: bool):
        def _do():
            if running:
                self.progress.configure(mode="indeterminate")
                self.progress.start()
            else:
                self.progress.stop()
                # 停下来时切成 0 值的确定模式，免得留一截黄条像出错
                self.progress.configure(mode="determinate", value=0)

        self.root.after(0, _do)

    def _set_ui_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        def _do():
            self.btn_init.config(state=state)
            self.template_combo.config(state='readonly' if enabled else 'disabled')
            self.btn_redraw.config(state="normal" if enabled and self._last_digest else "disabled")
            self.preview_check.config(state=state)
            self.btn_manual.config(state=state)
            self.btn_summarize.config(
                state="normal" if enabled and self._initialized else "disabled"
            )
            self.btn_image.config(
                state="normal" if enabled and self._initialized else "disabled"
            )
            self.chatroom_combo.config(
                state="readonly" if enabled and self._initialized else "disabled"
            )
            self.chat_search_entry.config(state=state)
            self.kind_group_btn.config(state=state)
            self.kind_private_btn.config(state=state)
            self.btn_member_profiles.config(
                state="normal" if enabled and self._initialized else "disabled"
            )
            self.provider_combo.config(state="readonly" if enabled else "disabled")
            self.model_entry.config(state="normal" if enabled else "disabled")
            self.api_entry.config(state=state)
            self.show_key_btn.config(state=state)
            self.ai_images_check.config(state=state)
            self.illustration_mode_combo.config(
                state="readonly" if enabled else "disabled"
            )
            self.image_model_combo.config(state="readonly" if enabled else "disabled")
            if enabled:
                self.btn_cancel.config(state="disabled")
        self.root.after(0, _do)

    def _current_illustration_mode(self):
        return _illustration_mode_from_label(self.illustration_mode_var.get())

    def _on_illustration_mode_change(self, _event=None):
        """整图海报必须用能画字的 pro 模型，切换时顺手把图片模型对上。"""
        mode = self._current_illustration_mode()
        current = str(self.image_model_var.get() or "").strip()
        if mode == "poster" and current != GEMINI_POSTER_MODEL:
            self.image_model_var.set(GEMINI_POSTER_MODEL)
        elif mode != "poster" and current == GEMINI_POSTER_MODEL:
            self.image_model_var.set(GEMINI_IMAGE_MODEL)

    def _provider_key_from_label(self, label):
        for key, config in PROVIDERS.items():
            if config["label"] == label:
                return key
        return DEFAULT_PROVIDER

    def _remember_provider_settings(self):
        self.provider_keys[self.current_provider] = self.api_key_var.get().strip()
        self.provider_models[self.current_provider] = self.model_var.get().strip()

    def _on_provider_changed(self, _event=None):
        self._remember_provider_settings()
        selected = self._provider_key_from_label(self.provider_var.get())
        self.current_provider = selected
        self.api_key_var.set(str(self.provider_keys.get(selected, "")))
        self.model_entry.config(values=provider_model_options(selected))
        self.model_var.set(
            str(self.provider_models.get(selected) or provider_default_model(selected))
        )
        hint = (
            "Key 仅保存在本机；Gemini 文字与插画可共用同一个 Key"
            if selected == "gemini"
            else "Key 仅保存在本机；图片日报配图使用单独保存的 Gemini Key"
        )
        self.key_hint_label.config(text=hint)
        self._set_status(f"已切换到 {provider_label(selected)}")

    def _on_manual_select(self):
        """让用户手动选择微信数据文件夹（wxid_xxx 或 xwechat_files）"""
        folder = filedialog.askdirectory(
            title="选择微信数据文件夹（含 db_storage 的那个，或其父目录）"
        )
        if not folder:
            return

        # 检查用户选的是 wxid_xxx 本身，还是 xwechat_files 父目录
        db_direct = os.path.join(folder, "db_storage", "message", "message_0.db")
        if os.path.isfile(db_direct):
            # 直接选中了 wxid_xxx 目录
            self._manual_user_dir = folder
        else:
            # 可能选中了 xwechat_files，找里面的 wxid_xxx 子目录
            found = None
            found_mtime = -1
            try:
                for sub in os.listdir(folder):
                    subpath = os.path.join(folder, sub)
                    db_path = os.path.join(subpath, "db_storage", "message", "message_0.db")
                    if os.path.isfile(db_path):
                        mtime = os.path.getmtime(db_path)
                        if mtime > found_mtime:
                            found_mtime = mtime
                            found = subpath
            except Exception:
                pass

            if found:
                self._manual_user_dir = found
            else:
                messagebox.showerror(
                    "文件夹无效",
                    "在选择的文件夹中未找到微信数据库文件（db_storage/message/message_0.db）。\n\n"
                    "请重新选择，应该选包含 db_storage 文件夹的那一层目录。"
                )
                return

        # 更新路径提示
        self.path_label.config(
            text=f"数据目录：{self._manual_user_dir}  （手动指定）",
            foreground="#0066cc"
        )
        self._set_status("已手动指定目录，请点击「自动初始化」开始读取。")

    def _toggle_key_visibility(self):
        if self.api_entry.cget("show") == "*":
            self.api_entry.config(show="")
            self.show_key_btn.config(text="隐藏")
        else:
            self.api_entry.config(show="*")
            self.show_key_btn.config(text="显示")

    def _display_result(self, text: str):
        self.result_tabs.select(0)
        self.result_text.config(state="normal")
        self.result_text.delete("1.0", "end")
        self.result_text.insert("end", text)
        self.result_text.config(state="disabled")
        self.btn_copy.config(state="normal")
        self.btn_save.config(state="normal")

    def _cleanup_connections(self):
        msg_connections = (
            self.conn_msg if isinstance(self.conn_msg, (list, tuple))
            else [self.conn_msg]
        )
        for conn in [*msg_connections, self.conn_contact]:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass
        msg_paths = getattr(self, "tmp_msg_paths", [])
        for path in [*msg_paths, self.tmp_contact_path]:
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except Exception:
                    pass
        self.conn_msg = []
        self.conn_contact = None
        self.tmp_msg_paths = []
        self.tmp_contact_path = None
        self.contact_name_map = {}
        self.group_member_name_map = {}
        self.contact_gender_map = {}
        self._last_summary_key = None
        self._last_summary_text = ""

    # ─────────────────────────────────────────────────────────────────────────
    # 初始化流程
    # ─────────────────────────────────────────────────────────────────────────

    def _on_init_click(self):
        self._cancel_event.clear()
        self._set_ui_enabled(False)
        self._set_progress(True)
        self.btn_cancel.config(state="normal")
        self._set_status("正在初始化...")
        threading.Thread(target=self._init_thread, daemon=True).start()

    def _key_failure_hint(self):
        """密钥读取失败时的提示；macOS 上多半是权限问题，单独说明。"""
        if theme_is_macos():
            return (
                "已找到微信数据库，但没有读取到密钥。\n\n"
                "macOS 读取微信进程内存需要更高权限：\n"
                "1. 关闭 SIP：重启进入恢复模式，终端执行 csrutil disable；\n"
                "2. 用管理员权限启动本程序：在终端里 sudo 运行；\n"
                "3. 确认微信已登录并保持运行。\n\n"
                "若不想改动系统安全设置，也可在别处导出聊天记录后手动分析。"
            )
        return (
            "已找到微信数据库，但没有读取到可验证的数据库密钥。\n\n"
            "请确认微信已登录并保持运行；如果微信刚升级过，"
            "请完全退出微信、重新打开并登录后再试。"
        )

    def _init_thread(self):
        try:
            # 清理旧连接
            self._cleanup_connections()
            self.chatrooms = []

            # 1. 查找微信数据目录
            if self._manual_user_dir:
                user_dir = self._manual_user_dir
                self._set_status(f"使用手动指定目录：{user_dir}")
            else:
                self._set_status("正在自动查找微信数据目录...")
                base_dir, user_dir = find_wechat_data_dir()
                if not user_dir:
                    self.root.after(0, lambda: messagebox.showerror(
                        "自动检测失败",
                        "未能自动找到微信数据目录。\n\n"
                        "请点击「手动选择文件夹」，找到微信存储数据的文件夹。\n\n"
                        "提示：在微信电脑版 → 设置 → 文件管理，\n"
                        "可以看到「微信文件的存储位置」，进入该目录，\n"
                        "找到形如 wxid_xxxxxxxx 的文件夹，选中它。"
                    ))
                    return
            db_storage = find_db_storage(user_dir)

            # 2. 从内存提取密钥
            self._set_status("正在自动读取微信数据库密钥（兼容微信 4.0 / 4.1）...")
            key_map, db_files, salt_to_dbs = extract_keys_from_memory(db_storage)
            if not key_map:
                self.root.after(0, lambda: messagebox.showerror(
                    "密钥读取失败", self._key_failure_hint()
                ))
                return
            self.key_map = key_map

            # 3. 解密数据库
            self._set_status("正在解密全部消息分库...")
            self._decrypt_and_open_dbs(db_storage)

            # 4. 加载群聊列表
            self._set_status(
                f"已打开 {len(self.conn_msg)} 个消息分库，正在汇总群聊列表..."
            )
            self._load_chatrooms()

            # 5. 更新 UI
            self._initialized = True
            self.root.after(0, self._populate_chatroom_combo)
            n = len(self.chatrooms)
            self._set_status(f"初始化完成，共找到 {n} 个群聊。请选择群聊和时间范围。", "black")
            # 显示最终使用的数据目录
            display_dir = user_dir
            suffix = "（手动指定）" if self._manual_user_dir else "（自动检测）"
            self.root.after(0, lambda: self.path_label.config(
                text=f"数据目录：{display_dir}  {suffix}",
                foreground="green"
            ))

        except Exception as e:
            self._cleanup_connections()
            err = traceback.format_exc()
            self.root.after(0, lambda: messagebox.showerror("初始化失败", str(e)))
            self._set_status(f"初始化失败：{e}")
        finally:
            self._set_progress(False)
            self._set_ui_enabled(True)

    def _decrypt_and_open_dbs(self, db_storage):
        # 微信 4.1 会按时间把消息拆到 message_0.db、message_1.db ...。
        # 必须全部打开，否则只能看到某个历史分片，近期日期会被误判为无消息。
        message_dir = os.path.join(db_storage, "message")
        message_files = []
        for name in os.listdir(message_dir):
            match = re.fullmatch(r"message_(\d+)\.db", name, flags=re.IGNORECASE)
            if match:
                message_files.append((int(match.group(1)), name))
        message_files.sort()
        if not message_files:
            raise RuntimeError("未找到 message_N.db 消息数据库")

        contact_db_path = os.path.join(db_storage, "contact", "contact.db")
        required_space = sum(
            os.path.getsize(os.path.join(message_dir, name))
            for _index, name in message_files
        )
        if os.path.isfile(contact_db_path):
            required_space += os.path.getsize(contact_db_path)
        temp_dir = select_decrypt_temp_dir(required_space)

        missing_keys = []
        for _index, name in message_files:
            source_path = os.path.join(message_dir, name)
            with open(source_path, "rb") as source:
                salt = source.read(16).hex()
            key = self.key_map.get(salt)
            if not key:
                missing_keys.append(name)
                continue
            tmp_path = decrypt_db(source_path, key, temp_dir=temp_dir)
            self.tmp_msg_paths.append(tmp_path)
            self.conn_msg.append(
                sqlite3.connect(tmp_path, check_same_thread=False)
            )

        if missing_keys:
            self._cleanup_connections()
            raise RuntimeError(
                "以下消息分库缺少解密密钥：" + "、".join(missing_keys)
            )
        if not self.conn_msg:
            raise RuntimeError("没有成功打开任何消息数据库")

        # contact.db（可选，获取群名用）
        if os.path.isfile(contact_db_path):
            with open(contact_db_path, "rb") as f:
                contact_salt = f.read(16).hex()
            contact_key = self.key_map.get(contact_salt)
            if contact_key:
                self.tmp_contact_path = decrypt_db(
                    contact_db_path, contact_key, temp_dir=temp_dir
                )
                self.conn_contact = sqlite3.connect(self.tmp_contact_path, check_same_thread=False)

    def _load_chatrooms(self):
        self.contact_name_map = load_contact_name_map(self.conn_contact)
        self.group_member_name_map = load_group_member_name_map(self.conn_contact)
        self.contact_gender_map = load_contact_gender_map(self.conn_contact)

        group_list = []
        for cr_id, count in list_chatrooms(self.conn_msg):
            nick = self.contact_name_map.get(cr_id, cr_id.replace("@chatroom", ""))
            group_list.append((cr_id, count, f"{nick}  （{count} 条消息）"))

        private_list = []
        for user_name, count in list_private_chats(self.conn_msg):
            nick = self.contact_name_map.get(user_name, user_name)
            private_list.append((user_name, count, f"{nick}  （{count} 条消息）"))

        self._all_chats = {"group": group_list, "private": private_list}
        self.chatrooms = list(self._all_chats.get(self.chat_kind, group_list))

    def _current_chat_source(self):
        return self._all_chats.get(self.chat_kind, [])

    def _populate_chatroom_combo(self):
        """按当前会话类型 + 搜索词刷新下拉框；self.chatrooms 即为过滤后列表。"""
        query = str(self.chat_search_var.get() or "").strip().lower()
        if query == "🔍 输入名字过滤…".lower():
            query = ""
        source = self._current_chat_source()
        if query:
            self.chatrooms = [c for c in source if query in c[2].lower()]
        else:
            self.chatrooms = list(source)
        values = [c[2] for c in self.chatrooms]
        self.chatroom_combo["values"] = values
        self.chatroom_combo.config(state="readonly" if values else "disabled")
        if values:
            self.chatroom_combo.current(0)
        else:
            self.chatroom_var.set("")
        has_any = bool(self._all_chats.get("group") or self._all_chats.get("private"))
        state = "normal" if has_any else "disabled"
        self.btn_summarize.config(state=state)
        self.btn_image.config(state=state)

    def _on_chat_kind_change(self):
        """群聊/聊天切换：换数据源，清空搜索，刷新列表与措辞。"""
        self.chat_kind = self.chat_kind_var.get()
        self._clear_placeholder_state(self.chat_search_entry)
        self.chat_search_var.set("")
        noun = "群聊" if self.chat_kind == "group" else "聊天"
        self.chat_list_label.config(text=f"选择{noun}")
        self._set_placeholder(self.chat_search_entry, self.chat_search_var,
                              "🔍 输入名字过滤…")
        if self._initialized:
            self._populate_chatroom_combo()

    def _on_chat_search(self, _event=None):
        if getattr(self.chat_search_entry, "_placeholder_on", False):
            return
        if self._initialized:
            self._populate_chatroom_combo()

    # ── 搜索框占位符 ────────────────────────────────────────────────────
    def _add_placeholder(self, entry, var, text):
        entry._placeholder_text = text
        self._set_placeholder(entry, var, text)
        entry.bind("<FocusIn>", lambda e: self._clear_placeholder_state(entry, var))
        entry.bind("<FocusOut>", lambda e: self._restore_placeholder(entry, var))

    def _set_placeholder(self, entry, var, text):
        var.set(text)
        entry._placeholder_on = True

    def _clear_placeholder_state(self, entry, var=None):
        if getattr(entry, "_placeholder_on", False):
            entry._placeholder_on = False
            if var is not None:
                var.set("")

    def _restore_placeholder(self, entry, var):
        if not str(var.get() or "").strip():
            self._set_placeholder(entry, var, getattr(entry, "_placeholder_text", ""))
            if self._initialized:
                self._populate_chatroom_combo()

    def _sender_name_map_for_room(self, chatroom_id):
        """合并联系人备注、自动群昵称和用户手动名片；群昵称优先。"""
        names = dict(self.contact_name_map)
        names.update(self.group_member_name_map.get(chatroom_id, {}))
        custom_profiles = self.member_profiles.get(chatroom_id, {})
        if isinstance(custom_profiles, dict):
            for username, profile in custom_profiles.items():
                if not isinstance(profile, dict):
                    continue
                custom_name = str(profile.get("name") or "").strip()
                if custom_name:
                    names[str(username)] = custom_name
        return names

    def _member_gender_hints(self, chatroom_id, sender_name_map):
        """把明确设置的性别转换成模型能识别的“展示昵称 → 性别”映射。"""
        hints = {}
        profiles = self.member_profiles.get(chatroom_id, {})
        if not isinstance(profiles, dict):
            return hints
        for username, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            gender = str(
                profile["gender"]
                if "gender" in profile
                else self.contact_gender_map.get(str(username), "unspecified")
            ).strip().lower()
            display_name = str(sender_name_map.get(str(username)) or "").strip()
            if gender in {"female", "male"} and display_name:
                hints[display_name] = gender
        return hints

    def _on_edit_member_profiles(self):
        """允许用户校正群昵称与人物形象，避免模型根据昵称瞎猜。"""
        idx = self.chatroom_combo.current()
        if idx < 0 or idx >= len(self.chatrooms):
            messagebox.showwarning("提示", "请先选择一个群聊。")
            return
        chatroom_id, _count, display = self.chatrooms[idx]
        start_d, end_d = self.start_date.get_date(), self.end_date.get_date()
        if start_d > end_d:
            messagebox.showwarning("日期错误", "开始日期不能晚于结束日期。")
            return
        start_ts = int(datetime.datetime.combine(start_d, datetime.time.min).timestamp())
        end_ts = int(datetime.datetime.combine(end_d, datetime.time.max).timestamp())
        try:
            active_users = get_sender_usernames_by_range(
                self.conn_msg, chatroom_id, start_ts, end_ts
            )
        except Exception as exc:
            messagebox.showerror("读取群成员失败", str(exc))
            return
        users = sorted(set(active_users) | set(self.group_member_name_map.get(chatroom_id, {})))
        if not users:
            messagebox.showinfo("没有可设置的群友", "所选时间范围内没有识别到可编辑的发言人。")
            return

        name_map = self._sender_name_map_for_room(chatroom_id)
        existing = self.member_profiles.get(chatroom_id, {})
        window = tk.Toplevel(self.root)
        window.title("群友名片 · 群昵称与人物形象")
        window.geometry("720x620")
        window.minsize(620, 460)
        window.transient(self.root)

        top = ttk.Frame(window, padding=(18, 16, 18, 8))
        top.pack(fill="x")
        ttk.Label(top, text=f"{display.split('（')[0].strip()} · 群友名片", style="PanelTitle.TLabel").pack(anchor="w")
        ttk.Label(
            top,
            text="已自动优先读取群昵称；若微信联系人库明确记录性别，也会自动预填。\n"
                 "没有资料时使用中性人物；可在这里覆盖自动结果，绝不根据昵称或头像猜。",
            style="Hint.TLabel",
            justify="left",
        ).pack(anchor="w", pady=(5, 0))

        table = ttk.Frame(window, padding=(18, 4, 8, 0))
        table.pack(fill="both", expand=True)
        canvas = tk.Canvas(table, bg=APP_BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        rows_frame = tk.Frame(canvas, bg=APP_BG)
        canvas_window = canvas.create_window((0, 0), window=rows_frame, anchor="nw")
        rows_frame.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(canvas_window, width=event.width),
        )

        header = tk.Frame(rows_frame, bg=APP_BG)
        header.pack(fill="x", pady=(0, 5))
        header.columnconfigure(0, weight=3)
        header.columnconfigure(1, weight=3)
        header.columnconfigure(2, weight=2)
        for column, text in enumerate(("当前识别的群友", "日报显示名", "人物形象")):
            tk.Label(header, text=text, bg=APP_BG, fg=MUTED,
                     font=(self._fonts["body"], 9, "bold")).grid(
                row=0, column=column, sticky="w", padx=(4, 8)
            )

        edited_rows = []
        for username in users:
            profile = existing.get(username, {}) if isinstance(existing, dict) else {}
            auto_name = str(
                self.group_member_name_map.get(chatroom_id, {}).get(username)
                or self.contact_name_map.get(username)
                or username
            ).strip()
            current_name = str(profile.get("name") or auto_name).strip()
            auto_gender = str(
                self.contact_gender_map.get(username) or "unspecified"
            ).strip().lower()
            gender = str(
                profile["gender"] if "gender" in profile else auto_gender
            ).strip().lower()
            if gender not in GENDER_VALUES:
                gender = "unspecified"
            row = tk.Frame(rows_frame, bg=CARD_BG, highlightbackground=BORDER,
                           highlightthickness=1)
            row.pack(fill="x", pady=3)
            row.columnconfigure(0, weight=3)
            row.columnconfigure(1, weight=3)
            row.columnconfigure(2, weight=2)
            tk.Label(row, text=f"{auto_name}\n{username}", bg=CARD_BG, fg=INK,
                     justify="left", anchor="w", font=(self._fonts["body"], 9),
                     wraplength=220).grid(row=0, column=0, sticky="ew", padx=8, pady=7)
            name_var = tk.StringVar(value=current_name)
            ttk.Entry(row, textvariable=name_var, style="Modern.TEntry").grid(
                row=0, column=1, sticky="ew", padx=(0, 8), pady=8
            )
            gender_var = tk.StringVar(value=GENDER_VALUES[gender])
            ttk.Combobox(
                row, textvariable=gender_var, state="readonly",
                values=[label for label, _value in GENDER_CHOICES],
                style="Modern.TCombobox",
            ).grid(row=0, column=2, sticky="ew", padx=(0, 8), pady=8)
            edited_rows.append((username, auto_name, auto_gender, name_var, gender_var))

        buttons = ttk.Frame(window, padding=(18, 10, 18, 16))
        buttons.pack(fill="x")

        def save_profiles():
            group_profiles = dict(existing) if isinstance(existing, dict) else {}
            for username, auto_name, auto_gender, name_var, gender_var in edited_rows:
                desired_name = name_var.get().strip()
                gender = GENDER_LABELS.get(gender_var.get(), "unspecified")
                profile = {}
                if desired_name and desired_name != auto_name:
                    profile["name"] = desired_name
                # 与自动读取的值相同则不落盘；主动改为“未指定”可屏蔽自动资料。
                if gender != auto_gender:
                    profile["gender"] = gender
                if profile:
                    group_profiles[username] = profile
                else:
                    group_profiles.pop(username, None)
            if group_profiles:
                self.member_profiles[chatroom_id] = group_profiles
            else:
                self.member_profiles.pop(chatroom_id, None)
            self.config["member_profiles"] = self.member_profiles
            self._last_summary_key = None
            self._last_summary_text = ""
            self._set_status("群友名片已保存；下次生成将使用群昵称和人物形象设置。")
            window.destroy()

        ttk.Button(buttons, text="保存名片", command=save_profiles,
                   style="Primary.TButton").pack(side="right")
        ttk.Button(buttons, text="取消", command=window.destroy,
                   style="Soft.TButton").pack(side="right", padx=(0, 8))

    # ─────────────────────────────────────────────────────────────────────────
    # 生成总结流程
    # ─────────────────────────────────────────────────────────────────────────

    def _on_summarize_click(self):
        if not self.conn_msg:
            messagebox.showwarning("提示", "请先点击「初始化」")
            return
        if not self.chatroom_var.get():
            messagebox.showwarning("提示", "请选择群聊")
            return

        start_d = self.start_date.get_date()
        end_d = self.end_date.get_date()
        if start_d > end_d:
            messagebox.showwarning("日期错误", "开始日期不能晚于结束日期")
            return

        # 在主线程读取日期（线程安全）
        start_d = self.start_date.get_date()
        end_d = self.end_date.get_date()
        idx = self.chatroom_combo.current()
        self._remember_provider_settings()
        provider = self.current_provider
        api_key = str(self.provider_keys.get(provider) or "").strip()
        model = str(self.provider_models.get(provider) or "").strip()
        if not api_key:
            messagebox.showwarning(
                "提示", f"请先填写 {provider_label(provider)} API Key。"
            )
            return
        if not model:
            messagebox.showwarning("提示", "请先填写模型名。")
            return
        self._set_ui_enabled(False)
        self._set_progress(True)
        self._display_result("")
        self.msg_count_label.config(text="")
        self._set_status("正在读取消息...")
        threading.Thread(target=self._summarize_thread,
                         args=(idx, start_d, end_d, provider, api_key, model),
                         daemon=True).start()

    def _summarize_thread(self, idx, start_d, end_d, provider, api_key, model):
        try:
            # 找选中的群
            if idx < 0 or idx >= len(self.chatrooms):
                self._set_status("请选择一个群聊")
                return
            chatroom_id, count, display = self.chatrooms[idx]

            # 转时间戳（已在主线程获取日期）
            start_ts = int(datetime.datetime.combine(start_d, datetime.time.min).timestamp())
            end_ts = int(datetime.datetime.combine(end_d, datetime.time.max).timestamp())
            sender_name_map = self._sender_name_map_for_room(chatroom_id)

            # 读消息
            messages = get_messages_by_range(
                self.conn_msg,
                chatroom_id,
                start_ts,
                end_ts,
                sender_name_map=sender_name_map,
            )
            n = len(messages)

            if not messages:
                self.root.after(0, lambda: self._display_result("该时间段内没有文本消息。"))
                self._set_status(f"未找到消息（{start_d} 至 {end_d}）")
                return

            self.root.after(0, lambda: self.msg_count_label.config(
                text=f"找到 {n} 条消息，AI 总结中..."
            ))
            self._set_status(f"找到 {n} 条消息，正在 AI 总结...")

            # AI 总结
            days_approx = (end_d - start_d).days + 1
            group_name = display.split("（")[0].strip()
            summary = ai_summarize(messages, api_key,
                                   group_id=chatroom_id, days=days_approx,
                                   prompt_template=self._prompt_template,
                                   progress_callback=self._set_status,
                                   provider=provider, model=model,
                                   cancel_event=self._cancel_event)
            self._last_summary_key = (
                chatroom_id,
                start_ts,
                end_ts,
                n,
                messages[-1],
                provider,
                model,
            )
            self._last_summary_text = summary

            # 加上日期标题
            header = (f"群聊：{group_name}\n"
                      f"时间：{start_d} 至 {end_d}（共 {n} 条消息）\n"
                      f"{'─' * 40}\n")
            full_text = header + summary

            self.root.after(0, lambda: self._display_result(full_text))
            self.root.after(0, lambda: self.msg_count_label.config(text=f"共 {n} 条消息"))
            self._set_status("总结完成！")

        except Exception as e:
            err_msg = str(e)
            self.root.after(0, lambda: messagebox.showerror("生成失败", err_msg))
            self._set_status(f"生成失败：{err_msg}")
        finally:
            self._set_progress(False)
            self._set_ui_enabled(True)

    def _on_image_click(self):
        self._active_template_id = self._template_id()
        if not self.conn_msg:
            messagebox.showwarning("提示", "请先点击「初始化」")
            return
        if not self.chatroom_var.get():
            messagebox.showwarning("提示", "请选择群聊")
            return

        start_d = self.start_date.get_date()
        end_d = self.end_date.get_date()
        if start_d > end_d:
            messagebox.showwarning("日期错误", "开始日期不能晚于结束日期")
            return

        idx = self.chatroom_combo.current()
        self._remember_provider_settings()
        provider = self.current_provider
        api_key = str(self.provider_keys.get(provider) or "").strip()
        model = str(self.provider_models.get(provider) or "").strip()
        if not api_key:
            messagebox.showwarning(
                "提示", f"请先填写 {provider_label(provider)} API Key。"
            )
            return
        if not model:
            messagebox.showwarning("提示", "请先填写模型名。")
            return
        use_ai_images = bool(self.ai_topic_images_var.get())
        illustration_mode = self._current_illustration_mode()
        image_model = str(self.image_model_var.get() or GEMINI_IMAGE_MODEL).strip()
        image_api_key = str(self.provider_keys.get("gemini") or "").strip()
        if use_ai_images and not image_api_key:
            messagebox.showwarning(
                "需要 Gemini Key",
                "AI 话题插画使用 Gemini 图片模型。请先切换到 Google Gemini，"
                "填写并保存一次 Key；之后使用其他文字模型时也能复用。",
            )
            return

        chat_kind = self.chat_kind
        noun = "群聊" if chat_kind == "group" else "聊天"
        output_path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG 图片", "*.png")],
            initialfile=(
                f"{_safe_filename_part(self.chatrooms[idx][2].split('（')[0])}"
                f"_{noun}日报_{start_d:%Y%m%d}.png"
            ),
            title="保存单页图片日报",
        )
        if not output_path:
            return

        self._cancel_event.clear()
        self._set_ui_enabled(False)
        self._set_progress(True)
        self.btn_cancel.config(state="normal")
        self.msg_count_label.config(text="")
        self._set_status("正在读取消息，准备图片日报...")
        threading.Thread(
            target=self._image_thread,
            args=(idx, start_d, end_d, provider, api_key, model, output_path,
                  use_ai_images, image_api_key, illustration_mode, image_model,
                  self.preview_before_image_var.get(), chat_kind),
            daemon=True,
        ).start()

    def _image_thread(self, idx, start_d, end_d, provider, api_key, model,
                      output_path, use_ai_images, image_api_key,
                      illustration_mode="poster", image_model=None,
                      preview_first=False, chat_kind="group"):
        try:
            if idx < 0 or idx >= len(self.chatrooms):
                raise ValueError("请选择一个群聊")
            chatroom_id, _count, display = self.chatrooms[idx]
            group_name = display.split("（")[0].strip()
            start_ts = int(
                datetime.datetime.combine(start_d, datetime.time.min).timestamp()
            )
            end_ts = int(
                datetime.datetime.combine(end_d, datetime.time.max).timestamp()
            )
            sender_name_map = self._sender_name_map_for_room(chatroom_id)
            messages = get_messages_by_range(
                self.conn_msg,
                chatroom_id,
                start_ts,
                end_ts,
                sender_name_map=sender_name_map,
            )
            if not messages:
                raise ValueError("该时间段内没有文本消息。")

            count = len(messages)
            report_progress = lambda message: self._set_image_status(count, message)
            cache_key = (
                chatroom_id,
                start_ts,
                end_ts,
                count,
                messages[-1],
                provider,
                model,
            )
            self.root.after(
                0,
                lambda: self.msg_count_label.config(
                    text=f"共 {count} 条消息，图片日报生成中..."
                ),
            )
            if self._last_summary_key == cache_key and self._last_summary_text:
                summary = self._last_summary_text
                report_progress("正在复用刚才的文字总结...")
            else:
                days_approx = (end_d - start_d).days + 1
                summary = ai_summarize(
                    messages,
                    api_key,
                    group_id=chatroom_id,
                    days=days_approx,
                    prompt_template=self._prompt_template,
                    progress_callback=report_progress,
                    provider=provider,
                    model=model,
                    cancel_event=self._cancel_event,
                )
                self._last_summary_key = cache_key
                self._last_summary_text = summary

            date_range = f"{start_d} 至 {end_d}"
            days_span = (end_d - start_d).days + 1
            digest = ai_newspaper_digest(
                summary,
                api_key,
                group_name,
                date_range,
                count,
                provider=provider,
                model=model,
                progress_callback=report_progress,
                cancel_event=self._cancel_event,
                member_genders=self._member_gender_hints(
                    chatroom_id, sender_name_map
                ),
                labels=build_scope_labels(days_span, chat_kind),
            )
            if preview_first:
                digest = self._review_digest(digest)
                if digest is None:
                    return
            digest['template_id'] = getattr(self, '_active_template_id', 'handdrawn')
            self._last_digest = copy.deepcopy(digest)
            self._render_digest(digest, output_path, use_ai_images, image_api_key,
                                illustration_mode, image_model)
        except Exception as exc:
            self._set_status(f"图片日报生成失败：{exc}")
            if not self._cancel_event.is_set():
                self.root.after(0, lambda error=str(exc): messagebox.showerror("生成失败", error))
        finally:
            self._set_progress(False)
            self._set_ui_enabled(True)

    def _render_digest(self, digest, output_path, use_ai_images, image_api_key,
                       illustration_mode, image_model):
        count = int(digest.get('message_count') or 0)
        report_progress = lambda message: self._set_image_status(count, message)
        try:
            topic_images = []
            image_warning = ""
            poster = None
            if use_ai_images and illustration_mode == "poster":
                try:
                    poster = generate_full_poster(
                        digest,
                        image_api_key,
                        progress_callback=report_progress,
                        cancel_event=self._cancel_event,
                        model=image_model or GEMINI_POSTER_MODEL,
                    )
                except (RuntimeError, ValueError) as exc:
                    if self._cancel_event.is_set():
                        raise
                    image_warning = (
                        f"{exc}\n\n已自动改用本地排版版本：文字准确，但不是整张手绘。"
                    )
                    report_progress("整图海报失败，正在改用本地排版...")
            elif use_ai_images:
                illustration_requests = build_digest_illustration_requests(
                    digest, detailed=illustration_mode == "detailed"
                )
                try:
                    topic_images = generate_topic_images(
                        illustration_requests,
                        image_api_key,
                        progress_callback=report_progress,
                    cancel_event=self._cancel_event,
                    provider="gemini",
                    model=image_model or GEMINI_IMAGE_MODEL,
                    mode="detailed" if illustration_mode == "detailed" else "sheet",
                )
                except RuntimeError as exc:
                    if self._cancel_event.is_set():
                        raise
                    image_warning = str(exc)
                    report_progress("AI 插画失败，正在保留内容并生成无插画版日报...")
            if self._cancel_event.is_set():
                raise RuntimeError("任务已取消。")
            if poster is not None:
                report_progress("整图海报已生成，正在保存...")
                rendered_path = save_poster_image(poster, output_path)
            else:
                report_progress("正在本地排版手绘日报...")
                rendered_path = render_newspaper(
                    digest, output_path, topic_images=topic_images
                )

            def show_complete():
                self.msg_count_label.config(text=f"共 {count} 条消息")
                self._set_status(f"图片日报已保存：{rendered_path}")
                self._show_image_preview(rendered_path)
                if image_warning:
                    messagebox.showwarning(
                        "AI 插画未生成",
                        "日报内容已正常保存，但这次的 AI 绘图请求失败了。\n\n"
                        + image_warning,
                    )

            self.root.after(0, show_complete)
        except Exception as exc:
            error = str(exc)

            if self._cancel_event.is_set():
                self.root.after(
                    0,
                    lambda: self.msg_count_label.config(text="图片日报任务已取消"),
                )
                self._set_status("任务已取消。")
                return

            def show_error():
                messagebox.showerror("图片日报生成失败", error)
                self._set_status(f"图片日报生成失败：{error}")

            self.root.after(0, show_error)
        finally:
            self._set_progress(False)
            self._set_ui_enabled(True)

    def _on_redraw(self):
        if not self._last_digest:
            return
        self._remember_provider_settings()
        use_images = self.ai_topic_images_var.get()
        key = str(self.provider_keys.get('gemini') or '').strip()
        if use_images and not key:
            messagebox.showwarning('需要 Gemini Key', '请先填写 Gemini Key。')
            return
        digest = copy.deepcopy(self._last_digest)
        digest['template_id'] = self._template_id()
        path = filedialog.asksaveasfilename(defaultextension='.png',
            filetypes=[('PNG 图片', '*.png')], title='重画上一份日报 · 保存新图片',
            initialfile=f"{_safe_filename_part(digest.get('group_name'))}_重画_{datetime.datetime.now():%Y%m%d_%H%M%S}.png")
        if not path:
            return
        self._cancel_event.clear()
        self._set_ui_enabled(False)
        self._set_progress(True)
        self.btn_cancel.config(state='normal')
        self._set_status('正在重画上一份日报，保留已确认的内容...')
        threading.Thread(target=self._render_digest,
            args=(digest, path, use_images, key, self._current_illustration_mode(),
                  self.image_model_var.get()), daemon=True).start()

    def _review_digest(self, digest):
        """后台等待用户编辑，所有 Tk 操作仍在主线程执行。"""
        done = threading.Event()
        result = []
        def open_editor():
            if self._cancel_event.is_set():
                done.set()
                return
            edited = copy.deepcopy(digest)
            win = tk.Toplevel(self.root)
            win.title('出图前预览 · 编辑日报内容')
            win.geometry('760x700')
            win.transient(self.root)
            ttk.Label(win, text='修改下面的内容，确认后开始生成图片。重画将复用这份内容。',
                      padding=12).pack(fill='x')
            canvas = tk.Canvas(win, highlightthickness=0)
            bar = ttk.Scrollbar(win, orient='vertical', command=canvas.yview)
            canvas.configure(yscrollcommand=bar.set)
            footer = ttk.Frame(win, padding=12)
            footer.pack(side='bottom', fill='x')
            bar.pack(side='right', fill='y')
            canvas.pack(fill='both', expand=True)
            body = ttk.Frame(canvas, padding=12)
            slot = canvas.create_window(0, 0, window=body, anchor='nw')
            body.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
            canvas.bind('<Configure>', lambda e: canvas.itemconfigure(slot, width=e.width))
            fields = []
            labels = {'headline':'头版标题', 'lead':'今日概览', 'title':'标题 / 称号',
                      'name':'昵称', 'reason':'理由', 'summary':'摘要', 'bubble':'气泡台词',
                      'award':'成就名称', 'speaker':'发言人', 'text':'金句', 'points':'要点'}
            def field(container, key, label):
                value = container.get(key, '')
                if not isinstance(value, (str, list)):
                    return
                ttk.Label(body, text=label).pack(anchor='w', pady=(8, 2))
                box = tk.Text(body, height=3 if key in ('lead','reason','summary','points') else 2,
                              wrap='word', font=(self._fonts['body'], 10))
                box.insert('1.0', '\n'.join(value) if isinstance(value, list) else value)
                box.pack(fill='x')
                fields.append((container, key, box, isinstance(value, list)))
            for key in ('headline', 'lead'):
                field(edited, key, labels[key])
            sections = [('topics','核心话题'), ('mvp_rankings','摸鱼大王'),
                        ('achievements','趣味成就'), ('quotes','今日金句')]
            for section, title in sections:
                for index, item in enumerate(edited.get(section, []), 1):
                    if not isinstance(item, dict):
                        continue
                    ttk.Label(body, text=f'{title} {index}', style='CardTitle.TLabel').pack(anchor='w', pady=(16, 0))
                    for key in item:
                        if key in labels:
                            field(item, key, labels[key])
            for key, label in [('tomorrow_topics','明日话题（每行一条）'), ('special_notes','特别关注（每行一条）')]:
                field(edited, key, label)
            cancel_timer = [None]
            def close(confirm=False):
                if cancel_timer[0] is not None:
                    self.root.after_cancel(cancel_timer[0])
                    cancel_timer[0] = None
                if confirm:
                    for container, key, box, is_list in fields:
                        value = box.get('1.0','end').strip()
                        container[key] = [v.strip() for v in value.splitlines() if v.strip()] if is_list else value
                    edited['mvp'] = next(iter(edited.get('mvp_rankings', [])), {})
                    edited['quote'] = next(iter(edited.get('quotes', [])), {})
                    result.append(edited)
                else:
                    self._set_status('已取消出图')
                done.set()
                win.destroy()
            ttk.Button(footer, text='确认并生成图片', command=lambda: close(True),
                       style='Primary.TButton').pack(side='right')
            ttk.Button(footer, text='取消', command=close).pack(side='right', padx=8)
            win.protocol('WM_DELETE_WINDOW', close)
            def check_cancel():
                if done.is_set():
                    return
                if self._cancel_event.is_set():
                    close()
                else:
                    cancel_timer[0] = self.root.after(200, check_cancel)
            check_cancel()
        self._set_status('等待预览确认，确认后才调用图片模型...')
        self.root.after(0, open_editor)
        while not done.wait(0.2):
            if self._cancel_event.is_set():
                return None
        return result[0] if result else None

    def _show_image_preview(self, image_path):
        self._result_image_path = str(image_path)
        self.result_tabs.select(self.image_result)
        self._fit_result_image()
        preview = tk.Toplevel(self.root)
        preview.title("图片日报预览")
        preview.geometry("760x900")
        preview.minsize(560, 700)
        with Image.open(image_path) as opened:
            source = opened.copy()
        resampling = getattr(Image, "Resampling", Image)
        source.thumbnail((700, 1500), resampling.LANCZOS)
        photo = ImageTk.PhotoImage(source)

        viewer = ttk.Frame(preview, padding=(12, 12, 4, 8))
        viewer.pack(fill="both", expand=True)
        canvas = tk.Canvas(viewer, bg=APP_BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(viewer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.create_image(0, 0, image=photo, anchor="nw")
        canvas.image = photo
        canvas.configure(scrollregion=(0, 0, source.width, source.height))
        canvas.bind(
            "<MouseWheel>",
            lambda event: canvas.yview_scroll(int(-event.delta / 120), "units"),
        )
        buttons = ttk.Frame(preview)
        buttons.pack(pady=(0, 12))
        ttk.Button(
            buttons,
            text="打开原图",
            command=lambda: theme_open(image_path),
            width=14,
        ).pack(side="left", padx=6)
        ttk.Button(
            buttons, text="关闭", command=preview.destroy, width=12
        ).pack(side="left", padx=6)

    # ─────────────────────────────────────────────────────────────────────────
    # 复制 / 保存
    # ─────────────────────────────────────────────────────────────────────────

    def _on_copy(self):
        text = self.result_text.get("1.0", "end").strip()
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self._set_status("已复制到剪贴板")

    def _on_save(self):
        text = self.result_text.get("1.0", "end").strip()
        if not text:
            return
        default_name = f"群聊总结_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.txt"
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
            initialfile=default_name,
            title="保存总结"
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            self._set_status(f"已保存：{path}")

    # ─────────────────────────────────────────────────────────────────────────
    # 关闭
    # ─────────────────────────────────────────────────────────────────────────

    def _on_edit_prompt(self):
        """打开提示词编辑窗口"""
        win = tk.Toplevel(self.root)
        win.title("修改提示词")
        win.resizable(True, True)
        win.minsize(560, 440)
        win.grab_set()  # 模态

        # 说明文字
        hint = ("可用占位符（直接写在提示词中）：\n"
                "  {date}       -> 当前日期，例：2026年04月29日\n"
                "  {day_range}  -> 时间范围，例：今天 / 最近7天\n"
                "  {count}      -> 消息条数\n"
                "  {messages}   -> 聊天记录正文（必须包含此项）")
        ttk.Label(win, text=hint, justify="left",
                  foreground="gray").pack(anchor="w", padx=12, pady=(10, 4))

        # 文本编辑框
        txt = scrolledtext.ScrolledText(win, wrap="word",
                                        font=("微软雅黑", 10), height=16)
        txt.pack(fill="both", expand=True, padx=12, pady=4)
        txt.insert("1.0", self._prompt_template)
        txt.focus_set()

        # 按钮行
        btn_row = ttk.Frame(win)
        btn_row.pack(pady=(4, 10))

        def save_prompt():
            new_tpl = txt.get("1.0", "end").rstrip("\n")
            if "{messages}" not in new_tpl:
                messagebox.showwarning("格式错误",
                    "提示词中必须包含 {messages} 占位符，\n否则聊天记录无法传给 AI。",
                    parent=win)
                return
            self._prompt_template = new_tpl
            self._set_status("提示词已更新")
            win.destroy()

        def reset_prompt():
            txt.delete("1.0", "end")
            txt.insert("1.0", DEFAULT_PROMPT_TEMPLATE)

        ttk.Button(btn_row, text="保存", command=save_prompt, width=12).pack(side="left", padx=6)
        ttk.Button(btn_row, text="恢复初始提示词", command=reset_prompt, width=16).pack(side="left", padx=6)
        ttk.Button(btn_row, text="取消", command=win.destroy, width=10).pack(side="left", padx=6)

    def _on_close(self):
        self._remember_provider_settings()
        cfg = self.config.copy()
        cfg.pop("api_key", None)
        cfg["provider"] = self.current_provider
        cfg["api_keys"] = self.provider_keys
        cfg["models"] = self.provider_models
        cfg["ai_topic_images"] = bool(self.ai_topic_images_var.get())
        cfg.pop("detailed_illustrations", None)
        cfg["illustration_mode"] = self._current_illustration_mode()
        cfg["image_model"] = self.image_model_var.get().strip() or GEMINI_IMAGE_MODEL
        cfg["member_profiles"] = self.member_profiles
        cfg["preview_before_image"] = self.preview_before_image_var.get()
        cfg['digest_template'] = self._template_id()
        # 保存自定义提示词（若与默认不同）
        if self._prompt_template != DEFAULT_PROMPT_TEMPLATE:
            cfg["prompt_template"] = self._prompt_template
        else:
            cfg.pop("prompt_template", None)
        save_config(cfg)
        self._cleanup_connections()
        self.root.destroy()


# ─────────────────────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────────────────────

def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    icon_path = resource_path("icon.ico")
    if os.path.isfile(icon_path):
        try:
            root.iconbitmap(icon_path)
        except tk.TclError:
            pass
    app = WeChatSummaryApp(root)

    # 让窗口居中
    root.geometry("1120x900")
    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

    root.mainloop()


if __name__ == "__main__":
    main()
