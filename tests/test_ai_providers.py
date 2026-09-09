import unittest
from unittest import mock
from pathlib import Path
import tempfile
import threading

import requests
from PIL import Image
import wechat_summary

from newspaper_renderer import CANVAS_SIZE, render_newspaper
from wechat_summary import (
    _chat_completion,
    _deepseek_chat,
    _split_summary_chunks,
    ai_newspaper_digest,
    ai_summarize,
    provider_default_model,
    to_wechat_plain_text,
)


class AIProviderTests(unittest.TestCase):
    @mock.patch("wechat_summary.requests.post")
    def test_deepseek_402_has_friendly_error(self, post):
        post.return_value = mock.Mock(status_code=402)
        with self.assertRaisesRegex(RuntimeError, "余额不足"):
            _deepseek_chat("test-key", "test")

    @mock.patch("wechat_summary.requests.post")
    def test_nvidia_uses_compatible_endpoint_and_selected_model(self, post):
        response = mock.Mock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "choices": [{"message": {"content": "NVIDIA 总结结果"}}]
        }
        post.return_value = response

        result = _chat_completion(
            "nvidia-test-key",
            "test",
            provider="nvidia",
            model="deepseek-ai/deepseek-v4-pro-0813",
        )

        self.assertEqual(result, "NVIDIA 总结结果")
        self.assertEqual(
            post.call_args.args[0],
            "https://integrate.api.nvidia.com/v1/chat/completions",
        )
        self.assertEqual(
            post.call_args.kwargs["json"]["model"],
            "deepseek-ai/deepseek-v4-pro-0813",
        )
        self.assertEqual(
            post.call_args.kwargs["headers"]["Authorization"],
            "Bearer nvidia-test-key",
        )
        self.assertEqual(
            post.call_args.kwargs["json"]["chat_template_kwargs"],
            {"thinking": False},
        )

    @mock.patch("wechat_summary.requests.post")
    def test_nvidia_rate_limit_has_friendly_error(self, post):
        post.return_value = mock.Mock(status_code=429)
        with self.assertRaisesRegex(RuntimeError, "频率或额度限制"):
            _chat_completion("test-key", "test", provider="nvidia")

    @mock.patch("wechat_summary.requests.post")
    def test_nvidia_timeout_stops_after_one_attempt(self, post):
        post.side_effect = requests.ReadTimeout("slow")

        with self.assertRaisesRegex(RuntimeError, "120 秒"):
            _chat_completion("test-key", "test", provider="nvidia")

        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs["timeout"], (20, 120))

    def test_large_chat_is_split_into_latency_safe_chunks(self):
        chunks = _split_summary_chunks(["甲" * 99000, "乙" * 900])
        self.assertEqual(len(chunks), 5)
        self.assertTrue(all(len(chunk) <= 24000 for chunk in chunks))

    @mock.patch("wechat_summary._chat_completion", return_value="快速总结")
    @mock.patch("wechat_summary._load_summary_cache", return_value=None)
    @mock.patch("wechat_summary._save_summary_cache")
    def test_1782_short_messages_are_split_before_final_summary(
            self, _save, _load, chat):
        messages = [
            f"[{index:04d}] 群友：今天聊点新鲜事，顺便讨论一个具体问题"
            for index in range(1782)
        ]
        chunks = _split_summary_chunks(messages)
        result = ai_summarize(messages, "test-key", provider="nvidia")
        self.assertEqual(result, "快速总结")
        self.assertGreater(len(chunks), 1)
        self.assertEqual(chat.call_count, len(chunks) + 1)

    def test_completed_summary_request_is_resumed_from_disk_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.object(
            wechat_summary, "SUMMARY_CACHE_DIR", temp_dir
        ), mock.patch(
            "wechat_summary._chat_completion", return_value="已完成的分段摘要"
        ) as chat:
            first = wechat_summary._cached_summary_completion(
                "test-key", "同一段聊天", provider="nvidia"
            )
            second = wechat_summary._cached_summary_completion(
                "test-key", "同一段聊天", provider="nvidia"
            )

        self.assertEqual(first, "已完成的分段摘要")
        self.assertEqual(second, first)
        self.assertEqual(chat.call_count, 1)

    @mock.patch("wechat_summary.requests.post")
    def test_cancelled_request_does_not_reach_network(self, post):
        cancel_event = threading.Event()
        cancel_event.set()
        with self.assertRaisesRegex(RuntimeError, "任务已取消"):
            _chat_completion(
                "test-key", "test", provider="nvidia", cancel_event=cancel_event
            )
        post.assert_not_called()

    def test_provider_default_models(self):
        self.assertEqual(provider_default_model("deepseek"), "deepseek-chat")
        self.assertEqual(
            provider_default_model("nvidia"),
            "deepseek-ai/deepseek-v4-flash-0731",
        )

    @mock.patch("wechat_summary._chat_completion", return_value="NVIDIA 摘要")
    @mock.patch("wechat_summary._load_summary_cache", return_value=None)
    @mock.patch("wechat_summary._save_summary_cache")
    def test_ai_summarize_forwards_provider_and_model(
            self, _save, _load, chat):
        result = ai_summarize(
            ["[12:00] 群友1：今天去滑雪"],
            "test-key",
            provider="nvidia",
            model="custom/model-name",
        )

        self.assertEqual(result, "NVIDIA 摘要")
        self.assertEqual(chat.call_args.kwargs["provider"], "nvidia")
        self.assertEqual(chat.call_args.kwargs["model"], "custom/model-name")

    @mock.patch(
        "wechat_summary._chat_completion",
        side_effect=RuntimeError("NVIDIA 等待超时"),
    )
    def test_ai_summarize_raises_instead_of_reporting_completion(self, _chat):
        with self.assertRaisesRegex(RuntimeError, "AI总结失败"):
            ai_summarize(
                ["[12:00] 群友1：今天去滑雪"],
                "test-key",
                provider="nvidia",
            )

    def test_markdown_is_cleaned_for_wechat(self):
        value = "# 标题\n\n**内容**\n\n| 成就 | 人物 |\n| --- | --- |\n| MVP | 小明 |"
        result = to_wechat_plain_text(value)
        self.assertNotIn("#", result)
        self.assertNotIn("**", result)
        self.assertNotIn("| ---", result)
        self.assertIn("1、MVP｜小明", result)

    @mock.patch("wechat_summary._chat_completion")
    def test_newspaper_digest_parses_and_limits_content(self, chat):
        chat.return_value = """```json
        {
          "headline": "雪季未至装备先卷起来",
          "lead": "群友今天主要讨论了雪板选购与周末行程。",
          "topics": [
            {"title": "雪板选购", "summary": "大家比较了三款雪板。"},
            {"title": "周末行程", "summary": "初步决定周六出发。"},
            {"title": "装备保养", "summary": "群友分享了打蜡经验。"},
            {"title": "第四话题", "summary": "日报现在支持更多重点。"},
            {"title": "第五话题", "summary": "日报现在支持更多重点。"},
            {"title": "第六话题", "summary": "日报现在支持更多重点。"},
            {"title": "第七话题", "summary": "日报现在支持更多重点。"},
            {"title": "第八话题", "summary": "日报现在支持更多重点。"},
            {"title": "第九话题", "summary": "日报现在支持更多重点。"},
            {"title": "第十话题", "summary": "日报现在支持更多重点。"},
            {"title": "多余话题", "summary": "第十一条应被裁掉。"}
          ],
          "mvp_rankings": [
            {"name": "阿雪", "title": "装备参谋", "reason": "整理了对比数据。"},
            {"name": "小明", "title": "行程管家", "reason": "确定了集合时间。"},
            {"name": "小林", "title": "打蜡大师", "reason": "分享了保养经验。"}
          ],
          "achievements": [
            {"award": "种草王", "name": "小明", "reason": "连发三个链接。"}
          ],
          "quotes": [{"speaker": "阿雪", "text": "人可以不快，装备要帅。"}],
          "tomorrow_topics": ["继续确认周末天气"],
          "special_notes": ["出发时间仍需群内确认"]
        }
        ```"""

        digest = ai_newspaper_digest(
            "今日聊了雪板与行程。",
            "test-key",
            "滑雪群",
            "2026-09-09 至 2026-09-09",
            564,
            provider="nvidia",
        )

        self.assertEqual(digest["group_name"], "滑雪群")
        self.assertEqual(digest["message_count"], "564")
        self.assertEqual(len(digest["topics"]), 6)
        self.assertEqual(digest["mvp"]["name"], "阿雪")
        self.assertEqual(len(digest["mvp_rankings"]), 3)
        self.assertEqual(digest["quotes"][0]["speaker"], "阿雪")
        self.assertEqual(digest["tomorrow_topics"], ["继续确认周末天气"])
        self.assertEqual(chat.call_args.kwargs["provider"], "nvidia")

    def test_newspaper_renderer_creates_single_page_png(self):
        digest = {
            "date": "2026-09-09",
            "group_name": "滑雪营销号病友群",
            "message_count": "564",
            "headline": "雪季未至，装备先卷起来",
            "lead": "群友围绕雪板、行程和装备保养展开讨论。",
            "topics": [
                {"title": "雪板选购", "summary": "比较三款板子后给出了具体建议。"},
                {"title": "周末行程", "summary": "初步定下周六早上出发。"},
                {"title": "打蜡时间", "summary": "大家分享了雪板保养经验。"},
            ],
            "mvp": {"name": "阿雪", "title": "装备参谋", "reason": "整理了关键参数。"},
            "achievements": [
                {"award": "种草王", "name": "小明", "reason": "分享了实用装备。"}
            ],
            "quote": {"speaker": "阿雪", "text": "人可以不快，装备要帅。"},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "daily.png"
            render_newspaper(digest, output)
            self.assertTrue(output.is_file())
            with Image.open(output) as rendered:
                self.assertEqual(rendered.size, CANVAS_SIZE)
                self.assertEqual(rendered.mode, "RGB")


if __name__ == "__main__":
    unittest.main()
