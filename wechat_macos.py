# -*- coding: utf-8 -*-
"""macOS 专用后端：进程发现、mach 进程内存读取、微信数据目录定位。

自动读取本机微信数据库需要读取微信进程内存。macOS 对开启 hardened
runtime 的已签名 App 默认禁止 task_for_pid，即使 root 也不行——必须
先关闭 SIP（csrutil disable）并以 sudo 运行本程序，task_for_pid 才会
放行。这不是代码能绕过的系统限制，界面里会向用户说明。
"""

import ctypes
import glob
import os
import subprocess
from pathlib import Path


# 供 wechat_summary 复用的异常类型（避免循环导入，运行时再取）。
def _memory_error():
    import wechat_summary
    return wechat_summary.MemoryAccessError


# ── mach VM 常量与函数原型 ────────────────────────────────────────────────
KERN_SUCCESS = 0
VM_PROT_READ = 0x1
VM_REGION_BASIC_INFO_64 = 9

try:
    # macOS 上 CDLL(None) 打开全局符号表；其他平台可能抛 TypeError/OSError，
    # 此时把 _libc 置空，让本模块仍可被导入以测试纯逻辑。
    _libc = ctypes.CDLL(None, use_errno=True)
except (OSError, TypeError):  # pragma: no cover - 非 macOS
    _libc = None


class _VMRegionBasicInfo64(ctypes.Structure):
    _fields_ = [
        ("protection", ctypes.c_int),
        ("max_protection", ctypes.c_int),
        ("inheritance", ctypes.c_uint),
        ("shared", ctypes.c_int),
        ("reserved", ctypes.c_int),
        ("offset", ctypes.c_ulonglong),
        ("behavior", ctypes.c_int),
        ("user_wired_count", ctypes.c_ushort),
    ]


_INFO_COUNT = ctypes.sizeof(_VMRegionBasicInfo64) // ctypes.sizeof(ctypes.c_int)


def _configure_prototypes():
    if _libc is None or not hasattr(_libc, "task_for_pid"):
        # 非 macOS 环境（或缺少 mach 符号）：留待实际调用时报错，
        # 让本模块在任何平台都能被导入以便测试纯逻辑。
        return
    _libc.task_for_pid.restype = ctypes.c_int
    _libc.task_for_pid.argtypes = [
        ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_uint),
    ]
    _libc.mach_vm_region.restype = ctypes.c_int
    _libc.mach_vm_region.argtypes = [
        ctypes.c_uint,
        ctypes.POINTER(ctypes.c_ulonglong),
        ctypes.POINTER(ctypes.c_ulonglong),
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint),
        ctypes.POINTER(ctypes.c_uint),
    ]
    _libc.mach_vm_read_overwrite.restype = ctypes.c_int
    _libc.mach_vm_read_overwrite.argtypes = [
        ctypes.c_uint,
        ctypes.c_ulonglong,
        ctypes.c_ulonglong,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulonglong),
    ]


_configure_prototypes()


def _mach_task_self():
    return ctypes.c_uint.in_dll(_libc, "mach_task_self_").value


class MacProcessReader:
    """通过 mach VM 接口读取目标进程内存。"""

    def __init__(self, pid):
        if _libc is None or not hasattr(_libc, "task_for_pid"):
            raise _memory_error()("当前系统不支持 mach 进程内存读取（仅 macOS）。")
        task = ctypes.c_uint(0)
        rc = _libc.task_for_pid(_mach_task_self(), int(pid), ctypes.byref(task))
        if rc != KERN_SUCCESS:
            raise _memory_error()(
                f"task_for_pid(pid={pid}) 失败（返回 {rc}）。"
                "读取微信进程内存需要先关闭 SIP 并以 sudo 运行本程序。"
            )
        self.task = task.value
        self.pid = pid

    def regions(self):
        regions = []
        address = ctypes.c_ulonglong(0)
        while True:
            size = ctypes.c_ulonglong(0)
            info = _VMRegionBasicInfo64()
            count = ctypes.c_uint(_INFO_COUNT)
            object_name = ctypes.c_uint(0)
            rc = _libc.mach_vm_region(
                self.task,
                ctypes.byref(address),
                ctypes.byref(size),
                VM_REGION_BASIC_INFO_64,
                ctypes.byref(info),
                ctypes.byref(count),
                ctypes.byref(object_name),
            )
            if rc != KERN_SUCCESS:
                break
            region_size = size.value
            if (
                info.protection & VM_PROT_READ
                and 0 < region_size < 500 * 1024 * 1024
            ):
                regions.append((address.value, region_size))
            next_address = address.value + region_size
            if next_address <= address.value:
                break
            address = ctypes.c_ulonglong(next_address)
        return regions

    def read(self, address, size):
        if size <= 0:
            return b""
        try:
            buf = ctypes.create_string_buffer(size)
        except (MemoryError, OverflowError):
            return None
        out_size = ctypes.c_ulonglong(0)
        rc = _libc.mach_vm_read_overwrite(
            self.task,
            ctypes.c_ulonglong(address),
            ctypes.c_ulonglong(size),
            ctypes.cast(buf, ctypes.c_void_p),
            ctypes.byref(out_size),
        )
        if rc != KERN_SUCCESS or out_size.value == 0:
            return None
        return buf.raw[:out_size.value]

    def close(self):
        self.task = None


