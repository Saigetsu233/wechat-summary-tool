# -*- coding: utf-8 -*-
"""
微信群聊 AI 总结工具 - 适用于微信 4.x (Windows / macOS)

使用前：
1. 打开微信客户端并登录
2. 双击运行此工具

依赖：pip install pycryptodome requests psutil
"""

import os
import sys
import ctypes
import hashlib
import hmac as hmac_mod
import struct
import re
import shutil
import tempfile
import sqlite3
import datetime
import json
import requests
import subprocess
import time
from urllib.parse import quote

import platform_support
from platform_support import IS_WINDOWS, IS_MACOS

# ctypes.wintypes 只有 Windows 才有，非 Windows 导入会直接失败。
if IS_WINDOWS:
    import ctypes.wintypes as wt

# ─────────────────────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────────────────────
PAGE_SZ = 4096
SALT_SZ = 16
RESERVE_SZ = 80
KEY_SZ = 32
SQLITE_HDR = b'SQLite format 3\x00'
MEM_COMMIT = 0x1000
READABLE = {0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80}

# 微信 4.1.10+ 不再长期保留旧版的明文 raw key，而是把 WCDB
# Config.Cipher 配置块放在进程内存中。该配置块使用固定掩码做异或处理。
CONFIG_CIPHER_NAME = b"com.Tencent.WCDB.Config.Cipher"
CONFIG_CIPHER_XOR_MASK = bytes.fromhex(
    "d2c7442458020000004889442450488b"
    "450048844c2448488944254048584c24"
)
CONFIG_CIPHER_MAX_BLOB = 1024
MAX_USER_ADDRESS = 0x0000800000000000

SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
IS_FROZEN = platform_support.IS_FROZEN
APP_DATA_DIR = platform_support.app_data_dir()
CONFIG_FILE = os.path.join(APP_DATA_DIR, "config.json")
DEFAULT_PROVIDER = "gemini"
PROVIDERS = {
    "gemini": {
        "label": "Google Gemini",
        "endpoint": (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "{model}:generateContent"
        ),
        "default_model": "gemini-3.8-flash",
        "models": [
            "gemini-3.8-flash",
            "gemini-3.8-pro",
            "gemini-3-pro",
            "gemini-2.5-flash",
            "gemini-2.5-pro",
        ],
    },
    "deepseek": {
        "label": "DeepSeek 官方",
        "endpoint": "https://api.deepseek.com/v1/chat/completions",
        "default_model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "openai": {
        "label": "OpenAI",
        "endpoint": "https://api.openai.com/v1/chat/completions",
        "default_model": "gpt-4o-mini",
        "models": [
            "gpt-4o-mini",
            "gpt-4o",
            "gpt-4.1-mini",
            "gpt-4.1",
            "o4-mini",
        ],
    },
    "openrouter": {
        "label": "OpenRouter（聚合各大模型）",
        "endpoint": "https://openrouter.ai/api/v1/chat/completions",
        "default_model": "openai/gpt-4o-mini",
        "models": [
            "openai/gpt-4o-mini",
            "openai/gpt-4o",
            "anthropic/claude-3.5-sonnet",
            "anthropic/claude-sonnet-4",
            "google/gemini-2.5-flash",
            "deepseek/deepseek-chat",
            "qwen/qwen-2.5-72b-instruct",
            "meta-llama/llama-3.3-70b-instruct",
        ],
    },
    "nvidia": {
        "label": "NVIDIA API Catalog",
        "endpoint": "https://integrate.api.nvidia.com/v1/chat/completions",
        "default_model": "deepseek-ai/deepseek-v4-flash-0731",
        "models": [
            "deepseek-ai/deepseek-v4-flash-0731",
            "deepseek-ai/deepseek-r1",
            "qwen/qwen2.5-72b-instruct",
            "meta/llama-3.3-70b-instruct",
        ],
    },
}
DEEPSEEK_REQUEST_TIMEOUT = (15, 90)
NVIDIA_REQUEST_TIMEOUT = (20, 120)
GEMINI_REQUEST_TIMEOUT = (20, 180)
GEMINI_MAX_ATTEMPTS = 3

# ─────────────────────────────────────────────────────────────────────────────
# 1. 查找微信数据目录
# ─────────────────────────────────────────────────────────────────────────────

def _find_user_dir_from_process():
    """方法一：直接查询 Weixin.exe 进程的打开文件，定位 message_0.db。

    无论微信数据装在哪个盘、哪个目录，只要微信在运行就能找到。
    返回 user_dir（含 db_storage 的那一层），找不到返回 None。
    """
    try:
        import psutil
        for proc in psutil.process_iter(["name", "pid"]):
            if not proc.info["name"]:
                continue
            if proc.info["name"].lower() != "weixin.exe":
                continue
            try:
                for f in proc.open_files():
                    p = f.path.replace("\\", "/")
                    if p.lower().endswith("/db_storage/message/message_0.db"):
                        # p = .../wxid_xxx/db_storage/message/message_0.db
                        # 往上三层就是 user_dir
                        user_dir = os.path.dirname(os.path.dirname(os.path.dirname(p)))
                        if os.path.isdir(user_dir):
                            return user_dir
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                continue
    except Exception:
        pass
    return None


def find_wechat_data_dir():
    """找微信数据目录，返回 (root_dir, user_dir)。

    优先用进程文件句柄直接定位（无视存储位置），
    失败时退化为多路径扫描。
    多账号时取 message_0.db 最近修改的（当前登录账号）。
    """
    if IS_MACOS:
        import wechat_macos
        return wechat_macos.find_wechat_data_dir()
    # ── 方法一：从进程打开文件直接找（最可靠，适用任意路径）──
    user_dir = _find_user_dir_from_process()
    if user_dir:
        return os.path.dirname(user_dir), user_dir
    # ── 方法二：多路径扫描（兜底，覆盖微信未登录时等场景）──
    # 获取所有可用盘符（用 Win32 API，兼容所有 Windows 版本，包括 Windows 11）
    drives = []
    try:
        import ctypes
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        drives = [chr(65 + i) + ":" for i in range(26) if bitmask & (1 << i)]
    except Exception:
        drives = ["C:", "D:", "E:", "F:"]

    def drive_root(d):
        """返回盘根路径，如 'C:' → 'C:/'，确保路径正确"""
        return d + "/"

    # 构建候选根目录列表（xwechat_files 所在的父目录）
    candidate_roots = []

    # 1. 用 USERPROFILE 环境变量获取当前用户目录（最可靠，任意电脑通用）
    userprofile = os.environ.get("USERPROFILE", "")
    if userprofile:
        candidate_roots += [
            os.path.join(userprofile, "Documents", "xwechat_files"),
            os.path.join(userprofile, "Documents", "WeChat Files"),
            os.path.join(userprofile, "xwechat_files"),
        ]

    # 2. 搜索所有盘下所有 Windows 用户目录（覆盖换电脑/多用户/非C盘场景）
    for drive in drives:
        dr = drive_root(drive)
        users_dir = dr + "Users"
        if os.path.isdir(users_dir):
            try:
                for uname in os.listdir(users_dir):
                    if uname in ("Public", "Default", "Default User", "All Users",
                                 "AppData", "desktop.ini"):
                        continue
                    u_path = os.path.join(users_dir, uname)
                    if not os.path.isdir(u_path):
                        continue
                    candidate_roots += [
                        os.path.join(u_path, "Documents", "xwechat_files"),
                        os.path.join(u_path, "Documents", "WeChat Files"),
                        os.path.join(u_path, "xwechat_files"),
                        # AppData\Roaming\Tencent\xwechat（部分安装方式）
                        os.path.join(u_path, "AppData", "Roaming", "Tencent", "xwechat"),
                    ]
            except Exception:
                pass
        # 盘根目录的常见自定义路径
        candidate_roots += [
            dr + "xwechat_files",
            dr + "WeChat Files",
            dr + "微信文件",
        ]
        # Program Files 下的 wx 子目录（微信自定义安装位置，如 Program Files\wx\liaotianjilu\xwechat_files）
        for pf in ("Program Files", "Program Files (x86)"):
            wx_base = os.path.join(dr + pf, "wx")
            if os.path.isdir(wx_base):
                try:
                    for sub in os.listdir(wx_base):
                        subpath = os.path.join(wx_base, sub)
                        if os.path.isdir(subpath):
                            candidate_roots.append(os.path.join(subpath, "xwechat_files"))
                            candidate_roots.append(os.path.join(subpath, "WeChat Files"))
                except Exception:
                    pass

    # 3. 从注册表找用户自定义存储路径（优先级最高，插到最前）
    try:
        import winreg
        for reg_path in [r"Software\Tencent\WeChat", r"Software\Tencent\xwechat"]:
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path)
                for val_name in ["FileSavePath", "InstallPath"]:
                    try:
                        path, _ = winreg.QueryValueEx(key, val_name)
                        if path and path != "MyDocument:":
                            candidate_roots.insert(0, os.path.join(path, "xwechat_files"))
                            candidate_roots.insert(0, path)
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception:
        pass

    # 4. 收集所有找到的有效账号目录，记录 message_0.db 的修改时间
    valid_candidates = []  # [(mtime, base_path, user_subpath), ...]
    seen_db = set()

    for base_path in candidate_roots:
        if not base_path or not os.path.isdir(base_path):
            continue
        try:
            for sub in os.listdir(base_path):
                if sub in ("all_users", "Backup") or sub.startswith("."):
                    continue
                subpath = os.path.join(base_path, sub)
                if not os.path.isdir(subpath):
                    continue
                db_path = os.path.join(subpath, "db_storage", "message", "message_0.db")
                if os.path.isfile(db_path) and db_path not in seen_db:
                    seen_db.add(db_path)
                    mtime = os.path.getmtime(db_path)
                    valid_candidates.append((mtime, base_path, subpath))
            # 无 wxid 子目录的情况（数据直接在 base_path 下）
            db_path = os.path.join(base_path, "db_storage", "message", "message_0.db")
            if os.path.isfile(db_path) and db_path not in seen_db:
                seen_db.add(db_path)
                mtime = os.path.getmtime(db_path)
                valid_candidates.append((mtime, os.path.dirname(base_path), base_path))
        except Exception:
            continue

    if not valid_candidates:
        return None, None

    # 5. 按修改时间降序排列，取最近活跃的账号（当前登录的微信）
    valid_candidates.sort(key=lambda x: x[0], reverse=True)
    _, best_base, best_user = valid_candidates[0]
    return best_base, best_user


def find_db_storage(user_dir):
    """找 db_storage 目录"""
    return os.path.join(user_dir, "db_storage")


# ─────────────────────────────────────────────────────────────────────────────
# 2. 从内存提取密钥（微信 4.x）
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# 进程内存读取：抽象成 reader 接口，两个平台各给一个后端。
# 密钥解析与校验（Config.Cipher、HMAC）是平台无关的纯逻辑，两端共用。
# ─────────────────────────────────────────────────────────────────────────────

class MemoryAccessError(RuntimeError):
    """无法打开或读取目标进程内存（权限不足、进程消失等）。"""


