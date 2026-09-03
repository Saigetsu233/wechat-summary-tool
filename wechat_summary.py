# -*- coding: utf-8 -*-
"""
微信群聊 AI 总结工具 - 适用于微信 4.x (Windows)

使用前：
1. 打开微信 PC 版并登录
2. 双击运行此工具

依赖：pip install pycryptodome requests psutil
"""

import os
import sys
import ctypes
import ctypes.wintypes as wt
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

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

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

class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_uint64), ("AllocationBase", ctypes.c_uint64),
        ("AllocationProtect", wt.DWORD), ("_pad1", wt.DWORD),
        ("RegionSize", ctypes.c_uint64), ("State", wt.DWORD),
        ("Protect", wt.DWORD), ("Type", wt.DWORD), ("_pad2", wt.DWORD),
    ]


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


def get_weixin_pids():
    """获取所有 Weixin.exe 进程 PID，按内存占用降序排列"""
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


def _read_process_memory(kernel32, process_handle, address, size):
    """读取一小段进程内存；读取失败时返回 None。"""
    if size <= 0:
        return b""
    try:
        buf = ctypes.create_string_buffer(size)
    except (MemoryError, OverflowError):
        return None
    nread = ctypes.c_size_t(0)
    ok = kernel32.ReadProcessMemory(
        process_handle,
        ctypes.c_uint64(address),
        buf,
        size,
        ctypes.byref(nread),
    )
    if not ok and nread.value == 0:
        return None
    return buf.raw[:nread.value]


