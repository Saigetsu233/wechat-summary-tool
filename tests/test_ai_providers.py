import unittest
from unittest import mock

import requests

from wechat_summary import (
    _chat_completion,
    _deepseek_chat,
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

    @mock.patch("wechat_summary.requests.post")
    def test_nvidia_rate_limit_has_friendly_error(self, post):
        post.return_value = mock.Mock(status_code=429)
        with self.assertRaisesRegex(RuntimeError, "频率或额度限制"):
            _chat_completion("test-key", "test", provider="nvidia")

    @mock.patch("wechat_summary.time.sleep")
    @mock.patch("wechat_summary.requests.post")
    def test_nvidia_read_timeout_retries_once(self, post, sleep):
        response = mock.Mock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "choices": [{"message": {"content": "重试成功"}}]
        }
        post.side_effect = [requests.ReadTimeout("slow"), response]
        progress = []

        result = _chat_completion(
            "test-key",
            "test",
            provider="nvidia",
            retry_callback=progress.append,
        )

        self.assertEqual(result, "重试成功")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args.kwargs["timeout"], (20, 240))
        self.assertEqual(sleep.call_count, 1)
        self.assertTrue(any("自动重试" in message for message in progress))

    @mock.patch("wechat_summary.time.sleep")
    @mock.patch("wechat_summary.requests.post")
    def test_nvidia_repeated_timeout_has_friendly_error(self, post, sleep):
        post.side_effect = requests.ReadTimeout("slow")

        with self.assertRaisesRegex(RuntimeError, "不是 API Key 错误"):
            _chat_completion("test-key", "test", provider="nvidia")

        self.assertEqual(post.call_count, 2)
        self.assertEqual(sleep.call_count, 1)

    def test_provider_default_models(self):
        self.assertEqual(provider_default_model("deepseek"), "deepseek-chat")
        self.assertEqual(
            provider_default_model("nvidia"),
            "deepseek-ai/deepseek-v4-pro-0813",
        )

    @mock.patch("wechat_summary._chat_completion", return_value="NVIDIA 摘要")
    def test_ai_summarize_forwards_provider_and_model(self, chat):
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


if __name__ == "__main__":
    unittest.main()