if IS_WINDOWS:

    class MBI(ctypes.Structure):
        _fields_ = [
            ("BaseAddress", ctypes.c_uint64), ("AllocationBase", ctypes.c_uint64),
            ("AllocationProtect", wt.DWORD), ("_pad1", wt.DWORD),
            ("RegionSize", ctypes.c_uint64), ("State", wt.DWORD),
            ("Protect", wt.DWORD), ("Type", wt.DWORD), ("_pad2", wt.DWORD),
        ]

    class _WindowsProcessReader:
        """用 kernel32 读取 Weixin.exe 进程内存。"""

        def __init__(self, pid):
            self.kernel32 = ctypes.windll.kernel32
            self.handle = self.kernel32.OpenProcess(0x0010 | 0x0400, False, pid)
            if not self.handle:
                raise MemoryAccessError(f"无法打开进程 {pid}（权限不足？）")

        def regions(self):
            regions = []
            address = 0
            mbi = MBI()
            while address < 0x7FFFFFFFFFFF:
                queried = self.kernel32.VirtualQueryEx(
                    self.handle, ctypes.c_uint64(address),
                    ctypes.byref(mbi), ctypes.sizeof(mbi),
                )
                if queried == 0:
                    break
                if (
                    mbi.State == MEM_COMMIT
                    and mbi.Protect in READABLE
                    and 0 < mbi.RegionSize < 500 * 1024 * 1024
                ):
                    regions.append((mbi.BaseAddress, mbi.RegionSize))
                next_address = mbi.BaseAddress + mbi.RegionSize
                if next_address <= address:
                    break
                address = next_address
            return regions

        def read(self, address, size):
            if size <= 0:
                return b""
            try:
                buf = ctypes.create_string_buffer(size)
            except (MemoryError, OverflowError):
                return None
            nread = ctypes.c_size_t(0)
            ok = self.kernel32.ReadProcessMemory(
                self.handle, ctypes.c_uint64(address), buf, size,
                ctypes.byref(nread),
            )
            if not ok and nread.value == 0:
                return None
            return buf.raw[:nread.value]

        def close(self):
            if self.handle:
                self.kernel32.CloseHandle(self.handle)
                self.handle = None


def _iter_reader_chunks(reader, regions, chunk_size=2 * 1024 * 1024, overlap=0):
    """分块读取内存，避免为较大的内存区域一次性分配巨型缓冲区。"""
    for base, region_size in regions:
        offset = 0
        tail = b""
        while offset < region_size:
            current_size = min(chunk_size, region_size - offset)
            chunk = reader.read(base + offset, current_size) or b""
            data = tail + chunk
            data_base = base + offset - len(tail)
            if data:
                yield data_base, data
            tail = data[-overlap:] if overlap and data else b""
            offset += current_size


def verify_enc_key(enc_key, page1):
    """用 HMAC-SHA512 验证 enc_key 是否正确"""
    salt = page1[:SALT_SZ]
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SZ)
    hmac_data = page1[SALT_SZ: PAGE_SZ - RESERVE_SZ + 16]
    stored_hmac = page1[PAGE_SZ - 64: PAGE_SZ]
    hm = hmac_mod.new(mac_key, hmac_data, hashlib.sha512)
    hm.update(struct.pack("<I", 1))
    return hm.digest() == stored_hmac


def _unpack_u64(data, offset):
    if offset < 0 or offset + 8 > len(data):
        return 0
    return struct.unpack_from("<Q", data, offset)[0]


def _config_cipher_candidates(blob):
    """解码 Config.Cipher 配置块，返回 (key_hex, salt_hex|None)。"""
    if not blob or len(blob) > CONFIG_CIPHER_MAX_BLOB:
        return []
    decoded = bytes(
        value ^ CONFIG_CIPHER_XOR_MASK[i % len(CONFIG_CIPHER_XOR_MASK)]
        for i, value in enumerate(blob)
    )
    literal_re = re.compile(rb"[xX]'([0-9a-fA-F]{64,192})'")
    candidates = []
    seen = set()
    for match in literal_re.finditer(decoded):
        hex_run = match.group(1).decode("ascii").lower()
        starts = [0]
        if len(hex_run) > 96:
            starts.extend(range(0, len(hex_run) - 63, 32))
            starts.append(len(hex_run) - 64)
        for start in dict.fromkeys(starts):
            if start + 64 > len(hex_run):
                continue
            key_hex = hex_run[start:start + 64]
            try:
                key_bytes = bytes.fromhex(key_hex)
            except ValueError:
                continue
            if len(set(key_bytes)) < 15 or key_bytes in (b"\x00" * 32, b"\xff" * 32):
                continue
            salt_hex = (
                hex_run[start + 64:start + 96]
                if start + 96 <= len(hex_run)
                else None
            )
            candidate = (key_hex, salt_hex)
            if candidate not in seen:
                seen.add(candidate)
                candidates.append(candidate)
    return candidates


def _match_key_candidate(key_hex, embedded_salt, db_files,
                         key_map, remaining_salts):
    """用数据库首页 HMAC 校验候选，并把通过的密钥写入 key_map。"""
    try:
        key_bytes = bytes.fromhex(key_hex)
    except ValueError:
        return 0

    if embedded_salt and embedded_salt in remaining_salts:
        target_salts = [embedded_salt]
    else:
        target_salts = list(remaining_salts)

    found = 0
    for target_salt in target_salts:
        for rel, _path, _size, salt_hex, page1 in db_files:
            if salt_hex != target_salt:
                continue
            if verify_enc_key(key_bytes, page1):
                key_map[salt_hex] = key_hex
                remaining_salts.discard(salt_hex)
                found += 1
                print(f"  [OK] 找到密钥：{rel}")
                break
    return found


def _scan_config_cipher_reader(reader, db_files, key_map, remaining_salts):
    """微信 4.1.10+：只读扫描 WCDB Config.Cipher 对象。"""
    stats = {"needles": 0, "nodes": 0, "candidates": 0, "verified": 0}
    regions = reader.regions()

    needle_addresses = set()
    for data_base, data in _iter_reader_chunks(
        reader, regions, overlap=len(CONFIG_CIPHER_NAME) - 1
    ):
        pos = data.find(CONFIG_CIPHER_NAME)
        while pos >= 0:
            needle_addresses.add(data_base + pos)
            pos = data.find(CONFIG_CIPHER_NAME, pos + 1)

    stats["needles"] = len(needle_addresses)
    if not needle_addresses:
        return stats

    pointer_patterns = [
        struct.pack("<Q", address) + struct.pack("<Q", len(CONFIG_CIPHER_NAME))
        for address in needle_addresses
    ]
    seen_nodes = set()
    seen_candidates = set()

    for data_base, data in _iter_reader_chunks(reader, regions, overlap=0x80):
        if not remaining_salts:
            break
        for pattern in pointer_patterns:
            pos = data.find(pattern)
            while pos >= 0:
                node_base = data_base + pos - 0x10
                if node_base in seen_nodes:
                    pos = data.find(pattern, pos + 1)
                    continue
                seen_nodes.add(node_base)

                node = reader.read(node_base, 0x50)
                if not node or len(node) < 0x40:
                    pos = data.find(pattern, pos + 1)
                    continue
                if (
                    _unpack_u64(node, 0x10) not in needle_addresses
                    or _unpack_u64(node, 0x18) != len(CONFIG_CIPHER_NAME)
                ):
                    pos = data.find(pattern, pos + 1)
                    continue

                config_pointer = _unpack_u64(node, 0x28)
                if not 0x10000 <= config_pointer < MAX_USER_ADDRESS:
                    pos = data.find(pattern, pos + 1)
                    continue
                stats["nodes"] += 1

                config_object = reader.read(config_pointer + 0x88, 0x28)
                if not config_object or len(config_object) < 0x18:
                    pos = data.find(pattern, pos + 1)
                    continue
                blob_pointer = _unpack_u64(config_object, 0x08)
                blob_size = _unpack_u64(config_object, 0x10)
                if not (
                    0 < blob_size <= CONFIG_CIPHER_MAX_BLOB
                    and 0x10000 <= blob_pointer < MAX_USER_ADDRESS
                ):
                    pos = data.find(pattern, pos + 1)
                    continue

                blob = reader.read(blob_pointer, int(blob_size))
                for key_hex, salt_hex in _config_cipher_candidates(blob):
                    candidate = (key_hex, salt_hex)
                    if candidate in seen_candidates:
                        continue
                    seen_candidates.add(candidate)
                    stats["candidates"] += 1
                    stats["verified"] += _match_key_candidate(
                        key_hex, salt_hex, db_files, key_map, remaining_salts,
                    )
                pos = data.find(pattern, pos + 1)
    return stats


def _scan_legacy_key_reader(reader, db_files, key_map, remaining_salts):
    """微信 4.0.x 兼容路径：扫描明文 x'<key><salt>'。"""
    hex_re = re.compile(rb"x'([0-9a-fA-F]{64,192})'")
    seen = set()
    found = 0
    for _base, data in _iter_reader_chunks(reader, reader.regions(), overlap=256):
        if not remaining_salts:
            break
        for match in hex_re.finditer(data):
            hex_run = match.group(1).decode("ascii").lower()
            key_hex = hex_run[:64]
            salt_hex = hex_run[64:96] if len(hex_run) >= 96 else None
            candidate = (key_hex, salt_hex)
            if candidate in seen:
                continue
            seen.add(candidate)
            found += _match_key_candidate(
                key_hex, salt_hex, db_files, key_map, remaining_salts
            )
    return found


def get_weixin_pids():
    """返回微信进程 PID，按内存占用降序（占用最大的通常是主进程）。"""
    if IS_MACOS:
        import wechat_macos
        return wechat_macos.list_wechat_pids()
    r = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True
    )
    pids = []
    for line in r.stdout.strip().split('\n'):
        if not line.strip():
            continue
        parts = line.strip('"').split('","')
        if len(parts) >= 5:
            try:
                pid = int(parts[1])
                mem = int(parts[4].replace(',', '').replace(' K', '').strip() or '0')
                pids.append((pid, mem))
            except ValueError:
                pass
    pids.sort(key=lambda x: x[1], reverse=True)
    return pids


def _open_process_reader(pid):
    """按平台打开进程内存 reader；失败返回 None。"""
    try:
        if IS_MACOS:
            import wechat_macos
            return wechat_macos.MacProcessReader(pid)
        if IS_WINDOWS:
            return _WindowsProcessReader(pid)
    except MemoryAccessError:
        return None
    raise RuntimeError(f"不支持在 {platform_support.platform_label()} 上读取进程内存。")