def _enumerate_readable_regions(kernel32, process_handle):
    """枚举目标进程中可读、已提交的内存区域。"""
    regions = []
    address = 0
    mbi = MBI()
    while address < 0x7FFFFFFFFFFF:
        queried = kernel32.VirtualQueryEx(
            process_handle,
            ctypes.c_uint64(address),
            ctypes.byref(mbi),
            ctypes.sizeof(mbi),
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


def _iter_process_chunks(kernel32, process_handle, regions,
                         chunk_size=2 * 1024 * 1024, overlap=0):
    """分块读取内存，避免为较大的内存区域一次性分配巨型缓冲区。"""
    for base, region_size in regions:
        offset = 0
        tail = b""
        while offset < region_size:
            current_size = min(chunk_size, region_size - offset)
            chunk = _read_process_memory(
                kernel32, process_handle, base + offset, current_size
            ) or b""
            data = tail + chunk
            data_base = base + offset - len(tail)
            if data:
                yield data_base, data
            tail = data[-overlap:] if overlap and data else b""
            offset += current_size


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


def _scan_config_cipher_process(kernel32, pid, db_files,
                                key_map, remaining_salts):
    """微信 4.1.10+：只读扫描 WCDB Config.Cipher 对象。"""
    process_handle = kernel32.OpenProcess(0x0010 | 0x0400, False, pid)
    if not process_handle:
        return {"needles": 0, "nodes": 0, "candidates": 0, "verified": 0}

    stats = {"needles": 0, "nodes": 0, "candidates": 0, "verified": 0}
    try:
        regions = _enumerate_readable_regions(kernel32, process_handle)
        needle_addresses = set()
        for data_base, data in _iter_process_chunks(
            kernel32,
            process_handle,
            regions,
            overlap=len(CONFIG_CIPHER_NAME) - 1,
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

        for data_base, data in _iter_process_chunks(
            kernel32, process_handle, regions, overlap=0x80
        ):
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

                    node = _read_process_memory(
                        kernel32, process_handle, node_base, 0x50
                    )
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

                    config_object = _read_process_memory(
                        kernel32, process_handle, config_pointer + 0x88, 0x28
                    )
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

                    blob = _read_process_memory(
                        kernel32, process_handle, blob_pointer, int(blob_size)
                    )
                    for key_hex, salt_hex in _config_cipher_candidates(blob):
                        candidate = (key_hex, salt_hex)
                        if candidate in seen_candidates:
                            continue
                        seen_candidates.add(candidate)
                        stats["candidates"] += 1
                        stats["verified"] += _match_key_candidate(
                            key_hex,
                            salt_hex,
                            db_files,
                            key_map,
                            remaining_salts,
                        )
                    pos = data.find(pattern, pos + 1)
    finally:
        kernel32.CloseHandle(process_handle)
    return stats


def _scan_legacy_key_process(kernel32, pid, regions, db_files,
                             key_map, remaining_salts):
    """微信 4.0.x 兼容路径：扫描明文 x'<key><salt>'。"""
    process_handle = kernel32.OpenProcess(0x0010 | 0x0400, False, pid)
    if not process_handle:
        return 0
    hex_re = re.compile(rb"x'([0-9a-fA-F]{64,192})'")
    seen = set()
    found = 0
    try:
        for _base, data in _iter_process_chunks(
            kernel32, process_handle, regions, overlap=256
        ):
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
    finally:
        kernel32.CloseHandle(process_handle)
    return found


def extract_keys_from_memory(db_storage_dir):
    """从微信进程内存中自动提取数据库密钥，兼容微信 4.0/4.1。"""
    kernel32 = ctypes.windll.kernel32

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
        raise RuntimeError("未找到 Weixin.exe 进程，请先打开微信并登录！")

    key_map = {}  # salt_hex -> enc_key_hex
    remaining_salts = set(salt_to_dbs.keys())

    print("  尝试微信 4.1 Config.Cipher 只读扫描...")
    for pid, mem_kb in pids:
        if not remaining_salts:
            break
        stats = _scan_config_cipher_process(
            kernel32, pid, db_files, key_map, remaining_salts
        )
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
            process_handle = kernel32.OpenProcess(0x0010 | 0x0400, False, pid)
            if not process_handle:
                continue
            try:
                regions = _enumerate_readable_regions(kernel32, process_handle)
            finally:
                kernel32.CloseHandle(process_handle)
            found = _scan_legacy_key_process(
                kernel32, pid, regions, db_files, key_map, remaining_salts
            )
            print(f"  PID={pid}：旧版扫描验证通过 {found}")

    return key_map, db_files, salt_to_dbs


# ─────────────────────────────────────────────────────────────────────────────
# 3. 解密数据库
# ─────────────────────────────────────────────────────────────────────────────

def select_decrypt_temp_dir(required_bytes):
    """选择可容纳解密副本的临时目录，系统盘不足时自动改用程序所在盘。"""
    safety_margin = 128 * 1024 * 1024
    system_temp = tempfile.gettempdir()
    project_temp = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".wechat_summary_tmp"
    )
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


def decrypt_db(db_path, enc_key_hex, temp_dir=None):
    """流式解密微信4.x数据库，返回临时文件路径。"""
    from Crypto.Cipher import AES

    enc_key = bytes.fromhex(enc_key_hex)
    if temp_dir is None:
        temp_dir = select_decrypt_temp_dir(os.path.getsize(db_path))
    os.makedirs(temp_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix="wechat_summary_", suffix=".db", dir=temp_dir
    )
    os.close(fd)
    try:
        with open(db_path, "rb") as source, open(tmp_path, "wb") as target:
            first_page = source.read(PAGE_SZ)
            if first_page[:16] == SQLITE_HDR:
                target.write(first_page)
                while True:
                    chunk = source.read(4 * 1024 * 1024)
                    if not chunk:
                        break
                    target.write(chunk)
                return tmp_path

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
        return tmp_path
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
    table = get_chatroom_table(chatroom_id)
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
选出 3～6 个主要话题，使用“1️⃣ 话题名称”“2️⃣ 话题名称”的形式编号。
有剧情发展的事情按“起因、发展、群友讨论、当前结果”写清楚；没有完整故事线的话题自然概括，不要硬凑四个阶段。
每个话题可以搭配 1 个贴合内容的 emoji，并在合适的位置加入一句表情包式短评，例如“（这合理吗.jpg）”“（懂得都懂👀）”“主打一个稳中带皮😂”；不要机械套用示例。

👑 今日 MVP
直接写“昵称｜一句有节目效果的称号”，下一行说明当选原因。必须根据该昵称作为“发送者”的实际发言判断，不能因为他被别人频繁提到就把别人的话算到他头上。

🏆 趣味成就
给不同群友颁发 5～8 个搞笑成就，每项严格使用下面的纯文本形式：
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


SUMMARY_CHUNK_CHARS = 18000


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


def _deepseek_chat(api_key, prompt, max_tokens=2000):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "deepseek-chat",
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
    response = requests.post(
        "https://api.deepseek.com/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=90,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


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
                 progress_callback=None):
    """调用 DeepSeek；长记录自动分段提炼后再合并，不截断前文。"""
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
        return f"提示词模板格式错误，未知占位符：{e}\n请在「修改提示词」中检查模板。"

    try:
        chunks = _split_summary_chunks(messages)
        if len(chunks) == 1:
            _notify_summary_progress(progress_callback, "正在生成 AI 总结...")
            prompt = template.format(
                date=date_str,
                day_range=day_str,
                count=len(messages),
                messages=chunks[0],
            )
            return to_wechat_plain_text(_deepseek_chat(api_key, prompt))

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
                _deepseek_chat(api_key, partial_prompt, max_tokens=1400)
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
                    _deepseek_chat(api_key, reduce_prompt, max_tokens=1400)
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
        return to_wechat_plain_text(_deepseek_chat(api_key, final_prompt))
    except Exception as e:
        return f"AI总结失败：{e}"


# ─────────────────────────────────────────────────────────────────────────────
# 6. 配置管理
# ─────────────────────────────────────────────────────────────────────────────

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# 7. 主程序
# ─────────────────────────────────────────────────────────────────────────────

def install_deps():
    """自动安装依赖"""
    packages = ["pycryptodome", "requests", "psutil"]
    for pkg in packages:
        try:
            if pkg == "pycryptodome":
                from Crypto.Cipher import AES
            elif pkg == "requests":
                import requests
            elif pkg == "psutil":
                import psutil
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

    # ── 获取/确认 API Key ──
    DEFAULT_API_KEY = ""  # 请填入你自己的 DeepSeek API Key
    api_key = cfg.get("api_key", "") or DEFAULT_API_KEY

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
    summary = ai_summarize(messages, api_key, group_id=selected_cr, days=days)

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
