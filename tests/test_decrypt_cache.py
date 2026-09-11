# -*- coding: utf-8 -*-
"""解密临时副本的复用与清理：不再每次新建、不堆满磁盘。"""

import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wechat_summary as w


def _plaintext_db(path, payload=b"x" * 8192):
    # 首 16 字节是 SQLite 头，decrypt_db 会走“已是明文直接复制”的快路径。
    path.write_bytes(w.SQLITE_HDR + payload)


class DecryptCacheTests(unittest.TestCase):
    def test_deterministic_name_and_reuse(self):
        with TemporaryDirectory() as d:
            d = Path(d)
            src = d / "message_0.db"
            _plaintext_db(src)
            tmp = d / "cache"
            tmp.mkdir()

            first = w.decrypt_db(str(src), "aa" * 32, temp_dir=str(tmp))
            second = w.decrypt_db(str(src), "aa" * 32, temp_dir=str(tmp))
            # 同一源 → 同一个固定文件名，且第二次直接复用。
            self.assertEqual(first, second)
            self.assertTrue(os.path.exists(first))
            # 目录里只有这一个解密副本，没有随机新建。
            copies = [n for n in os.listdir(tmp)
                      if n.startswith(w.DECRYPT_PREFIX) and n.endswith(".db")]
            self.assertEqual(len(copies), 1)

    def test_stale_copy_is_refreshed(self):
        with TemporaryDirectory() as d:
            d = Path(d)
            src = d / "message_0.db"
            _plaintext_db(src, b"old")
            tmp = d / "cache"
            tmp.mkdir()
            dest = w.decrypt_db(str(src), "aa" * 32, temp_dir=str(tmp))
            # 源更新（新消息）：把源改大且 mtime 更新，应重新解密覆盖。
            time.sleep(0.02)
            _plaintext_db(src, b"new-and-longer-content")
            os.utime(src, None)
            refreshed = w.decrypt_db(str(src), "aa" * 32, temp_dir=str(tmp))
            self.assertEqual(dest, refreshed)
            self.assertEqual(
                os.path.getsize(refreshed), os.path.getsize(src)
            )

    def test_sweep_keeps_only_current(self):
        with TemporaryDirectory() as d:
            tmp = Path(d)
            keep = tmp / f"{w.DECRYPT_PREFIX}keepme.db"
            stray1 = tmp / f"{w.DECRYPT_PREFIX}old_random.db"
            stray2 = tmp / f"{w.DECRYPT_PREFIX}another.db"
            other = tmp / "unrelated.txt"
            for p in (keep, stray1, stray2, other):
                p.write_bytes(b"x")
            w.sweep_decrypt_temp_dir(str(tmp), keep_paths=[str(keep)])
            self.assertTrue(keep.exists())
            self.assertFalse(stray1.exists())
            self.assertFalse(stray2.exists())
            self.assertTrue(other.exists())  # 非本工具文件不动


if __name__ == "__main__":
    unittest.main()