def extract_keys_from_memory(db_storage_dir):
    """从微信进程内存中自动提取数据库密钥，兼容微信 4.0/4.1。"""
    # 收集所有 .db 文件及其 salt
    db_files = []
    salt_to_dbs = {}
    for root, dirs, files in os.walk(db_storage_dir):
        for name in files:
            if not name.endswith(".db"):
                continue
            path = os.path.join(root, name)
            size = os.path.getsize(path)
            if size < PAGE_SZ:
                continue
            with open(path, "rb") as f:
                page1 = f.read(PAGE_SZ)
            # 跳过未加密的
            if page1[:16] == SQLITE_HDR:
                continue
            rel = os.path.relpath(path, db_storage_dir)
            salt = page1[:SALT_SZ].hex()
            db_files.append((rel, path, size, salt, page1))
            salt_to_dbs.setdefault(salt, []).append(rel)

    if not db_files:
        return {}, [], {}

    print(f"  找到 {len(db_files)} 个加密数据库")

    pids = get_weixin_pids()
    if not pids:
        raise RuntimeError("未找到微信进程，请先打开微信并登录！")

    key_map = {}  # salt_hex -> enc_key_hex
    remaining_salts = set(salt_to_dbs.keys())

    print("  尝试微信 4.1 Config.Cipher 只读扫描...")
    for pid, mem_kb in pids:
        if not remaining_salts:
            break
        reader = _open_process_reader(pid)
        if reader is None:
            print(f"  PID={pid}：无法打开进程内存（权限不足？）")
            continue
        try:
            stats = _scan_config_cipher_reader(
                reader, db_files, key_map, remaining_salts
            )
        finally:
            reader.close()
        print(
            f"  PID={pid} ({mem_kb//1024}MB)："
            f"配置标记 {stats['needles']}，候选 {stats['candidates']}，"
            f"验证通过 {stats['verified']}"
        )

    # 旧版微信仍使用明文配置；只在新路径未找全时扫描，避免无谓开销。
    if remaining_salts:
        print("  Config.Cipher 未找全，尝试微信 4.0 兼容扫描...")
        for pid, mem_kb in pids:
            if not remaining_salts:
                break
            reader = _open_process_reader(pid)
            if reader is None:
                continue
            try:
                found = _scan_legacy_key_reader(
                    reader, db_files, key_map, remaining_salts
                )
            finally:
                reader.close()
            print(f"  PID={pid}：旧版扫描验证通过 {found}")

    return key_map, db_files, salt_to_dbs


# ─────────────────────────────────────────────────────────────────────────────
# 3. 解密数据库
# ─────────────────────────────────────────────────────────────────────────────

def select_decrypt_temp_dir(required_bytes):
    """选择可容纳解密副本的临时目录，系统盘不足时自动改用程序所在盘。"""
    safety_margin = 128 * 1024 * 1024
    system_temp = tempfile.gettempdir()
    project_temp = os.path.join(APP_DATA_DIR, ".wechat_summary_tmp")
    candidates = [system_temp, project_temp]
    checked = []
    for candidate in dict.fromkeys(candidates):
        try:
            os.makedirs(candidate, exist_ok=True)
            free_bytes = shutil.disk_usage(candidate).free
            checked.append((candidate, free_bytes))
            if free_bytes >= required_bytes + safety_margin:
                return candidate
        except OSError:
            continue
    free_text = "，".join(
        f"{path} 可用 {free / 1024 ** 3:.1f}GB" for path, free in checked
    ) or "没有可用临时目录"
    raise RuntimeError(
        f"临时空间不足，需要约 {required_bytes / 1024 ** 3:.1f}GB；{free_text}"
    )


DECRYPT_PREFIX = "wechat_summary_"


def _decrypted_dest_path(db_path, temp_dir):
    """按源文件的绝对路径算出固定的解密目标名，保证复用、不堆积。"""
    tag = hashlib.md5(os.path.abspath(db_path).encode("utf-8")).hexdigest()
    return os.path.join(temp_dir, f"{DECRYPT_PREFIX}{tag}.db")


def _decrypted_copy_is_fresh(dest_path, source_path):
    """已解密副本仍然可用：大小一致且不早于源文件（源有新消息则失效）。"""
    try:
        return (
            os.path.exists(dest_path)
            and os.path.getsize(dest_path) == os.path.getsize(source_path)
            and os.path.getmtime(dest_path) >= os.path.getmtime(source_path)
        )
    except OSError:
        return False


def sweep_decrypt_temp_dir(temp_dir, keep_paths=()):
    """清理解密临时目录里除 keep_paths 外的所有 wechat_summary_*.db。

    用来回收旧版本的随机命名副本、上次崩溃残留、以及切换账号后的旧副本，
    让临时目录始终只留当前这一套，占用固定不增长。
    """
    keep = {os.path.abspath(p) for p in keep_paths if p}
    try:
        entries = os.listdir(temp_dir)
    except OSError:
        return
    for name in entries:
        if not (name.startswith(DECRYPT_PREFIX) and name.endswith(".db")):
            continue
        path = os.path.abspath(os.path.join(temp_dir, name))
        if path in keep:
            continue
        try:
            os.remove(path)
        except OSError:
            pass


def decrypt_db(db_path, enc_key_hex, temp_dir=None, reuse=True):
    """流式解密微信4.x数据库，返回解密后文件路径。

    使用由源路径决定的固定文件名：源没变化时直接复用上次的解密副本
    （不重复解密、不新建文件）；源有更新则原子覆盖同一个文件。
    """
    from Crypto.Cipher import AES

    enc_key = bytes.fromhex(enc_key_hex)
    if temp_dir is None:
        temp_dir = select_decrypt_temp_dir(os.path.getsize(db_path))
    os.makedirs(temp_dir, exist_ok=True)
    dest_path = _decrypted_dest_path(db_path, temp_dir)
    if reuse and _decrypted_copy_is_fresh(dest_path, db_path):
        return dest_path
    # 先写到 .part 再原子替换，避免中途失败留下半个损坏的副本。
    tmp_path = dest_path + ".part"
    try:
        with open(db_path, "rb") as source, open(tmp_path, "wb") as target:
            first_page = source.read(PAGE_SZ)
            if first_page[:16] == SQLITE_HDR:
                # 已是明文，直接复制。
                target.write(first_page)
                while True:
                    chunk = source.read(4 * 1024 * 1024)
                    if not chunk:
                        break
                    target.write(chunk)
            else:
                source.seek(0)
                page_number = 1
                while True:
                    page = source.read(PAGE_SZ)
                    if not page:
                        break
                    if len(page) != PAGE_SZ:
                        raise RuntimeError(
                            f"数据库文件尾部不完整：第 {page_number} 页只有 {len(page)} 字节"
                        )

                    iv = page[PAGE_SZ - RESERVE_SZ: PAGE_SZ - RESERVE_SZ + 16]
                    if page_number == 1:
                        encrypted = page[SALT_SZ: PAGE_SZ - RESERVE_SZ]
                        decrypted = AES.new(enc_key, AES.MODE_CBC, iv).decrypt(encrypted)
                        out_page = SQLITE_HDR + decrypted + b'\x00' * RESERVE_SZ
                    else:
                        encrypted = page[:PAGE_SZ - RESERVE_SZ]
                        decrypted = AES.new(enc_key, AES.MODE_CBC, iv).decrypt(encrypted)
                        out_page = decrypted + b'\x00' * RESERVE_SZ
                    target.write(out_page[:PAGE_SZ])
                    page_number += 1
        # 文件已关闭，再原子替换（Windows 不能替换仍打开的文件）。
        os.replace(tmp_path, dest_path)
        return dest_path
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ─────────────────────────────────────────────────────────────────────────────
# 4. 读取群聊消息
# ─────────────────────────────────────────────────────────────────────────────

def get_chatroom_table(chatroom_id):
    """根据群聊ID计算消息表名"""
    if not chatroom_id.endswith("@chatroom"):
        chatroom_id += "@chatroom"
    return "Msg_" + hashlib.md5(chatroom_id.encode()).hexdigest()


def conversation_table(username):
    """按会话 user_name 计算消息表名；群聊与私聊通用，不改写 user_name。

    调用方需传入 Name2Id 里的完整 user_name（群聊自带 @chatroom 后缀，
    私聊是原始 wxid），因此不会与 get_chatroom_table 的补后缀行为冲突。
    """
    return "Msg_" + hashlib.md5(str(username).encode()).hexdigest()


def build_scope_labels(days, chat_kind="group"):
    """根据天数与会话类型生成日报上的措辞。

    chat_kind: "group" 群聊 / "private" 私聊或单聊。
    days<=1 用“今日”，多日用“近 N 日”，避免多日日报仍写“今日”。
    """
    noun = "群聊" if chat_kind == "group" else "聊天"
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 1
    if days <= 1:
        count_label = f"今日{noun}总结"
    else:
        count_label = f"近 {days} 日{noun}总结"
    return {
        "noun": noun,
        "count_label": count_label,
        "title_label": f"{noun}日报",
        "overview_label": f"{noun}概览",
    }


def _message_connections(conn_msg):
    """把单连接或多连接统一为连接列表，兼容旧调用。"""
    if conn_msg is None:
        return []
    if isinstance(conn_msg, (list, tuple)):
        return [conn for conn in conn_msg if conn is not None]
    return [conn_msg]


def list_chatrooms(conn_msg, conn_session=None):
    """跨所有 message_N.db 列出群聊，并合计各分库的消息数。"""
    counts = {}
    for conn in _message_connections(conn_msg):
        cur = conn.cursor()
        try:
            cur.execute("SELECT user_name FROM Name2Id WHERE user_name LIKE '%@chatroom'")
            chatrooms = [row[0] for row in cur.fetchall()]
        except sqlite3.Error:
            continue

        for chatroom_id in chatrooms:
            table = get_chatroom_table(chatroom_id)
            try:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                count = cur.fetchone()[0]
            except sqlite3.Error:
                continue
            counts[chatroom_id] = counts.get(chatroom_id, 0) + count

    return sorted(counts.items(), key=lambda item: item[1], reverse=True)


# 私聊列表里要排除的系统/服务账号（非真人对话）。
_PRIVATE_CHAT_EXCLUDE = {
    "filehelper", "weixin", "fmessage", "medianote", "floatbottle",
    "newsapp", "qqmail", "tmessage", "qmessage", "notifymessage",
    "notification_messages", "helper_entry", "brandsessionholder",
}


def list_private_chats(conn_msg):
    """列出私聊/单聊会话（非 @chatroom），跨分库合计消息数。

    过滤掉公众号（gh_ 前缀）和常见系统账号；返回 [(username, count), ...]。
    """
    counts = {}
    for conn in _message_connections(conn_msg):
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT user_name FROM Name2Id "
                "WHERE user_name NOT LIKE '%@chatroom' "
                "AND user_name NOT LIKE '%@openim'"
            )
            names = [row[0] for row in cur.fetchall()]
        except sqlite3.Error:
            continue
        for username in names:
            if not username or username in _PRIVATE_CHAT_EXCLUDE:
                continue
            if username.startswith("gh_"):  # 公众号
                continue
            table = conversation_table(username)
            try:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                count = cur.fetchone()[0]
            except sqlite3.Error:
                continue
            if count:
                counts[username] = counts.get(username, 0) + count
    return sorted(counts.items(), key=lambda item: item[1], reverse=True)


def _decode_db_text(value):
    """把数据库中的文本/字节统一成字符串，解码失败时返回空串。"""
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return ""
    return str(value)