# ── 进程发现 ─────────────────────────────────────────────────────────────
_PROCESS_NAMES = {"weixin", "wechat"}


def list_wechat_pids():
    """返回 [(pid, rss_kb), ...]，按占用降序。优先用 psutil，退回 ps。"""
    results = []
    try:
        import psutil
        for proc in psutil.process_iter(["name", "pid"]):
            name = (proc.info.get("name") or "").lower()
            if name in _PROCESS_NAMES:
                try:
                    rss_kb = proc.memory_info().rss // 1024
                except Exception:
                    rss_kb = 0
                results.append((proc.info["pid"], rss_kb))
    except Exception:
        results = _list_pids_via_ps()
    if not results:
        results = _list_pids_via_ps()
    results.sort(key=lambda item: item[1], reverse=True)
    return results


def _list_pids_via_ps():
    try:
        out = subprocess.run(
            ["ps", "-axo", "pid=,rss=,comm="],
            capture_output=True, text=True, check=False,
        ).stdout
    except OSError:
        return []
    results = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid_s, rss_s, comm = parts
        base = os.path.basename(comm).lower()
        if base in _PROCESS_NAMES:
            try:
                results.append((int(pid_s), int(rss_s)))
            except ValueError:
                continue
    return results


# ── 数据目录定位 ─────────────────────────────────────────────────────────
def _candidate_container_roots():
    home = Path.home()
    roots = [
        home / "Library" / "Containers",
        home / "Library" / "Group Containers",
        home / "Library" / "Application Support",
    ]
    return [str(r) for r in roots if r.is_dir()]


def find_wechat_data_dir():
    """在 macOS 常见容器目录下寻找含 db_storage/message/message_0.db 的账号目录。

    返回 (root_dir, user_dir)，其中 user_dir/db_storage 存在，
    与 Windows 版约定一致；找不到返回 (None, None)。
    """
    matches = []  # (mtime, user_dir)
    for root in _candidate_container_roots():
        # 只在腾讯相关容器里做有界搜索，避免全盘 walk。
        pattern = os.path.join(root, "*[Ww]e[Cc]hat*", "**", "db_storage",
                               "message", "message_0.db")
        for db_path in glob.glob(pattern, recursive=True):
            _record_match(db_path, matches)
        pattern2 = os.path.join(root, "*[Xx]in[Ww]e[Cc]hat*", "**",
                                "db_storage", "message", "message_0.db")
        for db_path in glob.glob(pattern2, recursive=True):
            _record_match(db_path, matches)
        pattern3 = os.path.join(root, "*[Ww]eixin*", "**", "db_storage",
                                "message", "message_0.db")
        for db_path in glob.glob(pattern3, recursive=True):
            _record_match(db_path, matches)
    if not matches:
        return None, None
    matches.sort(key=lambda item: item[0], reverse=True)
    user_dir = matches[0][1]
    return os.path.dirname(user_dir), user_dir


def _record_match(db_path, matches):
    # db_path = .../<user_dir>/db_storage/message/message_0.db → 上溯三层
    user_dir = os.path.dirname(os.path.dirname(os.path.dirname(db_path)))
    if os.path.isdir(user_dir):
        try:
            matches.append((os.path.getmtime(db_path), user_dir))
        except OSError:
            pass
