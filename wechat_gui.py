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
import sys
import sqlite3
import traceback

# 确保能找到 wechat_summary.py（同目录）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from tkcalendar import DateEntry
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "tkcalendar", "-q"], check=False)
    from tkcalendar import DateEntry

from wechat_summary import (
    find_wechat_data_dir,
    find_db_storage,
    extract_keys_from_memory,
    decrypt_db,
    select_decrypt_temp_dir,
    list_chatrooms,
    get_messages_by_range,
    ai_summarize,
    load_config,
    save_config,
    DEFAULT_PROMPT_TEMPLATE,
)

DEFAULT_API_KEY = ""  # 请填入你自己的 DeepSeek API Key


class WeChatSummaryApp:
    def __init__(self, root):
        self.root = root
        self.root.title("微信群聊 AI 总结工具")
        self.root.resizable(True, True)
        self.root.minsize(620, 700)

        # 后端状态
        self.key_map = {}
        self.conn_msg = []
        self.conn_contact = None
        self.tmp_msg_paths = []
        self.tmp_contact_path = None
        self.chatrooms = []   # [(chatroom_id, count, display_name), ...]
        self.config = load_config()
        self._initialized = False
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
        # 是否当前使用内置key（True=内置，False=用户自己的）
        self._using_builtin = not bool(self.config.get("api_key", "").strip())

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ─────────────────────────────────────────────────────────────────────────
    # UI 构建
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        pad = dict(padx=12, pady=6)

        # ── 第一步：初始化 ──
        frame1 = ttk.LabelFrame(self.root, text="第一步：初始化（需要微信已登录）")
        frame1.pack(fill="x", **pad)

        init_row = ttk.Frame(frame1)
        init_row.pack(fill="x", padx=8, pady=(6, 2))

        self.btn_init = ttk.Button(init_row, text="自动初始化",
                                   command=self._on_init_click, width=14)
        self.btn_init.pack(side="left")

        self.btn_manual = ttk.Button(init_row, text="手动选择文件夹",
                                     command=self._on_manual_select, width=16)
        self.btn_manual.pack(side="left", padx=(8, 0))

        self.progress = ttk.Progressbar(init_row, mode="indeterminate", length=160)
        self.progress.pack(side="left", padx=(12, 0))

        # 当前数据路径提示
        path_row = ttk.Frame(frame1)
        path_row.pack(fill="x", padx=8, pady=(0, 6))
        self.path_label = ttk.Label(path_row, text="数据目录：（未选择，将自动检测）",
                                    foreground="gray", wraplength=560, justify="left")
        self.path_label.pack(side="left")

        # ── 第二步：选择群聊 ──
        frame2 = ttk.LabelFrame(self.root, text="第二步：选择群聊")
        frame2.pack(fill="x", **pad)

        chatroom_row = ttk.Frame(frame2)
        chatroom_row.pack(fill="x", padx=8, pady=6)

        ttk.Label(chatroom_row, text="群聊：").pack(side="left")
        self.chatroom_var = tk.StringVar()
        self.chatroom_combo = ttk.Combobox(chatroom_row, textvariable=self.chatroom_var,
                                           state="disabled", width=50)
        self.chatroom_combo.pack(side="left", padx=(4, 0), fill="x", expand=True)

        # ── 第三步：时间范围 ──
        frame3 = ttk.LabelFrame(self.root, text="第三步：选择时间范围")
        frame3.pack(fill="x", **pad)

        date_row = ttk.Frame(frame3)
        date_row.pack(fill="x", padx=8, pady=6)

        today = datetime.date.today()
        week_ago = today - datetime.timedelta(days=6)

        ttk.Label(date_row, text="开始日期：").pack(side="left")
        self.start_date = DateEntry(date_row, locale="zh_CN",
                                    date_pattern="yyyy-mm-dd",
                                    year=week_ago.year,
                                    month=week_ago.month,
                                    day=week_ago.day,
                                    width=12)
        self.start_date.pack(side="left", padx=(4, 16))

        ttk.Label(date_row, text="结束日期：").pack(side="left")
        self.end_date = DateEntry(date_row, locale="zh_CN",
                                  date_pattern="yyyy-mm-dd",
                                  year=today.year,
                                  month=today.month,
                                  day=today.day,
                                  width=12)
        self.end_date.pack(side="left", padx=(4, 0))

        # ── 生成按钮 ──
        btn_row = ttk.Frame(self.root)
        btn_row.pack(fill="x", padx=12, pady=4)
        self.btn_summarize = ttk.Button(btn_row, text="生成总结",
                                        command=self._on_summarize_click,
                                        state="disabled", width=20)
        self.btn_summarize.pack(side="left")

        self.btn_edit_prompt = ttk.Button(btn_row, text="修改提示词",
                                          command=self._on_edit_prompt, width=12)
        self.btn_edit_prompt.pack(side="left", padx=(10, 0))

        # 消息数量提示
        self.msg_count_label = ttk.Label(btn_row, text="", foreground="gray")
        self.msg_count_label.pack(side="left", padx=(12, 0))

        # ── 总结结果 ──
        frame4 = ttk.LabelFrame(self.root, text="总结结果")
        frame4.pack(fill="both", expand=True, **pad)

        self.result_text = scrolledtext.ScrolledText(
            frame4, wrap="word", state="disabled",
            font=("微软雅黑", 10), height=14
        )
        self.result_text.pack(fill="both", expand=True, padx=8, pady=(6, 4))

        btn_result_row = ttk.Frame(frame4)
        btn_result_row.pack(pady=(0, 6))
        self.btn_copy = ttk.Button(btn_result_row, text="复制到剪贴板",
                                   command=self._on_copy, state="disabled", width=16)
        self.btn_copy.pack(side="left", padx=6)
        self.btn_save = ttk.Button(btn_result_row, text="保存为 TXT",
                                   command=self._on_save, state="disabled", width=16)
        self.btn_save.pack(side="left", padx=6)

        # ── API Key ──
        api_frame = ttk.LabelFrame(self.root, text="DeepSeek API Key")
        api_frame.pack(fill="x", **pad)

        api_row = ttk.Frame(api_frame)
        api_row.pack(fill="x", padx=8, pady=6)

        # 内置key时字段预填（掩码显示），用户自己的key直接用
        initial_key = self.config.get("api_key", "").strip() or DEFAULT_API_KEY
        self.api_key_var = tk.StringVar(value=initial_key)
        self.api_entry = ttk.Entry(api_row, textvariable=self.api_key_var,
                                   show="*", width=48)
        self.api_entry.pack(side="left")

        # 内置key时「显示」不可用，防止别人看到key内容
        show_state = "disabled" if self._using_builtin else "normal"
        self.show_key_btn = ttk.Button(api_row, text="显示", width=5,
                                       state=show_state,
                                       command=self._toggle_key_visibility)
        self.show_key_btn.pack(side="left", padx=(4, 0))

        self.key_hint_label = ttk.Label(api_row,
            text="  （内置Key，可清空后粘贴自己的）" if self._using_builtin else "  （自定义Key）",
            foreground="gray")
        self.key_hint_label.pack(side="left")

        # 监听字段变化：用户修改后切换到自定义模式
        self.api_key_var.trace_add("write", self._on_api_key_changed)

        # ── 状态栏 ──
        self.status_var = tk.StringVar(value="就绪，请先点击「初始化」")
        status_bar = ttk.Label(self.root, textvariable=self.status_var,
                               relief="sunken", anchor="w")
        status_bar.pack(fill="x", side="bottom", padx=0, pady=0)

    # ─────────────────────────────────────────────────────────────────────────
    # 工具方法
    # ─────────────────────────────────────────────────────────────────────────

    def _set_status(self, msg, color="black"):
        self.root.after(0, lambda: self.status_var.set(msg))

    def _set_progress(self, running: bool):
        if running:
            self.root.after(0, self.progress.start)
        else:
            self.root.after(0, self.progress.stop)

    def _set_ui_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        def _do():
            self.btn_init.config(state=state)
            self.btn_manual.config(state=state)
            if enabled and self._initialized:
                self.btn_summarize.config(state="normal")
                self.chatroom_combo.config(state="readonly")
        self.root.after(0, _do)

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

    def _on_api_key_changed(self, *args):
        """用户修改了Key字段：切换到自定义模式，启用「显示」按钮"""
        current = self.api_key_var.get()
        if self._using_builtin and current != DEFAULT_API_KEY:
            # 用户开始编辑，脱离内置key模式
            self._using_builtin = False
            self.show_key_btn.config(state="normal")
            self.key_hint_label.config(text="  （自定义Key）")

    def _display_result(self, text: str):
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

    # ─────────────────────────────────────────────────────────────────────────
    # 初始化流程
    # ─────────────────────────────────────────────────────────────────────────

    def _on_init_click(self):
        self._set_ui_enabled(False)
        self._set_progress(True)
        self._set_status("正在初始化...")
        threading.Thread(target=self._init_thread, daemon=True).start()

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
                    "密钥读取失败",
                    "已找到微信数据库，但没有读取到可验证的数据库密钥。\n\n"
                    "请确认微信已登录并保持运行；如果微信刚升级过，"
                    "请完全退出微信、重新打开并登录后再试。"
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

    def _get_contact_name_map(self):
        """返回 {chatroom_id: nick_name}，动态检测列名"""
        if not self.conn_contact:
            return {}
        try:
            cur = self.conn_contact.cursor()
            # 找 contact 表（可能叫 contact 或 Contact）
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cur.fetchall()]
            tbl = next((t for t in tables if t.lower() == "contact"), None)
            if not tbl:
                return {}

            # 动态检测列名
            cur.execute(f"PRAGMA table_info({tbl})")
            cols = [row[1] for row in cur.fetchall()]
            cols_lower = [c.lower() for c in cols]

            # 找 user_name 列（群ID）
            name_col = None
            for candidate in ("user_name", "username", "UsrName", "user_id", "id"):
                if candidate.lower() in cols_lower:
                    name_col = cols[cols_lower.index(candidate.lower())]
                    break
            if not name_col:
                # 取第一列
                name_col = cols[0] if cols else None

            # 找 nick_name 列（昵称）
            nick_col = None
            for candidate in ("nick_name", "nickname", "NickName", "remark", "Remark", "alias", "name"):
                if candidate.lower() in cols_lower:
                    nick_col = cols[cols_lower.index(candidate.lower())]
                    break

            if not name_col or not nick_col:
                return {}

            cur.execute(
                f"SELECT {name_col}, {nick_col} FROM {tbl} "
                f"WHERE {name_col} LIKE '%@chatroom'"
            )
            return {row[0]: row[1] for row in cur.fetchall() if row[1]}
        except Exception:
            return {}

    def _load_chatrooms(self):
        rooms = list_chatrooms(self.conn_msg)
        name_map = self._get_contact_name_map()
        self.chatrooms = []
        for cr_id, count in rooms:
            nick = name_map.get(cr_id, cr_id.replace("@chatroom", ""))
            display = f"{nick}  （{count} 条消息）"
            self.chatrooms.append((cr_id, count, display))

    def _populate_chatroom_combo(self):
        values = [c[2] for c in self.chatrooms]
        self.chatroom_combo["values"] = values
        self.chatroom_combo.config(state="readonly")
        if values:
            self.chatroom_combo.current(0)
        self.btn_summarize.config(state="normal")

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

        self._set_ui_enabled(False)
        self._set_progress(True)
        self._display_result("")
        self.msg_count_label.config(text="")
        self._set_status("正在读取消息...")
        threading.Thread(target=self._summarize_thread,
                         args=(idx, start_d, end_d), daemon=True).start()

    def _summarize_thread(self, idx, start_d, end_d):
        try:
            # 找选中的群
            if idx < 0 or idx >= len(self.chatrooms):
                self._set_status("请选择一个群聊")
                return
            chatroom_id, count, display = self.chatrooms[idx]

            # 转时间戳（已在主线程获取日期）
            start_ts = int(datetime.datetime.combine(start_d, datetime.time.min).timestamp())
            end_ts = int(datetime.datetime.combine(end_d, datetime.time.max).timestamp())

            # 读消息
            messages = get_messages_by_range(self.conn_msg, chatroom_id, start_ts, end_ts)
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
            api_key = self.api_key_var.get().strip() or DEFAULT_API_KEY
            days_approx = (end_d - start_d).days + 1
            group_name = display.split("（")[0].strip()
            summary = ai_summarize(messages, api_key,
                                   group_id=chatroom_id, days=days_approx,
                                   prompt_template=self._prompt_template,
                                   progress_callback=self._set_status)

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
        cfg = self.config.copy()
        if self._using_builtin:
            # 用的是内置key，不保存到config（别人拿到config.json也看不到）
            cfg.pop("api_key", None)
        else:
            key = self.api_key_var.get().strip()
            if key:
                cfg["api_key"] = key
            else:
                cfg.pop("api_key", None)
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
    root = tk.Tk()
    app = WeChatSummaryApp(root)

    # 让窗口居中
    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

    root.mainloop()


if __name__ == "__main__":
    main()