def _quote_sql_identifier(identifier):
    """引用由 SQLite 元数据返回的表名或列名。"""
    return '"' + str(identifier).replace('"', '""') + '"'


def load_contact_name_map(conn_contact):
    """读取联系人显示名，返回 {微信内部 username: 备注名/昵称/别名}。"""
    if conn_contact is None:
        return {}

    try:
        cur = conn_contact.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cur.fetchall()]
        table = next((name for name in tables if name.lower() == "contact"), None)
        if not table:
            return {}

        table_sql = _quote_sql_identifier(table)
        cur.execute(f"PRAGMA table_info({table_sql})")
        columns = [row[1] for row in cur.fetchall()]
        column_by_lower = {column.lower(): column for column in columns}

        username_column = next(
            (
                column_by_lower[name]
                for name in ("username", "user_name", "usrname", "user_id")
                if name in column_by_lower
            ),
            None,
        )
        if not username_column:
            return {}

        # 本地备注最能帮助用户辨认，其次使用微信昵称和微信号别名。
        display_columns = []
        for candidate in (
            "remark",
            "nick_name",
            "nickname",
            "alias",
            "name",
        ):
            column = column_by_lower.get(candidate)
            if column and column not in display_columns:
                display_columns.append(column)

        selected_columns = [username_column, *display_columns]
        sql_columns = ", ".join(
            _quote_sql_identifier(column) for column in selected_columns
        )
        cur.execute(f"SELECT {sql_columns} FROM {table_sql}")

        result = {}
        for row in cur.fetchall():
            username = _decode_db_text(row[0]).strip()
            if not username:
                continue
            display_name = next(
                (
                    _decode_db_text(value).strip()
                    for value in row[1:]
                    if _decode_db_text(value).strip()
                ),
                "",
            )
            result[username] = display_name or username
        return result
    except sqlite3.Error:
        return {}


def load_group_member_name_map(conn_contact):
    """读取群成员的群昵称，返回 ``{群ID: {成员username: 群昵称}}``。

    微信不同版本的 contact.db 表名和字段略有差异。这里不绑定某一个
    固定 schema：优先识别常见的 chatroom_member / chat_room 关联表；找不到
    群昵称时自然回退到普通联系人显示名，绝不把备注误当作群昵称。
    """
    if conn_contact is None:
        return {}

    try:
        cur = conn_contact.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [str(row[0]) for row in cur.fetchall()]
        table_lookup = {name.lower(): name for name in tables}

        def first_column(columns, candidates):
            by_lower = {column.lower(): column for column in columns}
            return next((by_lower[name] for name in candidates if name in by_lower), None)

        def columns_for(table):
            cur.execute(f"PRAGMA table_info({_quote_sql_identifier(table)})")
            return [str(row[1]) for row in cur.fetchall()]

        contact_table = table_lookup.get("contact")
        contact_by_id = {}
        if contact_table:
            contact_columns = columns_for(contact_table)
            contact_id_column = first_column(contact_columns, ("id", "contact_id", "local_id"))
            username_column = first_column(
                contact_columns, ("username", "user_name", "usrname", "user_id")
            )
            if contact_id_column and username_column:
                cur.execute(
                    "SELECT "
                    f"{_quote_sql_identifier(contact_id_column)}, "
                    f"{_quote_sql_identifier(username_column)} "
                    f"FROM {_quote_sql_identifier(contact_table)}"
                )
                contact_by_id = {
                    _decode_db_text(row_id).strip(): _decode_db_text(username).strip()
                    for row_id, username in cur.fetchall()
                    if _decode_db_text(row_id).strip() and _decode_db_text(username).strip()
                }

        room_by_id = {}
        for candidate in ("chat_room", "chatroom", "chat_room_info"):
            room_table = table_lookup.get(candidate)
            if not room_table:
                continue
            room_columns = columns_for(room_table)
            room_id_column = first_column(room_columns, ("id", "room_id", "chatroom_id", "local_id"))
            room_username_column = first_column(
                room_columns, ("username", "user_name", "room_username", "chatroom_username")
            )
            if room_id_column and room_username_column:
                cur.execute(
                    "SELECT "
                    f"{_quote_sql_identifier(room_id_column)}, "
                    f"{_quote_sql_identifier(room_username_column)} "
                    f"FROM {_quote_sql_identifier(room_table)}"
                )
                room_by_id.update(
                    {
                        _decode_db_text(row_id).strip(): _decode_db_text(username).strip()
                        for row_id, username in cur.fetchall()
                        if _decode_db_text(row_id).strip() and _decode_db_text(username).strip()
                    }
                )

        result = {}
        member_tables = [
            table for table in tables
            if "member" in table.lower() and ("room" in table.lower() or "chat" in table.lower())
        ]
        for member_table in member_tables:
            columns = columns_for(member_table)
            room_column = first_column(
                columns, ("room_id", "chatroom_id", "chat_room_id", "roomid", "chatroomid", "talker")
            )
            member_username_column = first_column(
                columns,
                ("member_username", "member_user_name", "username", "user_name", "wxid"),
            )
            member_id_column = first_column(
                columns, ("member_id", "contact_id", "user_id", "memberid")
            )
            nickname_column = first_column(
                columns,
                (
                    "group_nickname", "room_nickname", "chatroom_nickname",
                    "member_nickname", "display_name", "nick_name", "nickname", "name",
                ),
            )
            if not room_column or not nickname_column:
                continue

            selected = [room_column, nickname_column]
            if member_username_column and member_username_column not in selected:
                selected.append(member_username_column)
            if member_id_column and member_id_column not in selected:
                selected.append(member_id_column)
            cur.execute(
                "SELECT " + ", ".join(_quote_sql_identifier(column) for column in selected)
                + f" FROM {_quote_sql_identifier(member_table)}"
            )
            for row in cur.fetchall():
                values = {
                    column: _decode_db_text(value).strip()
                    for column, value in zip(selected, row)
                }
                raw_room = values.get(room_column, "")
                room_username = raw_room if raw_room.endswith("@chatroom") else room_by_id.get(raw_room, "")
                member_username = values.get(member_username_column, "") if member_username_column else ""
                if not member_username and member_id_column:
                    member_username = contact_by_id.get(values.get(member_id_column, ""), "")
                nickname = values.get(nickname_column, "")
                if room_username and member_username and nickname and nickname != member_username:
                    result.setdefault(room_username, {})[member_username] = nickname
        return result
    except sqlite3.Error:
        return {}


def load_contact_gender_map(conn_contact):
    """读取联系人库中明确记录的性别，不对昵称、头像或聊天内容做推断。"""
    if conn_contact is None:
        return {}
    try:
        cur = conn_contact.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {str(row[0]).lower(): str(row[0]) for row in cur.fetchall()}
        contact_table = tables.get("contact")
        if not contact_table:
            return {}
        cur.execute(f"PRAGMA table_info({_quote_sql_identifier(contact_table)})")
        columns = [str(row[1]) for row in cur.fetchall()]
        by_lower = {column.lower(): column for column in columns}
        username_column = next(
            (
                by_lower[name]
                for name in ("username", "user_name", "usrname", "user_id")
                if name in by_lower
            ),
            None,
        )
        gender_column = next(
            (
                by_lower[name]
                for name in ("gender", "sex", "gender_type", "sex_type")
                if name in by_lower
            ),
            None,
        )
        if not username_column or not gender_column:
            return {}
        cur.execute(
            "SELECT "
            f"{_quote_sql_identifier(username_column)}, "
            f"{_quote_sql_identifier(gender_column)} "
            f"FROM {_quote_sql_identifier(contact_table)}"
        )
        values = {
            "1": "male", "male": "male", "m": "male", "男": "male",
            "2": "female", "female": "female", "f": "female", "女": "female",
        }
        result = {}
        for username, raw_gender in cur.fetchall():
            username = _decode_db_text(username).strip()
            gender = values.get(_decode_db_text(raw_gender).strip().lower())
            if username and gender:
                result[username] = gender
        return result
    except sqlite3.Error:
        return {}


def get_sender_usernames_by_range(conn_msg, chatroom_id, start_ts: int, end_ts: int):
    """返回某个时间段内实际发过言的成员 username，供群友名片设置使用。"""
    return sorted(
        {sender for _timestamp, sender, _content in _collect_text_rows(
            conn_msg, chatroom_id, start_ts, end_ts
        ) if sender}
    )


def _load_sender_username_map(cursor):
    """读取当前消息分库的 real_sender_id -> username 映射。"""
    try:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        table = next((name for name in tables if name.lower() == "name2id"), None)
        if not table:
            return {}
        table_sql = _quote_sql_identifier(table)
        cursor.execute(f"PRAGMA table_info({table_sql})")
        columns = [row[1] for row in cursor.fetchall()]
        column_by_lower = {column.lower(): column for column in columns}
        username_column = next(
            (
                column_by_lower[name]
                for name in ("user_name", "username", "usrname")
                if name in column_by_lower
            ),
            None,
        )
        if not username_column:
            return {}
        cursor.execute(
            f"SELECT rowid, {_quote_sql_identifier(username_column)} FROM {table_sql}"
        )
        return {
            int(row_id): _decode_db_text(username).strip()
            for row_id, username in cursor.fetchall()
            if _decode_db_text(username).strip()
        }
    except (sqlite3.Error, TypeError, ValueError):
        return {}


def _split_sender_prefix(content, sender_username=""):
    """移除群消息正文前的内部 username，但只在它确实是发送者时移除。"""
    prefix, separator, body = content.partition(":\n")
    if separator and (not sender_username or prefix == sender_username):
        return sender_username or prefix, body
    return sender_username, content


def _collect_text_rows(conn_msg, chatroom_id, start_ts, end_ts=None):
    """收集文本消息并保留发送者 username，跨分库合并、去重、排序。"""
    table = conversation_table(chatroom_id)
    rows = []
    for shard_index, conn in enumerate(_message_connections(conn_msg)):
        cur = conn.cursor()
        sender_usernames = _load_sender_username_map(cur)
        sql = (
            f"SELECT create_time, real_sender_id, message_content FROM {table} "
            "WHERE create_time >= ? AND local_type = 1"
        )
        params = [start_ts]
        if end_ts is not None:
            sql += " AND create_time <= ?"
            params.append(end_ts)
        try:
            cur.execute(sql, params)
            fetched_rows = cur.fetchall()
        except sqlite3.Error:
            # 兼容没有 real_sender_id 的旧消息表；缺表则第二次查询也会失败。
            legacy_sql = (
                f"SELECT create_time, message_content FROM {table} "
                "WHERE create_time >= ? AND local_type = 1"
            )
            if end_ts is not None:
                legacy_sql += " AND create_time <= ?"
            try:
                cur.execute(legacy_sql, params)
                fetched_rows = [
                    (timestamp, None, content)
                    for timestamp, content in cur.fetchall()
                ]
            except sqlite3.Error:
                # 群聊可能只存在于部分 message_N.db；缺表是正常情况。
                continue

        for row_index, (timestamp, real_sender_id, content) in enumerate(fetched_rows):
            try:
                sender_username = sender_usernames.get(int(real_sender_id), "")
            except (TypeError, ValueError):
                sender_username = ""
            rows.append(
                (
                    int(timestamp),
                    shard_index,
                    row_index,
                    sender_username,
                    content,
                )
            )

    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    unique_rows = []
    seen_shards = {}
    for timestamp, shard_index, _index, sender_username, content in rows:
        if not content:
            continue
        content_str = _decode_db_text(content)
        if not content_str:
            continue
        sender_username, content_str = _split_sender_prefix(
            content_str, sender_username
        )
        content_str = content_str.strip()
        if not content_str:
            continue
        if content_str.lstrip().startswith("<"):
            continue
        signature = (timestamp, sender_username, content_str)
        # 仅去掉分库边界处的跨分片重复；同一分片内同秒发送的相同文本
        # 可能是真实的连续消息，不能误删。
        first_shard = seen_shards.get(signature)
        if first_shard is not None and first_shard != shard_index:
            continue
        if first_shard is None:
            seen_shards[signature] = shard_index
        unique_rows.append((timestamp, sender_username, content_str))
    return unique_rows


