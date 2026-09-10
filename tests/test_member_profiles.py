# -*- coding: utf-8 -*-
"""群昵称优先和人物形象约束的回归测试。"""

import sqlite3
import unittest
from unittest import mock

from wechat_summary import (
    ai_newspaper_digest,
    load_contact_gender_map,
    load_group_member_name_map,
)
from topic_image_generator import build_full_poster_prompt, build_single_scene_prompt


class GroupNicknameTests(unittest.TestCase):
    def test_group_member_nickname_overrides_contact_remark(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            """
            CREATE TABLE contact (id INTEGER, username TEXT, remark TEXT, nick_name TEXT, sex INTEGER);
            CREATE TABLE chat_room (id INTEGER, username TEXT);
            CREATE TABLE chatroom_member (
                room_id INTEGER, member_id INTEGER, group_nickname TEXT
            );
            INSERT INTO contact VALUES (7, 'wxid_alice', '小红的微信备注', 'Alice', 2);
            INSERT INTO chat_room VALUES (3, 'ski@chatroom');
            INSERT INTO chatroom_member VALUES (3, 7, '雪场小红');
            """
        )

        self.assertEqual(
            load_group_member_name_map(conn),
            {"ski@chatroom": {"wxid_alice": "雪场小红"}},
        )
        self.assertEqual(load_contact_gender_map(conn), {"wxid_alice": "female"})


class PortraitGenderTests(unittest.TestCase):
    @mock.patch("wechat_summary._chat_completion")
    def test_explicit_gender_is_forwarded_to_digest_and_poster(self, completion):
        completion.return_value = """{
          "headline": "今日重点",
          "mvp_rankings": [
            {"name": "雪场小红", "title": "装备参谋", "reason": "认真答疑"}
          ]
        }"""
        digest = ai_newspaper_digest(
            "[10:00] 雪场小红：这块板适合初学者。",
            "test-key",
            "滑雪群",
            "2026-09-10",
            1,
            member_genders={"雪场小红": "female"},
        )

        self.assertEqual(digest["mvp_rankings"][0]["gender"], "female")
        self.assertIn("雪场小红：女性", completion.call_args.args[1])
        self.assertIn("明确画成女性", build_full_poster_prompt(digest))
        self.assertIn(
            "clearly as female",
            build_single_scene_prompt(
                {"gender": "female", "visual_prompt": "winner portrait"}, "rank"
            ),
        )


if __name__ == "__main__":
    unittest.main()
