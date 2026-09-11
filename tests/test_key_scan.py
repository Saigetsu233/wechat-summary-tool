# -*- coding: utf-8 -*-
"""校验平台无关的密钥扫描链路（reader 接口 + 明文/Config.Cipher 解析）。

用假内存 reader 驱动，因此这套逻辑在任何平台都能测——macOS 后端只
是换了 reader 的实现，扫描与校验逻辑与此完全一致。
"""

import hashlib
import hmac
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wechat_summary as w


def _forge_page1(key: bytes, salt: bytes) -> bytes:
    """构造一个能被 verify_enc_key 通过的数据库首页。"""
    body_len = w.PAGE_SZ - w.SALT_SZ - 64  # salt + body + hmac(64)
    body = bytes((i * 7) & 0xFF for i in range(body_len))
    page = salt + body
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", key, mac_salt, 2, dklen=w.KEY_SZ)
    hm = hmac.new(mac_key, page[w.SALT_SZ:], hashlib.sha512)
    hm.update(struct.pack("<I", 1))
    return page + hm.digest()


class _FakeReader:
    """把一段字节当作从 base 起的连续内存区域。"""

    def __init__(self, blob, base=0x100000):
        self.blob = blob
        self.base = base

    def regions(self):
        return [(self.base, len(self.blob))]

    def read(self, address, size):
        start = address - self.base
        if start < 0 or start >= len(self.blob):
            return None
        return self.blob[start:start + size]

    def close(self):
        pass


class KeyScanTests(unittest.TestCase):
    def setUp(self):
        self.key = bytes((i * 3 + 11) & 0xFF for i in range(w.KEY_SZ))
        self.salt = bytes((i * 5 + 1) & 0xFF for i in range(w.SALT_SZ))
        self.page1 = _forge_page1(self.key, self.salt)
        self.db_files = [("message/message_0.db", "x", w.PAGE_SZ,
                          self.salt.hex(), self.page1)]

    def test_verify_enc_key_accepts_forged_page(self):
        self.assertTrue(w.verify_enc_key(self.key, self.page1))
        self.assertFalse(w.verify_enc_key(b"\x00" * w.KEY_SZ, self.page1))

    def test_legacy_scan_finds_plaintext_key(self):
        # 微信 4.0 明文形式：x'<key><salt>'
        literal = b"x'" + (self.key.hex() + self.salt.hex()).encode() + b"'"
        blob = b"\x00" * 128 + literal + b"\x11" * 256
        key_map, remaining = {}, {self.salt.hex()}
        found = w._scan_legacy_key_reader(
            _FakeReader(blob), self.db_files, key_map, remaining
        )
        self.assertEqual(found, 1)
        self.assertEqual(key_map[self.salt.hex()], self.key.hex())
        self.assertEqual(remaining, set())

    def test_legacy_scan_ignores_wrong_key(self):
        wrong = bytes((i + 200) & 0xFF for i in range(w.KEY_SZ))
        literal = b"x'" + (wrong.hex() + self.salt.hex()).encode() + b"'"
        key_map, remaining = {}, {self.salt.hex()}
        found = w._scan_legacy_key_reader(
            _FakeReader(literal), self.db_files, key_map, remaining
        )
        self.assertEqual(found, 0)
        self.assertEqual(remaining, {self.salt.hex()})

    def test_iter_reader_chunks_reassembles_with_overlap(self):
        blob = bytes(range(256)) * 8
        reader = _FakeReader(blob, base=0x2000)
        collected = bytearray()
        seen_base = None
        for base, data in w._iter_reader_chunks(
            reader, reader.regions(), chunk_size=500, overlap=16
        ):
            if seen_base is None:
                seen_base = base
                collected.extend(data)
            else:
                collected.extend(data[16:])  # 去掉与上一块重叠的部分
        self.assertEqual(seen_base, 0x2000)
        self.assertEqual(bytes(collected), blob)


if __name__ == "__main__":
    unittest.main()