def _sender_labels(rows, sender_name_map=None):
    """生成稳定的人类可读标签，并区分同名群友且不暴露内部 username。"""
    sender_name_map = sender_name_map or {}
    senders = sorted({sender for _time, sender, _content in rows if sender})
    fallback_numbers = {sender: index for index, sender in enumerate(senders, start=1)}
    base_names = {}
    for sender in senders:
        name = _decode_db_text(sender_name_map.get(sender, "")).strip()
        name = re.sub(r"[\r\n\t]+", " ", name).strip()
        base_names[sender] = name or f"群友{fallback_numbers[sender]}"

    grouped = {}
    for sender, name in base_names.items():
        grouped.setdefault(name, []).append(sender)

    labels = {}
    for name, same_name_senders in grouped.items():
        ordered = sorted(same_name_senders)
        if len(ordered) == 1:
            labels[ordered[0]] = name
        else:
            for index, sender in enumerate(ordered, start=1):
                labels[sender] = f"{name}（同名{index}）"
    return labels


def _format_text_messages(rows, sender_name_map, time_format):
    labels = _sender_labels(rows, sender_name_map)
    messages = []
    for timestamp, sender_username, content_str in rows:
        sender_label = labels.get(sender_username, "未知成员")
        time_text = datetime.datetime.fromtimestamp(timestamp).strftime(time_format)
        messages.append(f"[{time_text}] {sender_label}：{content_str}")
    return messages


def get_messages(conn_msg, chatroom_id, days=1, sender_name_map=None):
    """跨所有消息分库获取指定群聊最近 N 天的文本消息。"""
    since = int((datetime.datetime.now() - datetime.timedelta(days=days)).timestamp())
    rows = _collect_text_rows(conn_msg, chatroom_id, since)
    return _format_text_messages(rows, sender_name_map, "%H:%M")


def get_messages_by_range(
    conn_msg,
    chatroom_id,
    start_ts: int,
    end_ts: int,
    sender_name_map=None,
):
    """跨所有消息分库获取 [start_ts, end_ts] 内的文本消息。"""
    rows = _collect_text_rows(conn_msg, chatroom_id, start_ts, end_ts)
    return _format_text_messages(rows, sender_name_map, "%Y-%m-%d %H:%M")


# ─────────────────────────────────────────────────────────────────────────────
# 5. AI 总结
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_PROMPT_TEMPLATE = """\
你是一个熟悉群内气氛、会接梗但不冒犯人的微信群聊日报编辑，文风轻松、俏皮、有一点“群内表情包”的感觉。
请根据下面提供的完整聊天记录，生成一份可以直接复制发送到微信群的日报。

聊天日期：{date}{day_range}
消息数量：{count} 条

{messages}

聊天记录中的每一行都严格采用“[时间] 发送者：消息正文”的格式。
冒号前的“发送者”由微信数据库直接解析，是该条消息的真实发言人；正文里出现的其他昵称，只代表被提及、被回复或被引用的人，不代表那个人说了这句话。

不要逐条复述聊天记录，而要从整体上识别：
1. 持续时间较长、参与人数较多的话题
2. 有明显起因、发展和结果的故事线
3. 技术讨论、重要观点、实用信息和待跟进事项
4. 有趣的吃瓜、玩梗和日常话题
5. 活跃成员当天最鲜明的人设或贡献

输出结构：

🗞 今日概览
用一段话概括当天群聊的整体氛围和主要内容，可以穿插 1～2 个贴合语境的 emoji 或一句简短吐槽。

🔥 核心议题
选出 5～10 个主要话题，使用“1️⃣ 话题名称”“2️⃣ 话题名称”的形式编号；内容不足时不要硬凑。
有剧情发展的事情按“起因、发展、群友讨论、当前结果”写清楚；没有完整故事线的话题自然概括，不要硬凑四个阶段。
每个话题可以搭配 1 个贴合内容的 emoji，并在合适的位置加入一句表情包式短评，例如“（这合理吗.jpg）”“（懂得都懂👀）”“主打一个稳中带皮😂”；不要机械套用示例。

👑 今日 MVP
直接写“昵称｜一句有节目效果的称号”，下一行说明当选原因。必须根据该昵称作为“发送者”的实际发言判断，不能因为他被别人频繁提到就把别人的话算到他头上。

🏆 趣味成就
给不同群友颁发 5～10 个搞笑成就，内容不足时宁缺毋滥。每项严格使用下面的纯文本形式：
① 成就名称｜昵称
理由：一句简短、有梗但不恶意的说明，可以加一个贴合该成就的 emoji

输出要求（必须严格遵守）：
1. 输出会直接复制到微信群，必须使用纯文本，禁止使用任何 Markdown 语法。
2. 不得使用 #、##、星号加粗、反引号、Markdown 表格或 Markdown 链接。
3. 不要重复输出群聊名称、日期、时间范围和消息总数，程序会自动添加这些信息。
4. 全文自然穿插 8～15 个 emoji 或文字表情梗，优先选择与话题相关的表情，如 😂、👀、🤡、🫡、🏂、🍿、💻；不要连续堆叠，也不要每句话都加。
5. 可以偶尔使用“（地铁老人看手机.jpg）”“（默默打开购物软件）”这类表情包式旁白，但必须贴合上下文，全文最多 3 处。
6. 可以玩梗、吐槽，但不要恶意攻击，不要杜撰原聊天中不存在的事实。
7. 涉及具体群友的观点、人设、MVP 和成就时，必须以行首明确标注的发送者为准；拿不准归属就使用“有群友提到”，禁止猜测。
8. 像一个潜水已久、很懂这个群的人来写：信息密度高，有娱乐性，松弛自然，不要油腻或过度夸张。"""

# 模板中可用的占位符说明：
# {date}      → 当前日期，如"2026年04月29日"
# {day_range} → 时间范围描述，如"今天"或"最近7天"
# {count}     → 消息条数
# {messages}  → 聊天记录正文


# 免费推理端点的最大上下文并不等于能在超时内处理完的输入量。
# 约 2.4 万字符能显著降低单次首 token 延迟，同时避免产生过多请求。
SUMMARY_CHUNK_CHARS = 24000
SUMMARY_CACHE_VERSION = 1
SUMMARY_CACHE_DIR = os.path.join(APP_DATA_DIR, ".summary_cache")
SUMMARY_CACHE_MAX_FILES = 200


def _split_summary_chunks(items, max_chars=SUMMARY_CHUNK_CHARS):
    """按字符数分段且不丢弃内容，单条超长消息也会被拆开。"""
    chunks = []
    current = []
    current_size = 0
    for original in items:
        text = str(original)
        parts = (
            [text]
            if len(text) <= max_chars
            else [text[i:i + max_chars] for i in range(0, len(text), max_chars)]
        )
        for part in parts:
            extra = len(part) + (1 if current else 0)
            if current and current_size + extra > max_chars:
                chunks.append("\n".join(current))
                current = []
                current_size = 0
            current.append(part)
            current_size += len(part) + (1 if len(current) > 1 else 0)
    if current:
        chunks.append("\n".join(current))
    return chunks


def _notify_summary_progress(callback, message):
    if callback:
        try:
            callback(message)
        except Exception:
            pass


