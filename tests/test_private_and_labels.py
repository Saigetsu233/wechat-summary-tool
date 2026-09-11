# -*- coding: utf-8 -*-
"""私聊列表、会话表名与日报措辞的回归测试。"""

import hashlib
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wechat_summary as w


def _msg_table(username):
    return "Msg_" + hashlib.md5(username.encode()).hexdigest()


class ScopeLabelTests(unittest.TestCase):
    def test_single_day_group(self):
        labels = w.build_scope_labels(1, "group")
        self.assertEqual(labels["count_label"], "今日群聊总结")
        self.assertEqual(labels["title_label"], "群聊日报")
        self.assertEqual(labels["overview_label"], "群聊概览")

    def test_multi_day_group(self):
        self.assertEqual(w.build_scope_labels(5, "group")["count_label"], "近 5 日群聊总结")

    def test_private_labels(self):
        labels = w.build_scope_labels(3, "private")
        self.assertEqual(labels["count_label"], "近 3 日聊天总结")
        self.assertEqual(labels["title_label"], "聊天日报")

    def test_conversation_table_matches_group_helper(self):
        # 群聊 id 自带 @chatroom：两个函数必须算出同一张表，保证不破坏 Windows。
        gid = "abc123@chatroom"
        self.assertEqual(w.conversation_table(gid), w.get_chatroom_table(gid))

    def test_conversation_table_private_no_suffix(self):
        # 私聊 wxid 不应被补 @chatroom。
        self.assertEqual(w.conversation_table("wxid_bob"), _msg_table("wxid_bob"))
        self.assertNotEqual(
            w.conversation_table("wxid_bob"), _msg_table("wxid_bob@chatroom")
        )


class PrivateChatListTests(unittest.TestCase):
    def _make_db(self):
        conn = sqlite3.connect(":memory:")
        peer, group, official, system = (
            "wxid_friend", "room1@chatroom", "gh_news", "filehelper",
        )
        conn.execute("CREATE TABLE Name2Id (user_name TEXT)")
        for name in (peer, group, official, system):
            conn.execute("INSERT INTO Name2Id VALUES (?)", (name,))
        # 只给私聊和公众号建消息表；系统账号没有表。
        for name, rows in ((peer, 12), (official, 3)):
            table = _msg_table(name)
            conn.execute(f"CREATE TABLE {table} (create_time INTEGER)")
            conn.executemany(
                f"INSERT INTO {table} VALUES (?)", [(i,) for i in range(rows)]
            )
        conn.commit()
        return conn

    def test_lists_private_excludes_group_official_system(self):
        conn = self._make_db()
        result = dict(w.list_private_chats(conn))
        self.assertIn("wxid_friend", result)
        self.assertEqual(result["wxid_friend"], 12)
        self.assertNotIn("room1@chatroom", result)   # 群聊
        self.assertNotIn("gh_news", result)          # 公众号
        self.assertNotIn("filehelper", result)       # 系统账号


if __name__ == "__main__":
    unittest.main()