def _summary_cache_key(provider, model, max_tokens, prompt):
    """为总结阶段生成不暴露聊天正文的稳定缓存键。"""
    selected_model = str(model or "").strip() or provider_default_model(provider)
    source = json.dumps(
        {
            "version": SUMMARY_CACHE_VERSION,
            "provider": provider,
            "model": selected_model,
            "max_tokens": int(max_tokens),
            "prompt": prompt,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _load_summary_cache(cache_key):
    path = os.path.join(SUMMARY_CACHE_DIR, f"{cache_key}.json")
    try:
        with open(path, "r", encoding="utf-8") as source:
            data = json.load(source)
        text = data.get("text") if isinstance(data, dict) else None
        return text if isinstance(text, str) and text.strip() else None
    except (OSError, ValueError, TypeError):
        return None


def _prune_summary_cache():
    try:
        entries = [
            os.path.join(SUMMARY_CACHE_DIR, name)
            for name in os.listdir(SUMMARY_CACHE_DIR)
            if name.endswith(".json")
        ]
        entries.sort(key=os.path.getmtime, reverse=True)
        for path in entries[SUMMARY_CACHE_MAX_FILES:]:
            try:
                os.remove(path)
            except OSError:
                pass
    except OSError:
        pass


def _save_summary_cache(cache_key, text):
    """仅缓存模型输出；聊天原文不会写入缓存文件。"""
    try:
        os.makedirs(SUMMARY_CACHE_DIR, exist_ok=True)
        final_path = os.path.join(SUMMARY_CACHE_DIR, f"{cache_key}.json")
        temp_path = final_path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as target:
            json.dump({"text": text}, target, ensure_ascii=False)
        os.replace(temp_path, final_path)
        _prune_summary_cache()
    except OSError:
        # 缓存失败不能影响正常生成。
        pass


def _cached_summary_completion(api_key, prompt, max_tokens=2000,
                               provider=DEFAULT_PROVIDER, model=None,
                               progress_callback=None, cache_message=None,
                               cancel_event=None):
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("任务已取消。")
    cache_key = _summary_cache_key(provider, model, max_tokens, prompt)
    cached = _load_summary_cache(cache_key)
    if cached is not None:
        if cache_message:
            _notify_summary_progress(progress_callback, cache_message)
        return cached
    result = _chat_completion(
        api_key,
        prompt,
        max_tokens=max_tokens,
        provider=provider,
        model=model,
        retry_callback=progress_callback,
        cancel_event=cancel_event,
    )
    _save_summary_cache(cache_key, result)
    return result


def provider_label(provider):
    config = PROVIDERS.get(provider)
    return str(config["label"]) if config else provider


def provider_default_model(provider):
    config = PROVIDERS.get(provider)
    if not config:
        raise ValueError(f"不支持的 AI 服务商：{provider}")
    return str(config["default_model"])


def provider_model_options(provider):
    """返回该服务商的常用模型列表，供下拉框预填；用户仍可自行输入其它模型。"""
    config = PROVIDERS.get(provider) or {}
    models = list(config.get("models") or [])
    default = str(config.get("default_model") or "")
    if default and default not in models:
        models.insert(0, default)
    return models


def _chat_completion(api_key, prompt, max_tokens=2000,
                     provider=DEFAULT_PROVIDER, model=None, retry_callback=None,
                     cancel_event=None):
    provider_config = PROVIDERS.get(provider)
    if not provider_config:
        raise ValueError(f"不支持的 AI 服务商：{provider}")
    label = str(provider_config["label"])
    clean_key = str(api_key or "").strip()
    if not clean_key:
        raise ValueError(f"请先填写 {label} API Key。")
    selected_model = str(model or "").strip() or str(provider_config["default_model"])
    if provider == "gemini":
        endpoint = str(provider_config["endpoint"]).format(
            model=quote(selected_model, safe="")
        )
        headers = {
            "x-goog-api-key": clean_key,
            "Content-Type": "application/json",
        }
        payload = {
            "system_instruction": {
                "parts": [{
                    "text": "你是一个信息提炼助手，擅长从群聊记录中提取有价值的内容。"
                }]
            },
            "contents": [
                {"role": "user", "parts": [{"text": prompt}]},
            ],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": max_tokens,
                # 日报提炼属于常规任务，低思考档兼顾速度、成本和稳定性。
                "thinkingConfig": {"thinkingLevel": "low"},
            },
        }
    else:
        endpoint = str(provider_config["endpoint"])
        headers = {
            "Authorization": f"Bearer {clean_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": selected_model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是一个信息提炼助手，擅长从群聊记录中提取有价值的内容。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": max_tokens,
        }
    # NVIDIA 的 DeepSeek V4 示例支持显式关闭思考模式。群聊摘要不需要
    # 输出推理过程，关闭后可明显减少免费端点的等待和输出 token。
    if provider == "nvidia" and "deepseek-v4" in selected_model.lower():
        payload["chat_template_kwargs"] = {"thinking": False}
    timeout = {
        "gemini": GEMINI_REQUEST_TIMEOUT,
        "nvidia": NVIDIA_REQUEST_TIMEOUT,
    }.get(provider, DEEPSEEK_REQUEST_TIMEOUT)
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("任务已取消。")
    max_attempts = GEMINI_MAX_ATTEMPTS if provider == "gemini" else 1
    response = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=timeout,
            )
        except requests.ReadTimeout as exc:
            if provider == "gemini" and attempt < max_attempts:
                response = None
            elif provider == "nvidia":
                raise RuntimeError(
                    "NVIDIA 免费端点等待超过 120 秒。"
                    "本段没有完成，但此前完成的段落已保留；再次生成会从缓存继续。"
                ) from exc
            elif provider == "gemini":
                raise RuntimeError(
                    "Gemini 连续等待超时。本段没有完成，但此前完成的段落已保留；"
                    "再次生成会从缓存继续。"
                ) from exc
            else:
                raise RuntimeError(
                    "DeepSeek API 等待超过 90 秒，请检查网络后重试。"
                ) from exc
        except requests.RequestException as exc:
            raise RuntimeError(f"连接 {label} API 失败：{exc}") from exc

        transient_status = (
            response is not None
            and response.status_code in (408, 429, 500, 502, 503, 504)
        )
        if provider != "gemini" or attempt >= max_attempts or not (
            response is None or transient_status
        ):
            break
        delay = 2 ** (attempt - 1)
        _notify_summary_progress(
            retry_callback,
            f"Gemini 暂时繁忙，{delay} 秒后重试（{attempt}/{max_attempts - 1}）...",
        )
        if cancel_event is not None:
            if cancel_event.wait(delay):
                raise RuntimeError("任务已取消。")
        else:
            time.sleep(delay)

    if response is None:
        raise RuntimeError("Gemini 请求没有收到响应，请稍后再试。")
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("任务已取消。")

    if provider == "gemini":
        error_messages = {
            400: "Gemini 请求格式错误，请检查模型名和输入内容。",
            401: "Gemini API Key 无效，请确认复制完整。",
            403: "Gemini 拒绝了请求，请检查 Key 权限以及项目是否已启用付费方案。",
            404: "Gemini 没有找到该模型，请确认模型名和 API 版本。",
            429: "Gemini 达到频率、Token、每日或消费额度限制，请检查项目配额。",
            500: "Gemini 服务异常或输入过长，请稍后重试。",
            502: "Gemini 网关暂时异常，请稍后重试。",
            503: "Gemini 当前繁忙，请稍后重试。",
            504: "Gemini 网关等待超时，请稍后重试。",
        }
    elif provider == "nvidia":
        error_messages = {
            400: "NVIDIA 请求格式错误，请检查模型名是否正确。",
            401: "NVIDIA API Key 无效，请在 API Catalog 重新生成并完整复制。",
            402: "NVIDIA API 免费额度或试用权益不可用，请检查账号状态。",
            403: "NVIDIA 拒绝了请求，当前 Key 可能没有该模型的访问权限。",
            404: "NVIDIA 没有找到该模型，请从模型页重新复制模型名。",
            422: "NVIDIA 不接受当前请求参数，请检查模型名。",
            429: "NVIDIA 免费接口达到频率或额度限制，请稍后再试。",
            500: "NVIDIA 服务暂时异常，请稍后再试。",
            503: "NVIDIA 当前繁忙，请稍后再试。",
        }
    else:
        error_messages = {
            400: "DeepSeek 请求格式错误，请检查模型名。",
            401: "DeepSeek API Key 无效，请检查是否复制完整。",
            402: "DeepSeek API 余额不足，请充值或切换到 Google Gemini。",
            422: "DeepSeek 不接受当前请求参数，请检查模型名。",
            429: "DeepSeek 请求过于频繁，请稍后再试。",
            500: "DeepSeek 服务暂时异常，请稍后再试。",
            503: "DeepSeek 当前繁忙，请稍后再试。",
        }
    if response.status_code in error_messages:
        raise RuntimeError(error_messages[response.status_code])

    try:
        response.raise_for_status()
        data = response.json()
        if provider == "gemini":
            parts = data["candidates"][0]["content"]["parts"]
            content = "\n".join(
                str(part.get("text") or "")
                for part in parts
                if isinstance(part, dict) and part.get("text")
            )
            if not content.strip():
                finish_reason = data.get("candidates", [{}])[0].get(
                    "finishReason", "没有文本输出"
                )
                raise ValueError(f"响应中没有文本内容（{finish_reason}）")
        else:
            content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("响应中没有文本内容")
        return content
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} 返回了无法识别的响应：{exc}") from exc


def _deepseek_chat(api_key, prompt, max_tokens=2000):
    """向后兼容的 DeepSeek 调用入口。"""
    return _chat_completion(
        api_key, prompt, max_tokens=max_tokens, provider="deepseek"
    )


def _markdown_table_to_plain_text(lines):
    """把模型偶尔返回的 Markdown 表格转换为适合微信的纯文本条目。"""
    converted = []
    index = 0
    separator_cell = re.compile(r"^:?-{3,}:?$")

    def split_row(line):
        stripped = line.strip().strip("|")
        return [cell.strip() for cell in stripped.split("|")]

    while index < len(lines):
        if index + 1 < len(lines) and "|" in lines[index] and "|" in lines[index + 1]:
            header = split_row(lines[index])
            separator = split_row(lines[index + 1])
            if separator and all(
                separator_cell.fullmatch(cell.replace(" ", "")) for cell in separator
            ):
                index += 2
                row_number = 1
                while index < len(lines) and "|" in lines[index]:
                    cells = split_row(lines[index])
                    if len(cells) >= 3:
                        converted.append(
                            f"{row_number}、{cells[0]}｜{cells[1]} —— "
                            + "｜".join(cells[2:])
                        )
                    elif len(cells) == 2:
                        converted.append(f"{row_number}、{cells[0]}｜{cells[1]}")
                    elif cells and cells[0]:
                        converted.append(f"{row_number}、{cells[0]}")
                    row_number += 1
                    index += 1
                continue
        converted.append(lines[index])
        index += 1
    return converted


def to_wechat_plain_text(text):
    """清理模型未遵守指令时残留的 Markdown，输出微信友好的纯文本。"""
    if not text:
        return text

    value = str(text).replace("\r\n", "\n").replace("\r", "\n")
    lines = _markdown_table_to_plain_text(value.split("\n"))
    value = "\n".join(lines)

    # 块级 Markdown
    value = re.sub(r"(?m)^\s*```[^\n]*$", "", value)
    value = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", value)
    value = re.sub(r"(?m)^\s{0,3}>\s?", "", value)
    value = re.sub(r"(?m)^\s*[-*_]{3,}\s*$", "", value)
    value = re.sub(r"(?m)^\s*[-*+]\s+", "• ", value)

    # 行内 Markdown；链接保留可读文本与真实地址。
    value = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"\1（\2）", value)
    value = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1（\2）", value)
    value = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", value)
    value = re.sub(r"__([^_\n]+)__", r"\1", value)
    value = re.sub(r"`([^`\n]+)`", r"\1", value)

    # 收紧过多空行，粘贴到微信后不会被拉得太长。
    value = re.sub(r"\n[ \t]+\n", "\n\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def ai_summarize(messages, api_key, group_id="", days=1, prompt_template=None,
                 progress_callback=None, provider=DEFAULT_PROVIDER, model=None,
                 cancel_event=None):
    """调用选定的 AI 服务；长记录自动分段提炼后再合并。"""
    if not messages:
        return "该时间段内没有消息。"

    date_str = datetime.datetime.now().strftime("%Y年%m月%d日")
    day_str = f"最近{days}天" if days > 1 else "今天"
    template = prompt_template if prompt_template else DEFAULT_PROMPT_TEMPLATE

    try:
        # 先验证自定义模板，避免已经完成分段请求后才发现占位符写错。
        template.format(
            date=date_str, day_range=day_str, count=len(messages), messages=""
        )
    except KeyError as e:
        raise ValueError(
            f"提示词模板格式错误，未知占位符：{e}\n"
            "请在「修改提示词」中检查模板。"
        ) from e

    try:
        chunks = _split_summary_chunks(messages)
        if len(chunks) == 1:
            _notify_summary_progress(
                progress_callback,
                f"正在通过 {provider_label(provider)} 生成 AI 总结...",
            )
            prompt = template.format(
                date=date_str,
                day_range=day_str,
                count=len(messages),
                messages=chunks[0],
            )
            return to_wechat_plain_text(
                _cached_summary_completion(
                    api_key,
                    prompt,
                    provider=provider,
                    model=model,
                    progress_callback=progress_callback,
                    cache_message="已恢复上次生成完成的总结。",
                    cancel_event=cancel_event,
                )
            )

        partial_summaries = []
        total_chunks = len(chunks)
        for index, chunk in enumerate(chunks, start=1):
            _notify_summary_progress(
                progress_callback,
                f"聊天记录较长，正在提炼第 {index}/{total_chunks} 段...",
            )
            partial_prompt = f"""\
以下是同一个微信群聊记录的第 {index}/{total_chunks} 段。
每行格式为“[时间] 发送者：消息正文”，发送者来自微信数据库；正文中被提及或引用的昵称不是当前发言人：

{chunk}

请提取本段的核心话题、有价值信息、明确结论、待办事项和重要链接。
涉及个人观点或趣味事件时保留真实发送者，禁止把被提及者误当成发言人；证据不足则不要署名。
忽略寒暄与无意义闲聊，保留具体事实，输出简洁的中文要点。"""
            partial_summaries.append(
                _cached_summary_completion(
                    api_key,
                    partial_prompt,
                    max_tokens=1400,
                    provider=provider,
                    model=model,
                    progress_callback=progress_callback,
                    cache_message=f"已恢复第 {index}/{total_chunks} 段，继续处理...",
                    cancel_event=cancel_event,
                )
            )

        # 分段摘要仍过长时继续分层压缩，直到能安全放入最终请求。
        reduce_round = 1
        while len("\n\n".join(partial_summaries)) > SUMMARY_CHUNK_CHARS:
            labelled = [
                f"分段摘要 {i}:\n{summary}"
                for i, summary in enumerate(partial_summaries, start=1)
            ]
            summary_groups = _split_summary_chunks(labelled)
            reduced = []
            for index, group in enumerate(summary_groups, start=1):
                _notify_summary_progress(
                    progress_callback,
                    f"正在合并长摘要，第 {reduce_round} 轮 {index}/{len(summary_groups)}...",
                )
                reduce_prompt = f"""\
请合并下面这些同一群聊的分段摘要：去除重复，保留事实、结论、待办、链接以及已经明确对应的发送者，
不要引入原文中没有的信息，不要改变发言人与观点的对应关系。输出结构紧凑的中文要点。

{group}"""
                reduced.append(
                    _cached_summary_completion(
                        api_key,
                        reduce_prompt,
                        max_tokens=1400,
                        provider=provider,
                        model=model,
                        progress_callback=progress_callback,
                        cache_message=(
                            f"已恢复合并结果，第 {reduce_round} 轮 "
                            f"{index}/{len(summary_groups)}..."
                        ),
                        cancel_event=cancel_event,
                    )
                )
            partial_summaries = reduced
            reduce_round += 1

        _notify_summary_progress(progress_callback, "正在合并全部分段并生成最终总结...")
        merged_source = (
            "以下内容是完整聊天记录的分段提炼结果，请在最终总结中去重并综合：\n\n"
            + "\n\n".join(partial_summaries)
        )
        final_prompt = template.format(
            date=date_str,
            day_range=day_str,
            count=len(messages),
            messages=merged_source,
        )
        return to_wechat_plain_text(
            _cached_summary_completion(
                api_key,
                final_prompt,
                provider=provider,
                model=model,
                progress_callback=progress_callback,
                cache_message="已恢复最终总结。",
                cancel_event=cancel_event,
            )
        )
    except Exception as e:
        raise RuntimeError(f"AI总结失败：{e}") from e


NEWSPAPER_DIGEST_PROMPT = """\
你是中文报纸的头版编辑。请把下面的群聊总结压缩成一张单页日报的内容。

群聊：{group_name}
日期：{date_range}
消息数：{message_count}

{summary}

只输出一个 JSON 对象，不得输出 Markdown、代码块或解释。格式为：
{{
  "headline": "12～24个字的头版标题",
  "lead": "60～100个字的今日导语",
  "topics": [
    {{"title": "话题短标题", "points": ["能独立成句的短要点", "能独立成句的短要点"], "summary": "40～80字，说清起因、讨论或结果", "bubble": "这个话题里最有代表性的一句短口语", "visual_prompt": "对应话题的简短英文插画描述"}}
  ],
  "mvp_rankings": [
    {{"name": "昵称", "title": "有趣但不冒犯的称号", "reason": "30～50字理由", "visual_prompt": "对应人物气质的简短英文Q版肖像描述"}}
  ],
  "achievements": [
    {{"award": "趣味成就名", "name": "昵称", "reason": "20～40字理由"}}
  ],
  "quotes": [{{"speaker": "昵称", "text": "当天真实金句，不超过 30 字"}}],
  "tomorrow_topics": ["根据今天内容判断、明天可能继续讨论的话题"],
  "special_notes": ["值得提醒或特别关注的信息；无法确认时明确写可能或仅供娱乐"]
}}

要求：
1. 为保持固定版式：topics 最多 6 个，mvp_rankings 最多 3 人，achievements 最多 6 个，quotes 最多 7 条，tomorrow_topics 和 special_notes 各最多 5 条。
2. 每一条都要短，但只要当天确有真实素材，就尽量把固定栏位填满（6 个话题、6 个成就、7 条金句、明日话题与特别关注各 5 条）；素材不够时才少写，绝不用空话凑数。
2.1 金句是版面上最长的文字，每条不超过 30 字；过长的原话请截取最有代表性的一句，不要改写成新的话。
3. 只能使用来源总结中已有的事实和发言人，不得杜撰。
4. 不使用 emoji、网络链接或换行符，保持报纸杂志语气。
5. mvp_rankings 只能填写聊天记录中明确可识别、且当天确有发言或贡献的昵称；宁可只返回 1 人或 2 人，也绝不使用“群友”“某群友”“匿名”等占位名凑满 3 人。mvp_rankings 按今日存在感排序。
6. 如果金句或趣味成就的归属不确定，可以不署名或使用“有群友提到”；禁止猜测具体是谁。
7. visual_prompt 必须用英文描述一个有明确主体、具体动作和话题关键物件的单一漫画场景，不含姓名、文字、数字、品牌或标志；不得使用 generic chat, people talking, group chat 等泛泛描述。
8. tomorrow_topics 是基于当天尚未结束的话题作谨慎展望；special_notes 只写安全提醒、信息局限或需要继续确认的事情，不得把猜测写成事实。
9. points 每个话题 2～3 条，每条不超过 12 个字，必须能独立读懂；禁止把一句话拆成两半、禁止以标点开头。
10. bubble 是可以画进漫画气泡的短口语，不超过 8 个字，尽量贴近群友原话；没有合适的就写空字符串。
"""


def _short_text(value, limit):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: max(1, limit - 1)].rstrip() + "…"


def _parse_json_object(value):
    text = str(value or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型没有返回可用的 JSON 对象")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"模型返回的日报 JSON 无法解析：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("模型返回的日报数据不是 JSON 对象")
    return data


def _topic_points(raw_points, summary, limit=3):
    """整理话题要点；模型没给 points 时按句读切分 summary，避免逐行折断。"""
    points = []
    if isinstance(raw_points, list):
        for item in raw_points:
            text = _short_text(item, 14).lstrip("，。、；：·-— ")
            if text and text not in points:
                points.append(text)
    if not points:
        text = str(summary or "").strip()
        chunks = [
            chunk.strip()
            for chunk in re.split(r"[。；;，,、!！?？]\s*", text)
            if len(chunk.strip()) >= 4
        ]
        # 只有每段都短到能独立成行时才拆；否则整段当成一条，宁可长也不切断句子。
        if chunks and all(len(chunk) <= 20 for chunk in chunks):
            return chunks[:limit]
        return [text] if text else []
    return points[:limit]


def _normalise_newspaper_digest(
    data, group_name, date_range, message_count, member_genders=None, labels=None
):
    member_genders = member_genders if isinstance(member_genders, dict) else {}
    topics = data.get("topics") if isinstance(data.get("topics"), list) else []
    clean_topics = []
    for item in topics[:6]:
        if not isinstance(item, dict):
            continue
        title = _short_text(item.get("title"), 18)
        summary = _short_text(item.get("summary"), 120)
        if title or summary:
            clean_topics.append(
                {
                    "title": title or "今日重点",
                    "summary": summary,
                    "points": _topic_points(item.get("points"), summary),
                    "bubble": _short_text(item.get("bubble"), 10),
                    "visual_prompt": _short_text(item.get("visual_prompt"), 140),
                }
            )

    raw_rankings = (
        data.get("mvp_rankings")
        if isinstance(data.get("mvp_rankings"), list)
        else []
    )
    if not raw_rankings and isinstance(data.get("mvp"), dict):
        raw_rankings = [data.get("mvp")]
    rankings = []
    for item in raw_rankings[:3]:
        if not isinstance(item, dict):
            continue
        name = _short_text(item.get("name"), 12)
        # 人物榜不能用“群友”占位凑数；这会让日报看起来像在乱封称号。
        if not name or name.lower() in {"群友", "某群友", "匿名", "unknown", "n/a"}:
            continue
        rankings.append(
            {
                "name": name,
                "title": _short_text(item.get("title"), 14),
                "reason": _short_text(item.get("reason"), 60),
                "visual_prompt": _short_text(item.get("visual_prompt"), 120),
                # 性别仅来自用户在“群友名片”中的明确设置；不根据昵称或头像猜。
                "gender": str(member_genders.get(name) or "unspecified"),
            }
        )
    raw_mvp = rankings[0] if rankings else {}
    raw_achievements = (
        data.get("achievements")
        if isinstance(data.get("achievements"), list)
        else []
    )
    achievements = []
    for item in raw_achievements[:6]:
        if not isinstance(item, dict):
            continue
        achievements.append(
            {
                "award": _short_text(item.get("award"), 16) or "今日成就",
                "name": _short_text(item.get("name"), 14),
                "reason": _short_text(item.get("reason"), 52),
            }
        )
    raw_quotes = data.get("quotes") if isinstance(data.get("quotes"), list) else []
    if not raw_quotes and isinstance(data.get("quote"), dict):
        raw_quotes = [data.get("quote")]
    quotes = []
    for item in raw_quotes[:7]:
        if not isinstance(item, dict):
            continue
        # 金句是整图海报上最小最长的一栏，超过 30 字就会开始出现错字。
        text = _short_text(item.get("text"), 30)
        if text:
            quotes.append(
                {
                    "speaker": _short_text(item.get("speaker"), 10),
                    "text": text,
                }
            )
    tomorrow_topics = [
        _short_text(item, 42)
        for item in (data.get("tomorrow_topics") or [])[:5]
        if _short_text(item, 42)
    ] if isinstance(data.get("tomorrow_topics"), list) else []
    special_notes = [
        _short_text(item, 48)
        for item in (data.get("special_notes") or [])[:5]
        if _short_text(item, 48)
    ] if isinstance(data.get("special_notes"), list) else []

    labels = labels or build_scope_labels(1, "group")
    return {
        "date": _short_text(date_range, 32),
        "group_name": _short_text(group_name, 24),
        "message_count": str(int(message_count)),
        "count_label": labels.get("count_label", "今日群聊总结"),
        "title_label": labels.get("title_label", "群聊日报"),
        "overview_label": labels.get("overview_label", "群聊概览"),
        "chat_noun": labels.get("noun", "群聊"),
        "headline": _short_text(data.get("headline"), 28) or "今日群聊，重点都在这里",
        "lead": _short_text(data.get("lead"), 130),
        "topics": clean_topics,
        "mvp": {
            "name": _short_text(raw_mvp.get("name"), 16),
            "title": _short_text(raw_mvp.get("title"), 20),
            "reason": _short_text(raw_mvp.get("reason"), 80),
            "gender": str(raw_mvp.get("gender") or "unspecified"),
        },
        "mvp_rankings": rankings,
        "achievements": achievements,
        "quote": quotes[0] if quotes else {"speaker": "", "text": ""},
        "quotes": quotes,
        "tomorrow_topics": tomorrow_topics,
        "special_notes": special_notes,
    }


def ai_newspaper_digest(summary, api_key, group_name, date_range, message_count,
                        provider=DEFAULT_PROVIDER, model=None,
                        progress_callback=None, cancel_event=None,
                        member_genders=None, labels=None):
    """把已提炼的文字总结压缩为单页图片所需的结构化字段。"""
    if not str(summary or "").strip():
        raise ValueError("没有可用于生成图片日报的总结内容。")
    _notify_summary_progress(progress_callback, "正在挑选单页日报重点...")
    prompt = NEWSPAPER_DIGEST_PROMPT.format(
        group_name=group_name,
        date_range=date_range,
        message_count=message_count,
        summary=summary,
    )
    gender_hints = {
        _short_text(name, 16): gender
        for name, gender in (member_genders or {}).items()
        if str(gender) in {"female", "male"} and _short_text(name, 16)
    }
    if gender_hints:
        prompt += (
            "\n\n人物形象约束（仅用于人物榜插画；未列出的昵称一律使用中性形象，"
            "不得根据昵称、称号或聊天内容猜测性别）：\n"
            + "\n".join(
                f"- {name}：{'女性' if gender == 'female' else '男性'}"
                for name, gender in gender_hints.items()
            )
        )
    try:
        response = _chat_completion(
            api_key,
            prompt,
            max_tokens=3000,
            provider=provider,
            model=model,
            retry_callback=progress_callback,
            cancel_event=cancel_event,
        )
        data = _parse_json_object(response)
        return _normalise_newspaper_digest(
            data, group_name, date_range, message_count, gender_hints, labels
        )
    except Exception as exc:
        raise RuntimeError(f"图片日报内容生成失败：{exc}") from exc


# ─────────────────────────────────────────────────────────────────────────────
# 6. 配置管理
# ─────────────────────────────────────────────────────────────────────────────

def load_config():
    candidates = [CONFIG_FILE]
    if IS_FROZEN:
        executable_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidates.extend(
            [
                os.path.join(executable_dir, "config.json"),
                os.path.join(os.path.dirname(executable_dir), "config.json"),
            ]
        )
    for config_path in dict.fromkeys(candidates):
        if not os.path.exists(config_path):
            continue
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            continue
    return {}


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# 7. 主程序
# ─────────────────────────────────────────────────────────────────────────────

def install_deps():
    """自动安装依赖"""
    packages = ["pycryptodome", "requests", "psutil", "pillow"]
    for pkg in packages:
        try:
            if pkg == "pycryptodome":
                from Crypto.Cipher import AES
            elif pkg == "requests":
                import requests
            elif pkg == "psutil":
                import psutil
            elif pkg == "pillow":
                from PIL import Image
        except ImportError:
            print(f"正在安装依赖 {pkg}...")
            subprocess.run([sys.executable, "-m", "pip", "install", pkg, "-q"], check=False)


def main():
    print("=" * 60)
    print("  微信群聊 AI 总结工具")
    print("  适用：微信 4.x Windows 版")
    print("=" * 60)
    print()

    install_deps()
    cfg = load_config()

    # ── 获取/确认 AI 服务配置 ──
    provider = str(cfg.get("provider") or DEFAULT_PROVIDER)
    if provider not in PROVIDERS:
        provider = DEFAULT_PROVIDER
    api_keys = cfg.get("api_keys") if isinstance(cfg.get("api_keys"), dict) else {}
    api_key = str(
        api_keys.get(provider)
        or (cfg.get("api_key") if provider == "deepseek" else "")
        or ""
    ).strip()
    models = cfg.get("models") if isinstance(cfg.get("models"), dict) else {}
    model = str(models.get(provider) or provider_default_model(provider)).strip()
    if not api_key:
        print(f"❌ 请先在图形界面填写 {provider_label(provider)} API Key。")
        input("按回车键关闭...")
        return

    # ── 查找微信数据目录 ──
    print("正在查找微信数据目录...")
    base_dir, user_dir = find_wechat_data_dir()

    if not user_dir:
        print("❌ 未找到微信数据目录。请手动输入路径：")
        print("（提示：应包含 db_storage 文件夹，如 D:\\wx\\xwechat_files\\wxid_xxx）")
        user_dir = input("路径: ").strip().strip('"')
        if not os.path.isdir(user_dir):
            print("路径无效，退出。")
            input("按回车键关闭...")
            return

    db_storage = find_db_storage(user_dir)
    print(f"✅ 找到微信数据：{user_dir}")
    print()

    # ── 从内存提取密钥 ──
    print("正在从微信进程内存提取密钥（需要微信已登录）...")
    try:
        key_map, db_files, salt_to_dbs = extract_keys_from_memory(db_storage)
    except RuntimeError as e:
        print(f"❌ {e}")
        input("按回车键关闭...")
        return

    if not key_map:
        print("❌ 未能提取到密钥。请确认微信已登录并保持运行状态。")
        input("按回车键关闭...")
        return

    print(f"✅ 成功提取 {len(key_map)} 个密钥\n")

    # ── 解密全部 message_N.db 分库 ──
    message_dir = os.path.join(db_storage, "message")
    message_files = []
    for name in os.listdir(message_dir):
        match = re.fullmatch(r"message_(\d+)\.db", name, flags=re.IGNORECASE)
        if match:
            message_files.append((int(match.group(1)), os.path.join(message_dir, name)))
    message_files.sort()

    connections = []
    tmp_paths = []
    required_space = sum(os.path.getsize(path) for _index, path in message_files)
    contact_db_path = os.path.join(db_storage, "contact", "contact.db")
    if os.path.isfile(contact_db_path):
        required_space += os.path.getsize(contact_db_path)
    message_temp_dir = select_decrypt_temp_dir(required_space)
    contact_connection = None

    def cleanup_message_shards():
        for connection in connections:
            try:
                connection.close()
            except Exception:
                pass
        if contact_connection is not None:
            try:
                contact_connection.close()
            except Exception:
                pass
        for path in tmp_paths:
            try:
                if os.path.exists(path):
                    os.unlink(path)
            except OSError:
                pass

    print(f"正在解密 {len(message_files)} 个消息分库...")
    try:
        if not message_files:
            raise RuntimeError("未找到 message_N.db 消息数据库")
        for _index, msg_db in message_files:
            with open(msg_db, "rb") as source:
                msg_salt = source.read(16).hex()
            msg_key = key_map.get(msg_salt)
            if not msg_key:
                raise RuntimeError(f"未找到 {os.path.basename(msg_db)} 的解密密钥")
            tmp_path = decrypt_db(msg_db, msg_key, temp_dir=message_temp_dir)
            tmp_paths.append(tmp_path)
            connections.append(sqlite3.connect(tmp_path))

        if os.path.isfile(contact_db_path):
            with open(contact_db_path, "rb") as source:
                contact_salt = source.read(16).hex()
            contact_key = key_map.get(contact_salt)
            if contact_key:
                contact_tmp_path = decrypt_db(
                    contact_db_path, contact_key, temp_dir=message_temp_dir
                )
                tmp_paths.append(contact_tmp_path)
                contact_connection = sqlite3.connect(contact_tmp_path)
        print(f"✅ {len(connections)} 个消息分库全部解密成功\n")
    except Exception as e:
        print(f"❌ 解密失败：{e}")
        cleanup_message_shards()
        input("按回车键关闭...")
        return

    # ── 列出群聊 ──
    try:
        chatrooms = list_chatrooms(connections)
    except Exception as e:
        print(f"❌ 查询群聊失败：{e}")
        cleanup_message_shards()
        input("按回车键关闭...")
        return

    if not chatrooms:
        print("❌ 未找到任何群聊。")
        cleanup_message_shards()
        input("按回车键关闭...")
        return

    contact_name_map = load_contact_name_map(contact_connection)
    print(f"找到 {len(chatrooms)} 个群聊（按消息数量排序）：")
    print()
    display_count = min(20, len(chatrooms))
    for i, (cr, cnt) in enumerate(chatrooms[:display_count]):
        display_name = contact_name_map.get(cr, cr.replace('@chatroom', ''))
        print(f"  [{i+1:2d}] {display_name}  （{cnt} 条历史消息）")
    if len(chatrooms) > display_count:
        print(f"  ... 还有 {len(chatrooms)-display_count} 个群（消息较少）")

    print()
    choice = input("请输入要总结的群聊编号: ").strip()
    if not choice.isdigit() or int(choice) < 1 or int(choice) > len(chatrooms):
        print("输入无效，退出。")
        cleanup_message_shards()
        return

    selected_cr = chatrooms[int(choice) - 1][0]
    group_name = contact_name_map.get(
        selected_cr, selected_cr.replace("@chatroom", "")
    )

    # ── 选时间范围 ──
    print()
    print("总结哪段时间的消息？")
    print("  [1] 今天")
    print("  [2] 最近3天")
    print("  [3] 最近7天")
    day_choice = input("请选择（默认1）: ").strip()
    days_map = {"1": 1, "2": 3, "3": 7}
    days = days_map.get(day_choice, 1)

    # ── 获取消息 ──
    print(f"\n正在读取群「{group_name}」最近{days}天的消息...")
    messages = get_messages(
        connections,
        selected_cr,
        days=days,
        sender_name_map=contact_name_map,
    )

    cleanup_message_shards()

    if not messages:
        print(f"该时间段内没有文本消息（共0条）。")
        input("按回车键关闭...")
        return

    print(f"共找到 {len(messages)} 条文本消息，正在 AI 总结...\n")

    # ── AI 总结 ──
    try:
        summary = ai_summarize(
            messages,
            api_key,
            group_id=selected_cr,
            days=days,
            provider=provider,
            model=model,
        )
    except Exception as exc:
        print(f"❌ {exc}")
        input("按回车键关闭...")
        return

    print("=" * 60)
    print(f"  群「{group_name}」AI 总结")
    print(f"  时间：{datetime.datetime.now().strftime('%Y-%m-%d')}，最近{days}天")
    print("=" * 60)
    print(summary)
    print("=" * 60)

    # 保存到文件
    date_str = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    out_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"总结_{group_name}_{date_str}.txt"
    )
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(f"群聊：{group_name}\n")
        f.write(f"时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"消息数：{len(messages)} 条\n")
        f.write("=" * 60 + "\n")
        f.write(summary)

    print(f"\n✅ 总结已保存到：{out_file}")
    print()
    input("按回车键关闭...")


if __name__ == "__main__":
    main()
